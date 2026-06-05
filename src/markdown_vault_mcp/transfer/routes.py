"""Starlette handler for the one-time /transfer/{token} route (#622)."""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import TYPE_CHECKING

from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, Response

from markdown_vault_mcp._server_deps import get_vault_singleton

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request

    from markdown_vault_mcp.transfer.store import TransferStore
    from markdown_vault_mcp.vault import Vault

logger = logging.getLogger(__name__)

_FILENAME_UNSAFE = re.compile(r'[\x00-\x1f"\\/]+')


def _sanitize_filename(path: str) -> str:
    """Return a safe Content-Disposition filename from a vault path."""
    base = path.rsplit("/", 1)[-1]
    cleaned = _FILENAME_UNSAFE.sub("_", base).strip()
    return cleaned or "download"


def make_transfer_handler(
    store: TransferStore,
    vault_getter: Callable[[], Vault] = get_vault_singleton,
) -> Callable[[Request], Awaitable[Response]]:
    """Build the async ASGI handler for ``/transfer/{token}``.

    Args:
        store: The shared :class:`TransferStore`.
        vault_getter: Resolves the live :class:`Vault` at request time
            (defaults to the module singleton; injectable for tests).

    Returns:
        An async handler dispatching GET to download, POST/PUT to upload.
    """

    async def handle(request: Request) -> Response:
        token = request.path_params["token"]
        if request.method.upper() == "GET":
            return await _handle_download(store, vault_getter, token)
        return await _handle_upload(store, vault_getter, request, token)

    return handle


async def _handle_download(
    store: TransferStore,
    vault_getter: Callable[[], Vault],
    token: str,
) -> Response:
    """Serve a vault file for a claimed download token, burning it on success."""
    record = store.claim(token, "download")
    if record is None:
        return Response(status_code=404)
    try:
        vault = vault_getter()
    except RuntimeError:
        store.release(token)
        return Response(status_code=503)
    try:
        if record.is_attachment:
            att = await asyncio.to_thread(vault.reader.read_attachment, record.path)
            body = base64.b64decode(att.content_base64)
            media_type = att.mime_type or "application/octet-stream"
        else:
            note = await asyncio.to_thread(vault.reader.read, record.path)
            if note is None:
                store.release(token)
                return Response(status_code=404)
            body = note.content.encode("utf-8")
            media_type = "text/markdown; charset=utf-8"
    except ValueError:
        store.release(token)
        return Response(status_code=404)
    filename = _sanitize_filename(record.path)
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        background=BackgroundTask(store.complete, token),
    )


async def _handle_upload(
    store: TransferStore,
    vault_getter: Callable[[], Vault],
    request: Request,
    token: str,
) -> Response:
    """Commit an uploaded body for a claimed upload token, burning it on success."""
    record = store.claim(token, "upload")
    if record is None:
        return Response(status_code=404)
    try:
        vault = vault_getter()
    except RuntimeError:
        store.release(token)
        return Response(status_code=503)
    cap = record.max_upload_bytes
    declared = request.headers.get("content-length")
    if cap is not None and declared is not None:
        try:
            if int(declared) > cap:
                store.release(token)
                return Response(status_code=413)
        except ValueError:
            pass
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if cap is not None and size > cap:
            store.release(token)
            return Response(status_code=413)
        chunks.append(chunk)
    body = b"".join(chunks)
    try:
        if record.is_attachment:
            await asyncio.to_thread(vault.writer.write_attachment, record.path, body)
        else:
            await asyncio.to_thread(
                vault.writer.write, record.path, body.decode("utf-8")
            )
    except ValueError:
        store.release(token)
        return Response(status_code=415)
    except Exception:
        store.release(token)
        logger.warning("transfer_upload_failed path=%s", record.path, exc_info=True)
        return Response(status_code=500)
    store.complete(token)
    return JSONResponse({"path": record.path, "bytes": len(body)}, status_code=201)
