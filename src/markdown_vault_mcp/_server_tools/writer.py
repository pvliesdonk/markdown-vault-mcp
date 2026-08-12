from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import socket
from dataclasses import asdict
from datetime import date
from typing import Any
from urllib.parse import urlparse, urlunparse

from fastmcp import Context, FastMCP
from fastmcp.dependencies import CurrentContext, Depends
from fastmcp.exceptions import ToolError
from fastmcp.server.elicitation import AcceptedElicitation
from mcp.shared.exceptions import McpError

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    EditConflictError,
)
from markdown_vault_mcp.okf import _HUMAN_ACTOR_PREFIX, append_okf_verification
from markdown_vault_mcp.utils.text import decode_utf8
from markdown_vault_mcp.vault import Vault

from .._icons import _TOOL_ICONS
from .._okf_write import (
    OkfWriteIntent,
    okf_write_intent,
    okf_write_suppressed,
    resolve_human_subject,
    resolve_verify_subject,
    resolve_write_actor,
)
from ..domain import get_config, get_vault
from ._common import attach_conventions

logger = logging.getLogger(__name__)


_ALLOWED_FETCH_SCHEMES = frozenset({"http", "https"})

# SSRF protection: block private/reserved IP ranges.
_FETCH_BLOCKED_HOSTNAMES = frozenset(
    {"localhost", "localhost.localdomain", "metadata.google.internal"}
)


_BLOCKED_ADDR_MSG = (
    "URLs targeting private, loopback, link-local, or reserved addresses "
    "are not allowed."
)

# Default ports per scheme — omitted from the Host header (RFC 7230 §5.4:
# a client SHOULD NOT send a port that equals the scheme default).
_SCHEME_DEFAULT_PORTS = {"http": 80, "https": 443}


def _ip_is_blocked(ip: str) -> bool:
    """Return True if *ip* (an IP literal) is in a non-public range.

    Covers private, loopback, link-local, unspecified (``0.0.0.0`` — which
    ``is_private`` misses on older Pythons), reserved, and multicast space,
    for both IPv4 and IPv6.
    """
    addr = ipaddress.ip_address(ip)
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_unspecified
        or addr.is_reserved
        or addr.is_multicast
    )


async def _resolve_pinned_ip(hostname: str, port: int) -> str:
    """Resolve *hostname* and return one validated public IP to pin to.

    SSRF guard that also closes the DNS-rebinding (TOCTOU) window: the caller
    connects to the returned IP literal rather than the hostname, so the
    address validated here is the address actually connected to — there is no
    second resolution at connect time for an attacker's DNS to swap.

    Fails **closed**: raises :class:`ValueError` if resolution errors, returns
    no records, or **any** resolved address is non-public. An IP-literal
    *hostname* is validated directly without a DNS lookup.

    Args:
        hostname: The URL host (an IP literal or a domain name).
        port: The target port, used for the ``getaddrinfo`` service hint.

    Returns:
        A validated, public IP literal to connect to.

    Raises:
        ValueError: If the host is blocklisted, resolves to a non-public
            address, or cannot be resolved.
    """
    if hostname in _FETCH_BLOCKED_HOSTNAMES:
        raise ValueError(_BLOCKED_ADDR_MSG)

    # IP-literal fast path: validate directly, no DNS lookup.
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if _ip_is_blocked(hostname):
            raise ValueError(_BLOCKED_ADDR_MSG)
        return hostname

    # Domain name: resolve and validate every returned address. Fail closed on
    # any resolution error so a DNS hiccup can never become an SSRF bypass.
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:  # socket.gaierror is an OSError subclass
        raise ValueError(
            f"Could not resolve host {hostname!r} for the fetch URL: {exc}"
        ) from exc

    resolved = [str(info[4][0]) for info in infos]
    if not resolved:
        raise ValueError(f"Host {hostname!r} did not resolve to any address.")
    for ip in resolved:
        if _ip_is_blocked(ip):
            raise ValueError(
                f"Host {hostname!r} resolves to a non-public address ({ip}); "
                "refusing to fetch."
            )
    return resolved[0]


async def _require_review_elicitation(ctx: Context, path: str) -> None:
    """Gate okf_verify's ``elicit`` mode on an affirmative human elicitation.

    Fails **closed**: raises :class:`ToolError` (writing nothing) when the client
    cannot elicit, or the human declines, cancels, or answers negatively. A model
    cannot answer an elicitation, so the attestation leaves its control by
    construction — a headless agent (no human) can never confirm.

    Args:
        ctx: The FastMCP request context used to issue the elicitation.
        path: The note being attested (surfaced in the prompt).

    Raises:
        ToolError: If elicitation is unsupported or the review is not confirmed.
    """
    message = (
        f"Confirm you have personally reviewed {path!r} and want to attest it "
        "as human-reviewed. This records a verification in the note's OKF "
        "frontmatter and promotes its trust tier."
    )
    try:
        # A scalar bool wraps into a single-field object schema; an affirmative
        # reply deconstructs back to True. mypy resolves only elicit's first
        # (``response_type: None``) overload — its PEP 695/696 ``T = Any``-default
        # typed overloads are opaque to it — so it mistypes the arg and
        # ``result.data`` (a bool at runtime). Narrow ignore for that upstream
        # typing gap; the truthiness gate below is correct at runtime.
        result = await ctx.elicit(message, response_type=bool)  # type: ignore[arg-type]
    except McpError as exc:
        raise ToolError(
            "okf_verify is in 'elicit' mode but this client does not support "
            "elicitation, so a human review cannot be confirmed and nothing was "
            "written. Use an elicitation-capable client, or set "
            "MARKDOWN_VAULT_MCP_OKF_VERIFY=trust-auth (attribute to the "
            "authenticated caller) or off (hide the tool)."
        ) from exc
    if isinstance(result, AcceptedElicitation) and result.data:
        return
    raise ToolError("Human review was not confirmed, so no verification was written.")


async def _resolve_verify_mode_subject(*, mode: str, ctx: Context, path: str) -> str:
    """Resolve the ``human:`` subject to stamp, enforcing *mode*'s gate.

    ``trust-auth`` requires an authenticated subject (refuses under auth mode
    ``none``); any other mode is treated as ``elicit`` and requires an
    affirmative elicitation before attributing to the authenticated subject (or
    the local sentinel). ``off`` never reaches here — the server hides the tool.

    Raises:
        ToolError: If the mode's gate is not satisfied.
    """
    if mode == "trust-auth":
        subject = resolve_human_subject()
        if subject is None:
            raise ToolError(
                "okf_verify (trust-auth mode) requires an authenticated "
                "identity; the server is running with no auth, so a "
                "verification cannot be attributed."
            )
        return subject
    await _require_review_elicitation(ctx, path)
    return resolve_verify_subject()


def register(mcp: FastMCP) -> None:
    """Register write/mutation tools on *mcp*."""

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["write"],
        annotations={
            "title": "Write Note",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def write(
        path: str,
        content: str = "",
        frontmatter: dict[str, Any] | None = None,
        content_base64: str = "",
        if_match: str | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Create or overwrite a document or attachment.

        For .md documents: uses 'content' (markdown body) and optional
        'frontmatter'. WARNING: replaces the entire file — use 'edit'
        for targeted changes. The search index is updated immediately;
        do not call 'reindex' afterward.

        For attachments (pdf, png, etc.): uses 'content_base64' (base64-
        encoded binary). 'content' and 'frontmatter' are ignored.
        Parent directories are created automatically for both.

        Args:
            path: Relative path (e.g. "Journal/note.md" or
                "assets/photo.png"). Extension determines handling.
            content: Full markdown body for .md files (excluding
                frontmatter). Ignored for attachments.
            frontmatter: Optional YAML frontmatter dict for .md files,
                e.g. {"title": "My Note", "tags": ["draft"]}.
                Ignored for attachments.
            content_base64: Base64-encoded binary content for attachment
                files. Required when path is not ``.md``.

                **Context cost:** base64 encoding inflates by ~33%; even a 1 MB
                attachment becomes ~1.3 MB of tokens.
            if_match: Optional etag obtained from a previous 'read' call.
                When provided, the write only proceeds if the file has not
                been modified since that read (optimistic concurrency).
                Omit to write unconditionally.

        Returns:
            Dict with path (str) and created (bool — true if new file,
            false if overwrite). For .md files, may include 'conventions':
            the user's authoring conventions for the target folder
            (root-first list of {folder, path, content}). When present,
            verify the note you just wrote complies — e.g. self-containment
            or linking-direction rules — and issue a follow-up 'edit' if it
            does not. To check conventions *before* writing, call
            'get_conventions(path)'.

        Supports split (write several new notes from one source) and merge
        (extend an existing note with content from another) when composed with
        ``read`` and ``delete``.

        Raises:
            ValueError: If content_base64 is missing/invalid for
                attachments, or the content exceeds
                ``MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB``.
            McpError: If if_match is provided and the file has been
                modified, or if_match is supplied for a file that does not
                yet exist (ConcurrentModificationError).
        """
        if not path.endswith(".md"):
            if not content_base64:
                raise ValueError(
                    f"content_base64 is required for non-.md attachments: {path}"
                )
            try:
                raw_bytes = base64.b64decode(content_base64)
            except Exception as exc:
                raise ValueError(f"Invalid base64 in content_base64: {exc}") from exc
            cap_mb = vault.max_attachment_size_mb
            if cap_mb > 0 and len(raw_bytes) > int(cap_mb * 1024 * 1024):
                raise ValueError(
                    f"Attachment {path!r} is {len(raw_bytes)} bytes "
                    f"({len(raw_bytes) / 1024 / 1024:.1f} MB), exceeds "
                    f"MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB ({cap_mb} MB). "
                    f"Increase MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB if "
                    f"you need the bytes in context."
                )
            result = await asyncio.to_thread(
                vault.writer.write_attachment, path, raw_bytes, if_match=if_match
            )
            return asdict(result)
        # Bind the OKF provenance actor for the enforced-write layer (#964).
        # A no-op unless OKF_WRITE is on; the intent rides the contextvar into
        # the to_thread worker where the enricher runs.
        with okf_write_intent(OkfWriteIntent(actor=resolve_write_actor())):
            result = await asyncio.to_thread(
                vault.writer.write,
                path,
                content,
                frontmatter=frontmatter,
                if_match=if_match,
            )
        return await attach_conventions(vault, asdict(result), path)

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["edit"],
        annotations={
            "title": "Edit Note",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
    )
    async def edit(
        path: str,
        old_text: str | None = None,
        new_text: str = "",
        if_match: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Make a targeted text replacement in an existing .md note (not supported for attachments).

        Three edit modes:
        - **Exact match** (old_text only): pass a portion of the file as
          old_text — must appear exactly once. Frontmatter can be edited.
        - **Line-range** (line_start + line_end, no old_text): replace the
          specified lines with new_text. Lines are 1-based (matching
          'read' output). Recommended: pass if_match for safety.
        - **Scoped match** (old_text + line_start/line_end): search for
          old_text within the line range only — useful when old_text
          appears multiple times in the file.

        When exact match fails, a normalized comparison is attempted
        (Unicode NFC, dash/quote normalization, whitespace collapsing).
        If a unique normalized match is found, it is used and
        match_type='normalized' is returned.

        Always call 'read' first to get the current text and line numbers.
        The search index is updated immediately; do not call 'reindex'.

        Args:
            path: Relative path to the document.
            old_text: Text to replace. Must appear exactly once in the
                document or line range. Get this via 'read'. Optional
                when using line-range mode.
            new_text: Replacement text. May be longer or shorter.
            if_match: Optional etag obtained from a previous 'read' call.
                When provided, the edit only proceeds if the file has not
                been modified since that read (optimistic concurrency).
            line_start: First line to replace (1-based, inclusive).
                Must be provided together with line_end.
            line_end: Last line to replace (1-based, inclusive).
                Must be provided together with line_start.

        Returns:
            - **path** (str): path of the edited document.
            - **replacements** (int): always 1.
            - **match_type** (str): ``'exact'`` or ``'normalized'``.
            - **conventions** (list, optional): the user's authoring
              conventions for the note's folder (root-first list of
              {folder, path, content}). When present, verify the edited
              note complies and issue a follow-up 'edit' if it does not.

        Raises:
            ValueError: If parameter combination is invalid, or line
                numbers are out of range.
            EditConflictError: If old_text is not found or appears more
                than once.
            DocumentNotFoundError: If no file exists at the given path.
            McpError: If if_match is provided and the file has been modified
                (ConcurrentModificationError).
        """
        try:
            with okf_write_intent(OkfWriteIntent(actor=resolve_write_actor())):
                result = await asyncio.to_thread(
                    vault.writer.edit,
                    path,
                    old_text=old_text,
                    new_text=new_text,
                    if_match=if_match,
                    line_start=line_start,
                    line_end=line_end,
                )
            return await attach_conventions(vault, asdict(result), path)
        except EditConflictError as exc:
            parts = [str(exc)]
            if exc.closest_match_line is not None:
                parts.append(f"closest_match_line: {exc.closest_match_line}")
            if exc.first_diff_char is not None:
                parts.append(f"first_diff_at_char: {exc.first_diff_char}")
            if exc.expected_snippet is not None:
                parts.append(f"expected: {exc.expected_snippet!r}")
            if exc.found_snippet is not None:
                parts.append(f"found: {exc.found_snippet!r}")
            raise ToolError("\n".join(parts)) from exc

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["delete"],
        annotations={
            "title": "Delete Note",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
        },
    )
    async def delete(
        path: str,
        if_match: str | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Permanently delete a document or attachment.

        For .md documents: removes the file and immediately updates all search
        indices — do not call 'reindex' afterward.
        For attachments: only the file is deleted (no index to update).
        IRREVERSIBLE unless git history exists. Confirm the path with
        the user before calling.

        Args:
            path: Relative path to the document or attachment to delete.
            if_match: Optional etag obtained from a previous 'read' call.
                When provided, the deletion only proceeds if the file has
                not been modified since that read (optimistic concurrency).
                Omit to delete unconditionally.

        Returns:
            Dict with path (str) of the deleted file.

        Typically called after a split or merge to remove the source note once
        its content has been relocated.

        Raises:
            DocumentNotFoundError: If no file exists at the given path.
            McpError: If if_match is provided and the file has been modified
                (ConcurrentModificationError).
        """
        result = await asyncio.to_thread(vault.writer.delete, path, if_match=if_match)
        return asdict(result)

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["rename"],
        annotations={
            "title": "Rename Note",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
    )
    async def rename(
        old_path: str,
        new_path: str,
        if_match: str | None = None,
        update_links: bool = False,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Rename or move a document or attachment. When renaming a .md note,
        always pass update_links=True to rewrite links in other documents
        that point to the old path — omitting this leaves those links broken.

        For .md documents: the file and its search index entries are updated
        immediately — do not call 'reindex' afterward.
        For attachments: only the file is moved (no index update needed).
        Parent directories for new_path are created automatically.

        Args:
            old_path: Current relative path (e.g. "drafts/idea.md"
                or "assets/old.png").
            new_path: Target relative path (e.g. "projects/idea.md"
                or "assets/new.png"). Fails if new_path already exists.
            if_match: Optional etag obtained from a previous 'read' call
                for old_path. When provided, the rename only proceeds if
                the file has not been modified since that read (optimistic
                concurrency). Omit to rename unconditionally.
            update_links: When True, all .md documents that link to old_path
                are also updated so their links point to new_path. Replacement
                is best-effort — failures are logged but do not prevent the
                rename. Default False; set True whenever renaming a .md note
                (omitting this leaves backlinks pointing to the old path).

        Returns:
            Dict with old_path (str), new_path (str), and updated_links (int)
            counting the number of source documents whose links were updated.

        Raises:
            DocumentNotFoundError: If old_path does not exist.
            DocumentExistsError: If new_path already exists.
            ValueError: If the path fails traversal validation.
            McpError: If if_match is provided and the file has been modified
                (ConcurrentModificationError).
        """
        result = await asyncio.to_thread(
            vault.writer.rename,
            old_path,
            new_path,
            if_match=if_match,
            update_links=update_links,
        )
        return asdict(result)

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["rename"],
        annotations={
            "title": "Move Folder",
            "readOnlyHint": False,
            # Removes the source directory tree (shutil.rmtree) and can leave a
            # partial state on a mid-move OS error — materially larger blast
            # radius than single-file rename, so flag it destructive.
            "destructiveHint": True,
            "idempotentHint": False,
        },
    )
    async def move_folder(
        old_dir: str,
        new_dir: str,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Move an entire folder subtree to a new location in one call,
        rewriting every link across the vault that points into the moved
        subtree — the folder-level analogue of 'rename'.

        Moves all files under old_dir (.md notes, attachments, and any other
        files) to the matching path under new_dir, preserving structure. Links
        between documents inside the subtree and backlinks from outside are all
        rewritten. The search index is updated immediately — do not call
        'reindex' afterward.

        The move is atomic at the gate: if any destination file already exists,
        the call fails before moving anything. Link rewrites are best-effort —
        a source that cannot be rewritten is reported in failed_links rather
        than aborting the move. Note: an OS error during the move phase itself
        (permission error, full disk, concurrent file removal) can leave the
        subtree partially moved with the index unchanged; call 'reindex' to
        reconcile the index with the on-disk state.

        Args:
            old_dir: Relative source folder prefix (e.g. "drafts").
            new_dir: Relative target folder prefix (e.g. "archive/2026").
                May be an existing folder — files merge in; a per-file name
                clash aborts the whole move.

        Returns:
            Dict with old_dir (str), new_dir (str), files_moved (int),
            updated_links (int), and failed_links (list[str]).

        Raises:
            DocumentNotFoundError: If old_dir is missing, not a folder, or empty.
            DocumentExistsError: If any destination file already exists.
            ValueError: If a path fails traversal validation or the two paths
                are nested.
        """
        result = await asyncio.to_thread(vault.writer.move_folder, old_dir, new_dir)
        return asdict(result)

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["fetch"],
        annotations={
            "title": "Fetch to Vault",
            "readOnlyHint": False,
            "destructiveHint": False,
            # Treat like write — calling twice with the same inputs is safe
            # (overwrites with same content). Remote content may change between
            # calls, but repeated invocations do not cause harm.
            "idempotentHint": True,
        },
    )
    async def fetch(
        url: str,
        path: str,
        frontmatter: dict[str, Any] | None = None,
        if_match: str | None = None,
        timeout_s: float = 30.0,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Download a file from a URL and save it to the vault.

        Fetches content from an HTTP/HTTPS URL and writes it as a note or
        attachment. Designed for MCP-to-MCP file transfer when content is
        too large to pass through the LLM context window.

        **Context cost:** zero for the bytes themselves — the file is
        downloaded server-side and saved to the vault. After a successful
        fetch, reference the file by its ``path`` (call ``read(path)`` only
        for small results, otherwise pass the path to other tools).

        For .md paths: the response is decoded as UTF-8 text and saved as
        a markdown note with optional frontmatter. The search index is
        updated immediately.

        For other paths: the response is saved as a binary attachment.
        The existing attachment size limit applies.

        Args:
            url: Source URL to download from. Only http:// and https://
                schemes are allowed. SSRF protection: the host is resolved and
                rejected if any address is private, loopback, link-local, or
                reserved, and the validated IP is pinned for the connection
                (closing DNS rebinding). Redirects are NOT followed.
            path: Destination path in the vault (e.g. "notes/report.md"
                or "assets/diagram.png"). Extension determines handling:
                .md for notes, anything else for attachments.
            frontmatter: Optional YAML frontmatter dict for .md files,
                e.g. {"title": "Report", "source": "http://..."}. Ignored
                for attachments.
            if_match: Optional etag from a previous 'read' call for
                optimistic concurrency. Omit to write unconditionally.
            timeout_s: Download timeout in seconds (default 30). Increase
                for large files on slow connections.

        Returns:
            Dict with:
            - path (str): vault path of the written file
            - created (bool): true if new file, false if overwrite
            - content_length (int): bytes downloaded
            - content_type (str or null): Content-Type from the response
            - conventions (list, optional; .md only): the user's authoring
              conventions for the target folder (root-first list of
              {folder, path, content}). When present, verify the saved note
              complies and issue a follow-up 'edit' if it does not.

        Primary building block for URL-to-note capture flows: call ``fetch`` to
        retrieve the source, summarize via the LLM, and ``write`` the result
        as a new note.

        Raises:
            ValueError: If the URL scheme is not http/https, the download
                exceeds the size limit, or the response cannot be decoded.
            ImportError: If httpx is not installed.
        """
        # Validate URL scheme (SSRF protection).
        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_FETCH_SCHEMES:
            raise ValueError(
                f"Only http and https URLs are allowed, got {parsed.scheme!r}"
            )
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("The fetch URL has no host.")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        # Resolve + validate the host now, and pin the resulting IP into the
        # connection below: the address validated here is the one actually
        # dialled, which closes the DNS-rebinding TOCTOU window. Fails closed.
        pinned_ip = await _resolve_pinned_ip(hostname, port)

        # Conditional import — httpx is an optional dependency.
        try:
            import httpx
        except ImportError:
            raise ImportError(
                "The 'fetch' tool requires 'httpx'. Install it with:\n"
                "  pip install 'markdown-vault-mcp[all]'\n"
                "  # or: pip install httpx"
            ) from None

        # Determine size limit (attachments only). This pre-check enforces
        # the limit during streaming so we abort early without buffering the
        # entire payload.
        is_markdown = path.endswith(".md")
        max_bytes = (
            0
            if is_markdown or vault.max_attachment_size_mb <= 0
            else int(vault.max_attachment_size_mb * 1024 * 1024)
        )

        # Connect to the validated IP, but keep the original Host header and TLS
        # SNI so vhost routing and certificate verification still use the real
        # hostname. httpx then dials the pinned IP without re-resolving.
        ip_host = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
        # Rebuild netloc from the pinned IP + port only. Any userinfo
        # (``user:pass@``) in the original URL is intentionally dropped — the
        # fetch tool must not relay embedded credentials to the server.
        pinned_netloc = f"{ip_host}:{parsed.port}" if parsed.port else ip_host
        pinned_url = urlunparse(parsed._replace(netloc=pinned_netloc))
        # Host header carries the real hostname, omitting an explicit default
        # port (RFC 7230 §5.4) so origin matching on strict servers still works.
        if parsed.port is None or parsed.port == _SCHEME_DEFAULT_PORTS.get(
            parsed.scheme
        ):
            host_header = hostname
        else:
            host_header = f"{hostname}:{parsed.port}"

        # Stream download — enforce size limit as chunks arrive.
        chunks: list[bytes] = []
        downloaded = 0
        async with (
            httpx.AsyncClient(timeout=timeout_s, follow_redirects=False) as client,
            client.stream(
                "GET",
                pinned_url,
                headers={"Host": host_header},
                extensions={"sni_hostname": hostname},
            ) as response,
        ):
            response.raise_for_status()
            content_type = response.headers.get("content-type")
            async for chunk in response.aiter_bytes(chunk_size=65536):
                downloaded += len(chunk)
                if max_bytes > 0 and downloaded > max_bytes:
                    raise ValueError(
                        f"Download exceeded the attachment size limit "
                        f"of {vault.max_attachment_size_mb} MB "
                        f"({max_bytes} bytes). Raise "
                        "MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB or "
                        "set it to 0 to disable the limit."
                    )
                chunks.append(chunk)

        raw_bytes = b"".join(chunks)
        content_length = downloaded

        # Redact userinfo and query string to avoid logging credentials
        # (pre-signed URLs, API tokens, embedded passwords).
        _parsed_log = urlparse(url)
        _safe_url = urlunparse(
            _parsed_log._replace(
                netloc=(
                    f"{_parsed_log.hostname}:{_parsed_log.port}"
                    if _parsed_log.port
                    else (_parsed_log.hostname or "")
                ),
                query="",
                fragment="",
            )
        )
        logger.info(
            "fetch: downloaded %d bytes from %s → %s",
            content_length,
            _safe_url,
            path,
        )

        # Dispatch to the appropriate write method.
        if is_markdown:
            try:
                text = decode_utf8(raw_bytes)  # strips a leading BOM (#681)
            except UnicodeDecodeError as exc:
                ct = content_type or "unknown"
                raise ValueError(
                    f"Response body is not valid UTF-8 (content-type: {ct}). "
                    "Only UTF-8 encoded responses can be saved as .md notes."
                ) from exc
            # Bind the OKF provenance actor (#964): fetch writes .md notes
            # through the same DocumentManager.write path as the write/edit
            # tools, so the enricher fires on an OKF-active vault. Attribute the
            # save to the authenticated caller, not the default tool actor.
            with okf_write_intent(OkfWriteIntent(actor=resolve_write_actor())):
                result = await asyncio.to_thread(
                    vault.writer.write,
                    path,
                    text,
                    frontmatter=frontmatter,
                    if_match=if_match,
                )
        else:
            # Attachments never carry OKF frontmatter and go through
            # write_attachment, which the enricher does not touch — no intent.
            result = await asyncio.to_thread(
                vault.writer.write_attachment,
                path,
                raw_bytes,
                if_match=if_match,
            )

        data = {
            **asdict(result),
            "content_length": content_length,
            "content_type": content_type,
        }
        if is_markdown:
            data = await attach_conventions(vault, data, path)
        return data

    @mcp.tool(
        tags={"okf", "write"},
        icons=_TOOL_ICONS["okf_convert_links"],
        annotations={
            "title": "OKF: Convert Wikilinks",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
        },
    )
    async def okf_convert_links(
        folder: str | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Rewrite wikilinks as OKF bundle-root-absolute markdown links.

        A migration transform (Open Knowledge Format): converts every
        resolvable `[[wikilink]]` in the vault (or one folder) into
        `[text](/path/note.md)`, OKF's recommended link style. Only links
        whose target is indexed are converted, so the link graph is
        preserved exactly — a converted link points at the same note the
        wikilink resolved to. Unresolvable wikilinks are left untouched and
        counted as skipped. Each changed note is written through the normal
        write path (git commit if configured). Re-running is safe: already-
        converted links are plain markdown and are not touched again.

        Args:
            folder: Restrict to this folder subtree (e.g. "guides"). Omit to
                convert the whole vault.

        Returns:
            Dict with files_changed, links_converted, links_skipped, and
            notes_scanned.
        """
        # Mechanical migration: suppress the enforced-write enricher so a link
        # rewrite does not re-stamp provenance or clear human verification (#964).
        with okf_write_suppressed():
            result = await asyncio.to_thread(
                vault.writer.okf_convert_links, folder=folder
            )
        return asdict(result)

    @mcp.tool(
        tags={"okf", "write"},
        icons=_TOOL_ICONS["okf_generate_index"],
        annotations={
            "title": "OKF: Generate index.md",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
        },
    )
    async def okf_generate_index(
        folder: str = "",
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Generate a reserved OKF index.md listing from the table of contents.

        A migration transform (Open Knowledge Format): writes (or overwrites)
        the folder's `index.md` as a progressive-disclosure listing —
        `- [title](/path.md) - description` per note, description drawn from
        frontmatter. Existing frontmatter is preserved, so regenerating the
        bundle-root index.md keeps its `okf_version` declaration. Reserved
        files (index.md, log.md) are omitted from the listing.

        Args:
            folder: Vault-relative folder to index (e.g. "guides"). Omit for
                the bundle root.

        Returns:
            Dict with path, entries (count), and frontmatter_preserved (bool).
        """
        with okf_write_suppressed():
            result = await asyncio.to_thread(
                vault.writer.okf_generate_index, folder=folder
            )
        return asdict(result)

    @mcp.tool(
        tags={"okf", "write"},
        icons=_TOOL_ICONS["okf_seed_log"],
        annotations={
            "title": "OKF: Seed log.md",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
    )
    async def okf_seed_log(
        folder: str = "",
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Seed a reserved OKF log.md change history from git history.

        A migration transform (Open Knowledge Format): writes a `log.md` with
        newest-first `## YYYY-MM-DD` sections built from the vault's git
        commit history (one bullet per commit). `folder` both chooses where
        log.md is written and scopes its content: a folder seeds only the
        commits that touched that subtree, while the bundle root seeds the
        whole vault's history. Refuses to overwrite an
        existing log.md — a change history is hand-maintained after seeding,
        so it is never clobbered. Requires the vault to be git-backed; with no
        git history the log is written empty.

        Args:
            folder: Vault-relative folder to write log.md into and scope
                history to (e.g. "guides"). Omit for the bundle root
                (whole-vault history).

        Returns:
            Dict with path, commits (count), and dates (distinct-day count).
        """
        try:
            with okf_write_suppressed():
                result = await asyncio.to_thread(
                    vault.writer.okf_seed_log, folder=folder
                )
        except FileExistsError as exc:
            raise ToolError(str(exc)) from exc
        return asdict(result)

    @mcp.tool(
        tags={"okf", "write", "okf-enforce"},
        icons=_TOOL_ICONS["okf_verify"],
        annotations={
            "title": "OKF: Verify Note",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
    )
    async def okf_verify(
        path: str,
        ctx: Context = CurrentContext(),
        config: ProjectConfig = Depends(get_config),
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Attest a note as human-reviewed by appending an OKF verification.

        Part of the OKF (Open Knowledge Format) enforced-write layer, available
        only when `MARKDOWN_VAULT_MCP_OKF_WRITE` is enabled. Appends a
        `{by: human:<subject>, at: <date>}` entry to the note's `verified`
        frontmatter list, promoting the note's trust tier to `human-reviewed`.

        How the review is confirmed depends on `MARKDOWN_VAULT_MCP_OKF_VERIFY`:

        - **elicit** (default): issues an MCP elicitation asking you to confirm
          you personally reviewed the note; the entry is written only on an
          affirmative reply. Fails closed — if the client cannot elicit or the
          review is declined, nothing is written. A model cannot answer an
          elicitation, so it cannot self-attest.
        - **trust-auth**: attributes to the authenticated caller with no
          confirmation; refuses (a tool error) when the server runs with no
          auth. Only safe when the sole caller is a human-driven UI.

        Note: `human-reviewed` means a human deliberately confirmed the review,
        not that the content is provably correct.

        Args:
            path: Vault-relative path of the note to verify.

        Returns:
            A dict with:
            - `path`: the verified note.
            - `verifier`: the `human:<subject>` actor recorded.
            - `verified_count`: the number of verification entries after the
              append.

        Raises:
            ToolError: If the mode's confirmation gate is not satisfied (no
                elicitation support / declined under `elicit`, or no
                authenticated identity under `trust-auth`), the note does not
                exist, or the note changed since it was read (a concurrent
                write — retry the verification).
        """
        subject = await _resolve_verify_mode_subject(
            mode=config.okf_verify, ctx=ctx, path=path
        )
        note = await asyncio.to_thread(vault.reader.read, path)
        if note is None:
            raise ToolError(f"Note not found: {path}")
        prior = note.frontmatter.get("verified")
        verified_count = (len(prior) if isinstance(prior, list) else 0) + 1
        new_text = append_okf_verification(
            note.content, subject=subject, today=date.today()
        )
        # Verification attests to a specific set of bytes, so the read-modify-
        # write must be atomic: pass the read's etag as if_match so a concurrent
        # write/edit/delete in the window fails the attestation instead of
        # silently clobbering it or resurrecting a deleted note (#964). Suppress
        # the enricher so it does not clear the verification just added.
        try:
            with okf_write_suppressed():
                await asyncio.to_thread(
                    vault.writer.write, path, new_text, if_match=note.etag
                )
        except ConcurrentModificationError as exc:
            raise ToolError(
                f"Note {path!r} changed since it was read; verification "
                "aborted to avoid attesting stale content. Re-read and retry."
            ) from exc
        return {
            "path": path,
            "verifier": f"{_HUMAN_ACTOR_PREFIX}{subject}",
            "verified_count": verified_count,
        }
