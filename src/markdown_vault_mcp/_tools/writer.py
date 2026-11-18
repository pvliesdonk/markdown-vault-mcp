"""Write-tool registrations.

FastMCP 4 protocol-era behavior used by ``okf_verify`` is recorded in
``docs/design/reference/fastmcp-4.md``.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Iterator

    from markdown_vault_mcp.types import EditResult, WriteResult

import httpx
from fastmcp import Context, FastMCP
from fastmcp.dependencies import CurrentContext, Depends
from fastmcp.exceptions import ToolError
from fastmcp.server.elicitation import AcceptedElicitation
from fastmcp_pvl_core import fetch_url, tool_boundary
from mcp.shared.exceptions import MCPError
from mcp.types import (
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitResult,
    InputRequiredResult,
)
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from markdown_vault_mcp._tools._outcomes import library_outcomes
from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    EditConflictError,
    FolderMoveInterruptedError,
    InvalidRequestError,
    StoreUnsupportedSchemaError,
)
from markdown_vault_mcp.utils import is_note
from markdown_vault_mcp.utils.text import decode_utf8
from markdown_vault_mcp.vault import Vault

from .._icons import _TOOL_ICONS
from .._identity import bound_principal, resolve_mcp_principal
from .._okf_receipts import ReceiptNotRecordedError
from .._okf_write import (
    resolve_human_subject,
    resolve_verify_subject,
)
from ..domain import get_config, get_vault
from ._common import attach_conventions, attach_remote_health, okf_active
from .transactions import run_in_transaction

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def write_identity_scope() -> Iterator[None]:
    """Resolve the caller's identity once and bind it for a write (#1160).

    Resolves a :class:`~markdown_vault_mcp._identity.Principal` from the MCP
    request context and binds it on the identity contextvar, so the write
    path — and the write-callback dispatcher's ``fire()`` snapshot, which is
    what carries it onto the commit thread (#1218) — sees it. Enter this
    **before** the ``asyncio.to_thread`` call so the copied worker context
    carries the binding.

    """
    principal = resolve_mcp_principal()
    with contextlib.ExitStack() as stack:
        stack.enter_context(bound_principal(principal))
        yield


# ``fetch_url`` requires a positive size cap. Markdown fetches and
# cap-disabled attachment fetches are intentionally unbounded, so they get a
# bound no real download can reach.
_FETCH_UNCAPPED_BYTES = 2**63 - 1

_RECEIPT_STATE_UNUSABLE = (
    "Local review receipt state is corrupt or from a newer server version, so "
    "nothing was written. Ask the operator to check the server's local state."
)


async def _require_review_authority(config: ProjectConfig, vault: Vault) -> None:
    """Refuse independent review when the vault is not active."""
    if (
        not config.content.okf_write
        and not (await asyncio.to_thread(vault.okf.state)).active
    ):
        raise ToolError(
            "OKF is not active for this vault, so okf_verify cannot record a review. "
            "Ask the operator to declare the bundle or enable OKF for this vault.",
            log_level=logging.INFO,
        )


def _review_message(path: str, digest: str, check: str) -> str:
    """Build the human-review confirmation prompt for *path*."""
    return (
        f"Confirm you have personally reviewed {path!r}, snapshot {digest[:12]}, "
        f"check {check}, and want to attest it "
        "as human-reviewed. This records a verification in the note's OKF "
        "frontmatter and promotes its trust tier."
    )


def _review_input_required(path: str, digest: str, check: str) -> InputRequiredResult:
    """Build the modern-protocol review request for *path*."""
    request = ElicitRequest(
        method="elicitation/create",
        params=ElicitRequestFormParams(
            message=_review_message(path, digest, check),
            requested_schema={
                "type": "object",
                "properties": {
                    "value": {
                        "type": "boolean",
                        "title": "Confirm human review",
                    }
                },
                "required": ["value"],
            },
        ),
    )
    return InputRequiredResult(
        result_type="input_required",
        input_requests={json.dumps([path, digest, check]): request},
    )


def _require_modern_review(
    ctx: Context, path: str, digest: str, check: str
) -> InputRequiredResult | None:
    """Return a review request or validate its modern-protocol response.

    Raises:
        ToolError: If the human declines, cancels, or answers negatively.
    """
    responses = ctx.input_responses
    if responses is None:
        return _review_input_required(path, digest, check)
    answer = responses.get(json.dumps([path, digest, check]))
    if (
        isinstance(answer, ElicitResult)
        and answer.action == "accept"
        and isinstance(answer.content, dict)
        and answer.content.get("value") is True
    ):
        return None
    raise ToolError(
        "Human review was not confirmed, so no verification was written.",
        log_level=logging.INFO,
    )


def _remote_status_error(exc: httpx.HTTPStatusError) -> ToolError | None:
    """Map the remote site's HTTP status for fetch to an outcome (#1608).

    The message names the status, never the URL: the caller's URL may carry
    credentials or a signed query, and the middleware logs this text. A 429
    goes back to ``tool_boundary``, which tells the model to retry later at
    WARNING; a 5xx heals on the remote side, so it is a WARNING retry; any
    other status means the URL is wrong for this request.

    Returns:
        The ``ToolError`` to raise, or ``None`` for a 429, which the caller
        re-raises unchanged.
    """
    status = exc.response.status_code
    if status == 429:
        return None
    if status >= 500:
        return ToolError(
            f"The site answered HTTP {status}, so nothing was saved. Retry later.",
            log_level=logging.WARNING,
        )
    return ToolError(
        f"The site answered HTTP {status}, so nothing was saved. Check the URL.",
        log_level=logging.INFO,
    )


async def _require_review_elicitation(
    ctx: Context, path: str, digest: str, check: str
) -> None:
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
    try:
        # A scalar bool wraps into a single-field object schema; an affirmative
        # reply deconstructs back to True.
        result = await ctx.elicit(
            _review_message(path, digest, check), response_type=bool
        )
    except MCPError as exc:
        logger.info("okf_verify_elicitation_unsupported path=%s", path)
        raise ToolError(
            "okf_verify needs a human to confirm the review, and this client "
            "cannot ask one, so nothing was written. Tell the user the review "
            "needs a client that supports elicitation.",
            log_level=logging.INFO,
        ) from exc
    if isinstance(result, AcceptedElicitation) and result.data:
        return
    raise ToolError(
        "Human review was not confirmed, so no verification was written.",
        log_level=logging.INFO,
    )


async def _resolve_verify_mode_subject(
    *, mode: str, ctx: Context, path: str, snapshot: dict[str, str], check: str
) -> str | InputRequiredResult:
    """Resolve the ``human:`` subject to stamp, enforcing *mode*'s gate.

    ``trust-auth`` requires a human principal (refuses service credentials
    and auth mode ``none``); any other mode is treated as ``elicit`` and requires an
    affirmative elicitation before attributing to the authenticated subject (or
    the local sentinel). ``off`` never reaches here — the server hides the tool.

    Raises:
        ToolError: If the mode's gate is not satisfied.
    """
    if mode == "trust-auth":
        subject = resolve_human_subject()
        if subject is None:
            raise ToolError(
                "okf_verify attributes a review to the authenticated person, and "
                "this connection has no human identity, so nothing was written. "
                "Tell the user the review needs a signed-in person.",
                log_level=logging.INFO,
            )
        return subject
    request_context = ctx.request_context
    if (
        request_context is not None
        and request_context.protocol_version in MODERN_PROTOCOL_VERSIONS
    ):
        request = _require_modern_review(ctx, path, snapshot["digest"], check)
        if request is not None:
            return request
    else:
        await _require_review_elicitation(ctx, path, snapshot["digest"], check)
    return resolve_verify_subject()


def _write_payload(
    result: WriteResult | EditResult, *, link_report: bool = False
) -> dict[str, Any]:
    """Serialise a write or edit result, dropping keys that carry nothing.

    ``previous_revision`` is only ever set by an overwriting ``write`` on a
    git-backed vault whose replaced content is provably in a commit (#1137).
    Every other producer of this result type — ``append``,
    ``write_attachment``, ``fetch`` — leaves it ``None``, and a permanently
    null key on those responses spends client context on nothing.  Absent says
    "no route back" just as well as null does, without the noise.

    ``unresolved_links`` is kept only when the call asked for the link report
    (*link_report*, #1725), and then kept even as null: there, null says the
    check could not run, which an absent key would hide.  Every other
    response stays exactly as it was before the report existed.
    """
    data = asdict(result)
    if data.get("previous_revision") is None:
        data.pop("previous_revision", None)
    if not link_report:
        data.pop("unresolved_links", None)
    return data


def register(mcp: FastMCP) -> None:
    """Register write/mutation tools on *mcp*."""

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["write"],
        annotations={
            "title": "Write Note",
            "read_only_hint": False,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def write(
        path: str,
        content: str = "",
        frontmatter: dict[str, Any] | None = None,
        content_base64: str = "",
        if_match: str | None = None,
        transaction: str | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Create a note or attachment, or replace one whole; returns its path and
        whether it was created.

        Use edit for targeted changes and append to add to the end. Parent folders are
        created as needed.

        Args:
            path: Path in the vault, such as `"Journal/note.md"` or `"assets/photo.png"`; the
                extension decides between note and attachment.
            content: The note's Markdown body, without frontmatter.
            frontmatter: The note's frontmatter, such as
                `{"title": "My Note", "tags": ["draft"]}`.
            content_base64: An attachment's bytes, base64-encoded; for paths not ending
                in .md.
            if_match: Etag from read, to replace an existing file; omit for a new file.
            transaction: Handle from begin_transaction; omit to commit this call alone.

        Returns:
            Dict with path (str) and created (bool: true if new file,
            false if overwrite). On a git-backed vault, an overwrite also
            carries 'previous_revision': the commit holding the content this
            write just replaced. Read it back with
            read(path, revision=<that sha>), then write it again with if_match
            set to the etag from a plain read(path). The key is absent when no
            commit provably holds the replaced content: a create, no git, or
            content that was never committed (which git cannot recover at all).
            For .md files, may include 'conventions':
            the user's authoring conventions for the target folder
            (root-first list of {folder, path, content}). When present,
            verify the note you just wrote complies, such as with self-containment
            or linking-direction rules, and issue a follow-up 'edit' if it
            does not. To check conventions *before* writing, call
            'get_conventions(path)'.

            - unresolved_links (list[str] or null, optional): present only
              for a .md path, when the server runs with
              `MARKDOWN_VAULT_MCP_REPORT_UNRESOLVED_LINKS=true`. The target,
              as written, of each link this write added that
              get_broken_links reports; links the note already held are left
              out. An empty list means every new link resolves; null means
              the index could not be checked this time.
            - remote (dict, optional): present only while the vault's git clone
              cannot reach its remote, with state, reason, since and detail; the
              change is committed locally only.

        Raises:
            InvalidRequestError: If a destination or changed path is protected.
            ValueError: If content_base64 is missing/invalid for
                attachments, or the content exceeds
                `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB`.
            `MCPError`: If if_match is provided and the file has been
                modified, or if_match is supplied for a file that does not
                yet exist (ConcurrentModificationError). Also when the server
                runs with `MARKDOWN_VAULT_MCP_WRITE_PROTECT_EXISTING=true` (default)
                and path already exists while no if_match is supplied
                (DocumentExistsError); use 'edit' or 'append' instead, or
                read the file first and pass its etag as if_match.
        """
        if not is_note(path):
            if not content_base64:
                raise InvalidRequestError(
                    f"content_base64 is required for non-.md attachments: {path}"
                )
            try:
                raw_bytes = base64.b64decode(content_base64)
            except Exception as exc:
                raise InvalidRequestError(
                    f"Invalid base64 in content_base64: {exc}"
                ) from exc
            cap_mb = vault.max_attachment_size_mb
            if cap_mb > 0 and len(raw_bytes) > int(cap_mb * 1024 * 1024):
                raise InvalidRequestError(
                    f"Attachment {path!r} is {len(raw_bytes):,} bytes, over the "
                    f"{int(cap_mb * 1024 * 1024):,}-byte limit this server "
                    "accepts in a write. Upload it with create_upload_link if "
                    "that tool is available."
                )
            # Attachments carry no OKF frontmatter; bind only the principal
            # so the git commit is attributed to the caller (#1218).
            with write_identity_scope():
                result = await run_in_transaction(
                    vault,
                    transaction,
                    vault.writer.write_attachment,
                    path,
                    raw_bytes,
                    if_match=if_match,
                )
            return attach_remote_health(vault, _write_payload(result))
        # Bind the caller for commit attribution across the worker boundary.
        report = vault.report_unresolved_links
        with write_identity_scope():
            result = await run_in_transaction(
                vault,
                transaction,
                vault.writer.write,
                path,
                content,
                frontmatter=frontmatter,
                if_match=if_match,
                report_unresolved_links=report,
            )
        payload = _write_payload(result, link_report=report)
        return attach_remote_health(
            vault, await attach_conventions(vault, payload, path)
        )

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["edit"],
        annotations={
            "title": "Edit Note",
            "read_only_hint": False,
            "destructive_hint": False,
            "idempotent_hint": False,
        },
    )
    @tool_boundary
    @library_outcomes
    async def edit(
        path: str,
        old_text: str | None = None,
        new_text: str = "",
        if_match: str | None = None,
        line_start: int | None = None,
        line_end: int | None = None,
        transaction: str | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Replace text in an existing note, chosen by an exact old_text, by a range of
        lines, or by an old_text within a range of lines.

        Read the note first for its current text. When old_text has no
        exact match, a unique match after normalising Unicode, dashes, quotes and
        whitespace is used and reported as match_type `"normalized"`.

        Args:
            path: Path of the note.
            old_text: Text to replace, occurring once in the note or in the line range;
                omit to replace the whole line range.
            new_text: Replacement text; empty deletes the matched text or the lines.
            if_match: Etag from read. Pass it, one edit per read, when line_start and
                line_end are given; omit it for several old_text-only edits to one note
                at once.
            line_start: First line to replace, counting from 1 over the content read
                returns, frontmatter included; pass with line_end.
            line_end: Last line to replace, inclusive; pass with line_start.
            transaction: Handle from begin_transaction; omit to commit this call alone.

        Returns:
            - **path** (str): path of the edited document.
            - **replacements** (int): always 1.
            - **match_type** (str): `'exact'` or `'normalized'`.
            - **conventions** (list, optional): the user's authoring
              conventions for the note's folder (root-first list of
              {folder, path, content}). When present, verify the edited
              note complies and issue a follow-up 'edit' if it does not.
            - **unresolved_links** (list[str] or null, optional): present only
              when the server runs with
              `MARKDOWN_VAULT_MCP_REPORT_UNRESOLVED_LINKS=true`. The target,
              as written, of each link this edit added that get_broken_links
              reports; links the note already held are left out. An empty list
              means every new link resolves; null means the index could not be
              checked this time.
            - **remote** (dict, optional): present only while the vault's git clone
              cannot reach its remote, with state, reason, since and detail; the
              change is committed locally only.

        Raises:
            InvalidRequestError: If a destination or changed path is protected.
            ValueError: If parameter combination is invalid, or line
                numbers are out of range.
            EditConflictError: If old_text is not found or appears more
                than once; a not-found error may name the closest line and the
                first differing character.
            DocumentNotFoundError: If no file exists at the given path.
            `MCPError`: If if_match is provided and the file has been modified
                (ConcurrentModificationError).
        """
        report = vault.report_unresolved_links
        try:
            with write_identity_scope():
                result = await run_in_transaction(
                    vault,
                    transaction,
                    vault.writer.edit,
                    path,
                    old_text=old_text,
                    new_text=new_text,
                    if_match=if_match,
                    line_start=line_start,
                    line_end=line_end,
                    report_unresolved_links=report,
                )
            payload = _write_payload(result, link_report=report)
            return attach_remote_health(
                vault, await attach_conventions(vault, payload, path)
            )
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
            raise ToolError("\n".join(parts), log_level=logging.INFO) from exc

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["append"],
        annotations={
            "title": "Append to Note",
            "read_only_hint": False,
            "destructive_hint": False,
            "idempotent_hint": False,
        },
    )
    @tool_boundary
    @library_outcomes
    async def append(
        path: str,
        content: str,
        if_match: str | None = None,
        create_if_missing: bool = False,
        transaction: str | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Add text to the end of an existing note without reading it first.

        Prefer it over edit when the change only adds at the end, such as a log entry.
        The text starts on a new line; begin it with a blank line or a heading to start
        a paragraph or section.

        Args:
            path: Path of the note, such as `"Journal/2026.md"`.
            content: Non-empty text to add after everything the note holds.
            if_match: Etag from read; the text is then added only to that version. Omit
                to append regardless.
            create_if_missing: Create the note with content as its body when path does
                not exist. Default false, so a mistyped path creates nothing.
            transaction: Handle from begin_transaction; omit to commit this call alone.

        Returns:
            - **path** (str): path of the document.
            - **created** (bool): true only when create_if_missing created
              a new note.
            - **conventions** (list, optional): the user's authoring
              conventions for the note's folder (root-first list of
              {folder, path, content}). When present, verify the appended
              content complies and issue a follow-up 'edit' if it does not.
            - **unresolved_links** (list[str] or null, optional): present only
              when the server runs with
              `MARKDOWN_VAULT_MCP_REPORT_UNRESOLVED_LINKS=true`. The target,
              as written, of each link this append added that
              get_broken_links reports; links the note already held are left
              out. An empty list means every new link resolves; null means the
              index could not be checked this time.
            - **remote** (dict, optional): present only while the vault's git clone
              cannot reach its remote, with state, reason, since and detail; the
              change is committed locally only.

        Raises:
            InvalidRequestError: If a destination or changed path is protected.
            ValueError: If content is empty.
            DocumentNotFoundError: If no file exists at the given path and
                create_if_missing is false.
            `MCPError`: If if_match is provided and the file has been modified
                (ConcurrentModificationError).
        """
        report = vault.report_unresolved_links
        with write_identity_scope():
            result = await run_in_transaction(
                vault,
                transaction,
                vault.writer.append,
                path,
                content,
                if_match=if_match,
                create_if_missing=create_if_missing,
                report_unresolved_links=report,
            )
        payload = _write_payload(result, link_report=report)
        return attach_remote_health(
            vault, await attach_conventions(vault, payload, path)
        )

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["delete"],
        annotations={
            "title": "Delete Note",
            "read_only_hint": False,
            "destructive_hint": True,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def delete(
        path: str,
        if_match: str | None = None,
        transaction: str | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Permanently delete a note or attachment; only git history can bring it back.

        Args:
            path: Path of the note or attachment.
            if_match: Etag from read; the file is then deleted only as that version.
                Omit to delete it as it is.
            transaction: Handle from begin_transaction; omit to commit this call alone.

        Returns:
            Dict with path (str) of the deleted file.

            - remote (dict, optional): present only while the vault's git clone
              cannot reach its remote, with state, reason, since and detail; the
              change is committed locally only.

        Raises:
            InvalidRequestError: If a destination or changed path is protected.
            DocumentNotFoundError: If no file exists at the given path.
            `MCPError`: If if_match is provided and the file has been modified
                (ConcurrentModificationError).
        """
        # Bind the caller's Principal so the delete commit is attributed
        # (#1218); the OKF enricher never runs for deletes, so no intent.
        with write_identity_scope():
            result = await run_in_transaction(
                vault, transaction, vault.writer.delete, path, if_match=if_match
            )
        return attach_remote_health(vault, asdict(result))

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["rename"],
        annotations={
            "title": "Rename Note",
            "read_only_hint": False,
            "destructive_hint": False,
            "idempotent_hint": False,
        },
    )
    @tool_boundary
    @library_outcomes
    async def rename(
        old_path: str,
        new_path: str,
        if_match: str | None = None,
        update_links: bool = False,
        transaction: str | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Rename or move a note or attachment; with update_links, links to it in other
        notes follow it.

        For a note, always pass update_links=True. Parent folders are created as needed.

        Args:
            old_path: Current path, such as `"drafts/idea.md"` or `"assets/old.png"`.
            new_path: New path, such as `"projects/idea.md"`, where nothing exists yet.
            if_match: Etag from read of old_path; still accepted when the only
                changes since are link rewrites the server made.
            update_links: Rewrite links to old_path in other notes so they point at
                new_path; attachment references are not tracked. Default false; pass
                true for every note.
            transaction: Handle from begin_transaction; omit to commit this call alone.

        Returns:
            Dict with old_path (str), new_path (str), and updated_links (int)
            counting the number of source documents whose links were updated.
            Carries hint (str) only when update_links was requested for an
            attachment, saying why nothing was rewritten.

            - link_rewrites_since_etag (int, optional): present only when
              if_match was accepted although the server had since rewritten links
              in the note; counts those rewrites, which the rename kept.

            - remote (dict, optional): present only while the vault's git clone
              cannot reach its remote, with state, reason, since and detail; the
              change is committed locally only.

        Raises:
            InvalidRequestError: If a destination or changed path is protected.
            DocumentNotFoundError: If old_path does not exist.
            DocumentExistsError: If new_path already exists.
            ValueError: If the path fails traversal validation.
            `MCPError`: If if_match is provided and the file has changed since,
                other than through link rewrites the server made
                (ConcurrentModificationError).
            TimeoutError: With update_links on a note, if writes queued before the
                call have not reached the index within 60 seconds; nothing is
                changed.
            IndexUnavailableError: With update_links on a note, if the index build
                failed; nothing is changed.
        """
        # Bind the caller's Principal so the rename commit (and any link-
        # rewrite commits) are attributed (#1218); no OKF intent — the
        # enricher's actor for link rewrites stays the tool actor, as before.
        with write_identity_scope():
            result = await run_in_transaction(
                vault,
                transaction,
                vault.writer.rename,
                old_path,
                new_path,
                if_match=if_match,
                update_links=update_links,
                # Parallel renames of linked notes stale each other's etags
                # through link rewrites alone (#1492).
                tolerate_link_rewrites=True,
            )
        data = asdict(result)
        # Absent, not null, when there is nothing to say — like `remote`.
        for key in ("hint", "link_rewrites_since_etag"):
            if data[key] is None:
                del data[key]
        return attach_remote_health(vault, data)

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["rename"],
        annotations={
            "title": "Move Folder",
            "read_only_hint": False,
            # Removes the source directory tree (shutil.rmtree) and can leave a
            # partial state on a mid-move OS error — materially larger blast
            # radius than single-file rename, so flag it destructive.
            "destructive_hint": True,
            "idempotent_hint": False,
        },
    )
    @tool_boundary
    @library_outcomes
    async def move_folder(
        old_dir: str,
        new_dir: str,
        transaction: str | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Move a folder and everything under it to a new location, rewriting every link
        into it across the vault: rename for a whole folder.

        Notes whose links could not be rewritten are listed in failed_links.

        Args:
            old_dir: Folder to move, such as `"drafts"`.
            new_dir: Destination folder, such as `"archive/2026"`; an existing folder is
                merged into.
            transaction: Handle from begin_transaction; omit to commit this call alone.

        Returns:
            Dict with old_dir (str), new_dir (str), files_moved (int) and
            updated_links (int), plus:

            - failed_links (list[str]): paths of notes whose links could not be
              rewritten; the move itself stands.
            - hint (str, optional): guidance when protected link rewrites are skipped.
            - remote (dict, optional): present only while the vault's git clone
              cannot reach its remote, with state, reason, since and detail; the
              change is committed locally only.

        Raises:
            InvalidRequestError: If a destination or changed path is protected.
            DocumentNotFoundError: If no non-empty folder exists at old_dir.
            DocumentExistsError: If any destination file already exists; nothing
                is moved.
            ValueError: If a path fails traversal validation or the two paths
                are nested.
            ToolError: If a file error on the server interrupts the move, so some
                files may already be under new_dir; call reindex so search
                matches the files.
            TimeoutError: If writes queued before the call have not reached the index
                within 60 seconds; nothing is changed.
            IndexUnavailableError: If the index build failed; nothing is changed.
        """
        # Bind the caller's Principal so every per-file commit of the move is
        # attributed (#1218).
        with write_identity_scope():
            try:
                result = await run_in_transaction(
                    vault, transaction, vault.writer.move_folder, old_dir, new_dir
                )
            except FolderMoveInterruptedError as exc:
                # A file error while moving can leave the subtree part-moved
                # with the index still naming the old paths; reindex is the one
                # repair the model can make. It needs an operator: ERROR. Any
                # error raised before a file moved stays tool_boundary's.
                logger.exception(
                    "move_folder_os_error old_dir=%s new_dir=%s", old_dir, new_dir
                )
                raise ToolError(
                    f"Moving {old_dir!r} to {new_dir!r} hit a file error on the "
                    "server, so some files may already be under new_dir. The "
                    "request was fine: call reindex so search matches the files, "
                    "then tell the user."
                ) from exc
        payload = asdict(result)
        if payload["hint"] is None:
            del payload["hint"]
        return attach_remote_health(vault, payload)

    @mcp.tool(
        tags={"write"},
        icons=_TOOL_ICONS["fetch"],
        annotations={
            "title": "Fetch to Vault",
            "read_only_hint": False,
            "destructive_hint": False,
            # Treat like write — calling twice with the same inputs is safe
            # (overwrites with same content). Remote content may change between
            # calls, but repeated invocations do not cause harm.
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def fetch(
        url: str,
        path: str,
        frontmatter: dict[str, Any] | None = None,
        if_match: str | None = None,
        timeout_s: float = 30.0,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Download a URL on the server and save it in the vault as a note or
        attachment; returns its path, size and final_url.

        The bytes never pass through the conversation, so use it for large files and
        then pass the path to other tools.

        Args:
            url: An HTTP or HTTPS URL on the public internet. Redirects are followed, so
                check final_url in the result when the source host matters.
            path: Destination path, such as `"notes/report.md"` or `"assets/diagram.png"`; .md
                saves a note, anything else an attachment.
            frontmatter: Frontmatter for a note, such as
                `{"title": "Report", "source": "https://example.org/report"}`.
            if_match: Etag from read, to replace an existing file; omit for a new file.
            timeout_s: Download timeout in seconds (default 30); raise it for a large
                file from a slow host.

        Returns:
            Dict with:

            - path (str): vault path of the written file
            - created (bool): true if new file, false if overwrite
            - content_length (int): bytes downloaded
            - content_type (str or null): Content-Type from the response
            - final_url (str): the URL the bytes actually came from: equal
              to `url` when nothing redirected, otherwise the last hop.
              A user name or password in the URL is stripped; the query string is not, so do not log
              it verbatim
            - conventions (list, optional; .md only): the user's authoring
              conventions for the target folder (root-first list of
              {folder, path, content}). When present, verify the saved note
              complies and issue a follow-up 'edit' if it does not.
            - remote (dict, optional): present only while the vault's git clone
              cannot reach its remote, with state, reason, since and detail; the
              change is committed locally only.

        Raises:
            InvalidRequestError: If a destination or changed path is protected.
            DocumentExistsError: If the server runs with
                `MARKDOWN_VAULT_MCP_WRITE_PROTECT_EXISTING=true` (default) and *path*
                already exists while no *if_match* is supplied. The save
                routes through the same guarded `write` /
                `write_attachment` path as the write tools, so read the
                existing note first and pass its etag as *if_match* to
                replace it deliberately, or fetch to a fresh path.
            InvalidRequestError: If the URL scheme is not http/https, the host
                is not publicly routable (private, loopback, link-local,
                carrier-grade NAT and reserved ranges are refused) or cannot be
                resolved, checked on the supplied URL and on every redirect hop,
                the download exceeds the size limit, or the response cannot be
                decoded.
            ToolError: If the remote site answers a non-2xx status (retry
                later for a 5xx, check the URL otherwise; the message never
                names the URL) or redirects more times than the client allows.
            `httpx.HTTPStatusError`: On a 429; `tool_boundary` turns it into a
                `"retry later"` answer.
            `httpx.TransportError`: On a timeout or a failed connection; it
                propagates to `tool_boundary`.
        """
        # Attachment size cap only: markdown notes are not size-limited, and
        # a non-positive configured cap disables the limit. `capped` derives
        # from the computed byte count, not the MB value, so a sub-byte
        # positive setting (which floors to 0 bytes) counts as "no cap" —
        # matching the pre-#862 guard — rather than tripping fetch_url's
        # positive-max_bytes validation on every attachment fetch.
        is_markdown = is_note(path)
        await asyncio.to_thread(
            vault._validate_path if is_markdown else vault._validate_attachment_path,
            path,
        )
        await asyncio.to_thread(vault._doc_mgr.refuse_folder_in_submodule, path, "path")
        await asyncio.to_thread(vault._doc_mgr.refuse_protected, path)
        cap_bytes = (
            0
            if is_markdown or vault.max_attachment_size_mb <= 0
            else int(vault.max_attachment_size_mb * 1024 * 1024)
        )
        capped = cap_bytes > 0
        max_bytes = cap_bytes if capped else _FETCH_UNCAPPED_BYTES

        # All SSRF hardening (scheme allowlist, non-public-address rejection
        # incl. CGNAT, DNS-rebind pinning, trust_env=False, per-redirect-hop
        # revalidation of all of those, streaming size cap, userinfo/query
        # redaction) lives in pvl-core's fetch_url — the shared primitive this
        # tool's guard was lifted into (#862; pvl-core #219).
        try:
            fetched = await fetch_url(url, max_bytes=max_bytes, timeout_s=timeout_s)
        except ValueError as exc:
            # Every ValueError fetch_url raises is the URL's: a scheme, host or
            # address it refuses, a name that does not resolve, or a body over
            # the cap (#1608). The size-cap one is restated in the vault's
            # terms. Matches fetch_url's full terminal suffix — not a
            # substring: the message embeds the (redacted) URL, which is
            # caller-supplied, so a substring test could be spoofed by a URL
            # crafted to contain the phrase; the suffix is fetch_url's own text
            # and cannot be. On a reword, the original message is passed on.
            if capped and str(exc).endswith(
                f"exceeded the size cap of {max_bytes} bytes."
            ):
                raise InvalidRequestError(
                    f"The download is over the {max_bytes:,}-byte limit this "
                    "server accepts for an attachment, so nothing was saved. "
                    "Ask the user for a smaller file or another source."
                ) from exc
            raise InvalidRequestError(str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            mapped = _remote_status_error(exc)
            if mapped is None:
                raise
            raise mapped from exc
        except httpx.TooManyRedirects as exc:
            raise ToolError(
                "The URL redirects too many times, so nothing was saved. Check "
                "the URL.",
                log_level=logging.INFO,
            ) from exc

        raw_bytes = fetched.body
        content_type = fetched.content_type
        content_length = fetched.size
        # Where the bytes actually came from. Equal to `url` when nothing
        # redirected; since #1116 redirects are followed, so a caller that
        # cares which host served the content has to be told (validating the
        # supplied URL alone is no longer sufficient). Surfaced rather than
        # logged: fetch_url already logged the redacted source, and this
        # string keeps its query, which may be a pre-signed token.
        final_url = fetched.final_url

        # Dispatch to the appropriate write method.
        if is_markdown:
            try:
                text = decode_utf8(raw_bytes)  # strips a leading BOM (#681)
            except UnicodeDecodeError as exc:
                ct = content_type or "unknown"
                raise InvalidRequestError(
                    f"Response body is not valid UTF-8 (content-type: {ct}). "
                    "Only UTF-8 encoded responses can be saved as .md notes; "
                    "save it under a non-.md path as an attachment instead."
                ) from exc
            # Attribute the save commit to the authenticated caller (#1218).
            with write_identity_scope():
                result = await asyncio.to_thread(
                    vault.writer.write,
                    path,
                    text,
                    frontmatter=frontmatter,
                    if_match=if_match,
                )
        else:
            # Bind the principal for git-commit attribution (#1218).
            with write_identity_scope():
                result = await asyncio.to_thread(
                    vault.writer.write_attachment,
                    path,
                    raw_bytes,
                    if_match=if_match,
                )

        # fetch_url already logged the (redacted) source URL; record the vault
        # destination only after the write actually landed.
        logger.info("fetch_saved bytes=%d path=%s", content_length, path)

        data = {
            **_write_payload(result),
            "content_length": content_length,
            "content_type": content_type,
            "final_url": final_url,
        }
        if is_markdown:
            data = await attach_conventions(vault, data, path)
        return attach_remote_health(vault, data)

    @mcp.tool(
        tags={"okf", "write"},
        icons=_TOOL_ICONS["okf_convert_links"],
        annotations={
            "title": "OKF: Convert Wikilinks",
            "read_only_hint": False,
            "destructive_hint": True,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def okf_convert_links(
        folder: str | None = None,
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Rewrite wikilinks as bundle-root-absolute Markdown links, the Open Knowledge
        Format's recommended style; returns counts of links converted and skipped.

        A wikilink whose target is not in the vault stays as it is. Running it again
        changes nothing already converted.

        Args:
            folder: Only notes in this folder and below, such as `"guides"`; omit for the
                whole vault.

        Returns:
            Dict with:

            - files_changed (int): notes rewritten.
            - links_converted (int): wikilinks turned into Markdown links.
            - links_skipped (int): wikilinks left as they are because the target
              is not in the vault.
            - notes_scanned (int): notes examined.
            - remote (dict, optional): present only while the vault's git clone
              cannot reach its remote, with state, reason, since and detail; the
              change is committed locally only.

            An attachment embed such as `![[pic.png]]` is not a link, so it is
            left as it is and not counted.

        Raises:
            InvalidRequestError: If a path is protected; eligible bulk writes remain.
            TimeoutError: If writes queued before the call have not reached the index
                within 60 seconds; nothing is changed.
            IndexUnavailableError: If the index build failed; nothing is changed.
            DocumentUnreadableError: If a note it rewrites cannot be read; notes
                converted before it keep their changes.
        """
        # The suppression lives on the transform itself (#1401), so the
        # library facade and this tool get the same mechanical write.
        result = await asyncio.to_thread(vault.writer.okf_convert_links, folder=folder)
        return attach_remote_health(vault, asdict(result))

    @mcp.tool(
        tags={"okf", "write"},
        icons=_TOOL_ICONS["okf_generate_index"],
        annotations={
            "title": "OKF: Generate index.md",
            "read_only_hint": False,
            "destructive_hint": True,
            "idempotent_hint": True,
        },
    )
    @tool_boundary
    @library_outcomes
    async def okf_generate_index(
        folder: str = "",
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Write a folder's Open Knowledge Format index.md: a link to each note directly
        in it with its description, and one to each subfolder's index.md, replacing
        the old listing and keeping its frontmatter.

        Subfolders without an index.md get one too.

        Args:
            folder: Folder to index, such as `"guides"`; omit for the bundle root.

        Returns:
            Dict with:

            - path (str): the index.md written.
            - entries (int): notes and subfolder links listed.
            - frontmatter_preserved (bool): true when the file already had
              a frontmatter block, including an empty block, which is kept;
              false when it had none. No fields are added.
            - created (list[str]): index.md files written for subfolders that
              had none, outermost first.
            - remote (dict, optional): present only while the vault's git clone
              cannot reach its remote, with state, reason, since and detail; the
              change is committed locally only.

        Raises:
            InvalidRequestError: If a path is protected; eligible bulk writes remain.
            TimeoutError: If writes queued before the call have not reached the index
                within 60 seconds; nothing is changed.
            IndexUnavailableError: If the index build failed; nothing is changed.
            DocumentUnreadableError: If the folder's index.md exists but cannot be
                read; it is left untouched.
        """
        result = await asyncio.to_thread(vault.writer.okf_generate_index, folder=folder)
        return attach_remote_health(vault, asdict(result))

    @mcp.tool(
        tags={"okf", "write"},
        icons=_TOOL_ICONS["okf_seed_log"],
        annotations={
            "title": "OKF: Seed log.md",
            "read_only_hint": False,
            "destructive_hint": False,
            "idempotent_hint": False,
        },
    )
    @tool_boundary
    @library_outcomes
    async def okf_seed_log(
        folder: str = "",
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Create a folder's Open Knowledge Format log.md from the git history of the
        notes under it, one dated section per day, newest first.

        It never replaces an existing log.md. It covers the 100 most recent commits. A
        vault without git history gets an empty log.

        Args:
            folder: Folder to write log.md in, whose history it covers, such as `"guides"`;
                omit for the bundle root and the whole vault's history.

        Returns:
            Dict with path (str), commits (int) and dates (int, the number of
            distinct days), plus:

            - remote (dict, optional): present only while the vault's git clone
              cannot reach its remote, with state, reason, since and detail; the
              change is committed locally only.

        Raises:
            InvalidRequestError: If a destination or changed path is protected.
            ToolError: If log.md already exists in the folder; nothing is written.
            DocumentUnreadableError: If log.md exists but cannot be read; it is left
                untouched.
        """
        try:
            result = await asyncio.to_thread(vault.writer.okf_seed_log, folder=folder)
        except FileExistsError as exc:
            raise ToolError(str(exc), log_level=logging.INFO) from exc
        return attach_remote_health(vault, asdict(result))

    @mcp.tool(
        tags={"okf", "write", "okf-author"},
        icons=_TOOL_ICONS["okf_author"],
        annotations={
            "title": "OKF: Author Concept",
            "read_only_hint": False,
            "destructive_hint": False,
            "idempotent_hint": False,
        },
    )
    @tool_boundary
    @library_outcomes
    async def okf_author(
        path: str,
        if_match: str,
        fields: dict[str, Any],
        producer: str | None = None,
        config: ProjectConfig = Depends(get_config),
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any]:
        """Set selected editorial fields on an existing OKF concept.

        Args:
            path: Concept path in the vault.
            if_match: Etag from a recent read of the concept.
            fields: Non-empty type, title and description strings; tags as a list
                of strings; status as draft, stable or deprecated; stale_after
                as an ISO date or offset datetime; sources as a list of mappings
                with a non-empty resource string. Omitted fields are preserved.
            producer: Explicit producer actor: <producer>/<version>, process:<id>
                or human:<id>, such as codex/6.1 or process:importer; omit to
                leave generation metadata unchanged.

        Returns:
            The written path and previous revision when available.
            Read the note again to obtain a fresh etag.
        """
        if not config.content.okf_capabilities.author or not await okf_active(vault):
            raise ToolError(
                "OKF authoring is not active for this vault. Ask the operator to enable authoring on an active bundle.",
                log_level=logging.INFO,
            )
        with write_identity_scope():
            result = await asyncio.to_thread(
                vault.writer.okf_author,
                path,
                if_match=if_match,
                fields=fields,
                producer=producer,
            )
        return attach_remote_health(vault, asdict(result))

    @mcp.tool(
        tags={"okf", "write", "okf-enforce"},
        icons=_TOOL_ICONS["okf_verify"],
        annotations={
            "title": "OKF: Verify Note",
            "read_only_hint": False,
            "destructive_hint": False,
            "idempotent_hint": False,
        },
    )
    @tool_boundary
    @library_outcomes
    async def okf_verify(
        path: str,
        reviewed_snapshot: dict[str, str],
        check: Literal["human-review"] = "human-review",
        ctx: Context = CurrentContext(),
        config: ProjectConfig = Depends(get_config),
        vault: Vault = Depends(get_vault),
    ) -> dict[str, Any] | InputRequiredResult:
        """Record a review of a note snapshot; returns the verifier recorded.

        Reviews apply to YAML-frontmatter or body-only notes. The snapshot excludes
        verified and x-mvmcp. The user may be asked to confirm the review.

        Args:
            path: Path of the note the user reviewed.
            reviewed_snapshot: Snapshot from the whole-note read that was reviewed.
            check: Review performed; defaults to human-review.

        Returns:
            On the modern protocol's first round, an input request asking the
            client for human confirmation. After confirmation, a dict with:

            - `path`: the verified note.
            - `verifier`: the `human:<subject>` actor recorded.
            - `verified_count`: the number of verification entries after the
              append.
            - remote (dict, optional): present only while the vault's git clone
              cannot reach its remote, with state, reason, since and detail; the
              change is committed locally only.

        Raises:
            ToolError: If the mode's confirmation gate is not met, or the note
                changed since it was read; read and review the current text before
                calling okf_verify again. Under `elicit` the gate needs a client that can
                ask the user, and the user's confirmation; under `trust-auth`,
                an authenticated identity.
            DocumentNotFoundError: If the note does not exist.
            InvalidRequestError: If the path is protected.
        """
        await _require_review_authority(config, vault)
        await asyncio.to_thread(vault._doc_mgr.refuse_protected, path)
        try:
            await asyncio.to_thread(
                vault.writer.require_reviewable, path, reviewed_snapshot
            )
        except StoreUnsupportedSchemaError as exc:
            raise ToolError(_RECEIPT_STATE_UNUSABLE) from exc
        except OSError as exc:
            raise ToolError(
                f"Could not read note {path!r} or the local review receipt state, "
                "so nothing was written. Ask the operator to check the server's files."
            ) from exc
        subject_or_request = await _resolve_verify_mode_subject(
            mode=config.content.okf_capabilities.verify,
            ctx=ctx,
            path=path,
            snapshot=reviewed_snapshot,
            check=check,
        )
        if isinstance(subject_or_request, InputRequiredResult):
            return subject_or_request
        await _require_review_authority(config, vault)
        try:
            with write_identity_scope():
                result = await asyncio.to_thread(
                    vault.writer.okf_verify,
                    path,
                    subject_or_request,
                    reviewed_snapshot,
                    check,
                )
        except ConcurrentModificationError as exc:
            raise ToolError(
                f"Note {path!r} changed while it was being verified, so nothing "
                "was written. Read and review the current text before retrying.",
                log_level=logging.INFO,
            ) from exc
        except StoreUnsupportedSchemaError as exc:
            raise ToolError(_RECEIPT_STATE_UNUSABLE) from exc
        except ReceiptNotRecordedError as exc:
            raise ToolError(
                f"The review was recorded in note {path!r}, but no local receipt "
                "was saved, so this server holds no local evidence of it. Do not "
                "call okf_verify again for this review; ask the operator to check "
                "the server's local state."
            ) from exc
        except OSError as exc:
            raise ToolError(
                f"Writing the review to note {path!r} failed with an I/O error; "
                "the note may already hold it, and no local receipt was saved. "
                "Read the note again before deciding whether to call okf_verify "
                "again."
            ) from exc
        return attach_remote_health(vault, result)
