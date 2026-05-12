"""MV-side wiring for fastmcp-pvl-core's file-exchange upload helper.

Provides the ``receiver`` callable passed to
:func:`fastmcp_pvl_core.register_file_exchange_upload` in
:mod:`markdown_vault_mcp.server`.  The receiver commits uploaded
bytes to the vault via the existing :class:`~markdown_vault_mcp.collection.Collection`
write APIs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from markdown_vault_mcp._server_deps import get_collection_singleton

if TYPE_CHECKING:
    from fastmcp_pvl_core import UploadRecord


def _vault_upload_receiver(record: UploadRecord, body: bytes) -> dict[str, Any]:
    """Commit uploaded bytes to the vault.

    Dispatches to :meth:`~markdown_vault_mcp.collection.Collection.write`
    for ``.md`` filenames or
    :meth:`~markdown_vault_mcp.collection.Collection.write_attachment`
    for binaries based on the ``target_id``'s extension.  pvl-core's
    ``ExchangeURI.validate_segment`` already enforces that ``target_id``
    is a single safe filename (no slashes, no ``..``, no control bytes),
    and the MV-side pre-link validator runs additional checks at link
    creation time, so this function trusts the value.

    Passes ``skip_size_cap=True`` to ``write_attachment`` because the
    HTTP route already enforced ``MARKDOWN_VAULT_MCP_UPLOAD_MAX_BYTES``
    against the request body, and these bytes never touch LLM context —
    the inner ``MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB`` cap exists
    only to protect context budget for base64 callers of the MCP
    ``write`` tool.

    Args:
        record: pvl-core's :class:`~fastmcp_pvl_core.UploadRecord` —
            ``target_id`` is the vault-relative filename the agent passed
            to ``create_upload_link``.
        body: Raw bytes streamed from the POST request, already validated
            against ``record.max_bytes`` by the HTTP route.

    Returns:
        Dict serialised as the HTTP 200 response body.  Conventional keys:
        ``path``, ``size_bytes``.
    """
    collection = get_collection_singleton()
    if record.target_id.endswith(".md"):
        collection.write(record.target_id, content=body.decode("utf-8"))
    else:
        collection.write_attachment(record.target_id, body, skip_size_cap=True)
    return {"path": record.target_id, "size_bytes": len(body)}


def _validate_upload_target(target_id: str, extra: dict[str, Any] | None) -> None:
    """Reject upload targets that would escape the vault or violate the allowlist.

    Wired as pvl-core's ``register_file_exchange_upload(pre_link_validator=...)``
    callback so failures surface as ``ValueError`` at ``create_upload_link``
    call time, in-band to the agent, rather than after a wasted HTTP POST.

    pvl-core's :class:`~fastmcp_pvl_core.ExchangeURI` already enforces that
    ``target_id`` is a single safe filename (no slashes, no ``..``, no
    control bytes, no leading/trailing whitespace) before this callback
    runs.  This function is therefore defense-in-depth for path traversal
    plus the primary enforcement point for the attachment-extension
    allowlist.

    Delegates to :meth:`Collection._validate_path` for ``.md`` targets and
    :meth:`Collection._validate_attachment_path` for everything else, which
    also re-checks that the resolved path stays under ``source_dir``.

    Args:
        target_id: The vault-relative filename the agent passed to
            ``create_upload_link``.
        extra: Caller-supplied opaque dict from
            ``create_upload_link(extra=...)``.  Unused — accepted to match
            pvl-core's ``PreLinkValidator`` callable signature.

    Raises:
        ValueError: If ``target_id`` would escape the vault or its
            extension is not in the configured attachment allowlist.
    """
    del extra
    collection = get_collection_singleton()
    if target_id.endswith(".md"):
        collection._validate_path(target_id)
    else:
        collection._validate_attachment_path(target_id)
