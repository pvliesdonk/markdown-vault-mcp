from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import socket
from dataclasses import asdict
from typing import Any
from urllib.parse import urlparse, urlunparse

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError

from markdown_vault_mcp.exceptions import EditConflictError
from markdown_vault_mcp.utils.text import decode_utf8
from markdown_vault_mcp.vault import Vault

from .._icons import _TOOL_ICONS
from .._server_deps import get_vault

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
            false if overwrite).

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
        result = await asyncio.to_thread(
            vault.writer.write,
            path,
            content,
            frontmatter=frontmatter,
            if_match=if_match,
        )
        return asdict(result)

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
            result = await asyncio.to_thread(
                vault.writer.edit,
                path,
                old_text=old_text,
                new_text=new_text,
                if_match=if_match,
                line_start=line_start,
                line_end=line_end,
            )
            return asdict(result)
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
            result = await asyncio.to_thread(
                vault.writer.write,
                path,
                text,
                frontmatter=frontmatter,
                if_match=if_match,
            )
        else:
            result = await asyncio.to_thread(
                vault.writer.write_attachment,
                path,
                raw_bytes,
                if_match=if_match,
            )

        return {
            **asdict(result),
            "content_length": content_length,
            "content_type": content_type,
        }
