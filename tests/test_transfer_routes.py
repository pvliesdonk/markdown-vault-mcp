"""Integration tests for the /transfer/{token} route (#622)."""

import base64

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from markdown_vault_mcp.transfer.routes import make_transfer_handler
from markdown_vault_mcp.transfer.store import TransferStore
from markdown_vault_mcp.vault import Vault


@pytest.fixture
def vault(tmp_path):
    """A small writable vault with one note and one attachment."""
    src = tmp_path / "vault"
    src.mkdir()
    (src / "note.md").write_text("# Hello\n\nbody text\n")
    (src / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\nDATA")
    col = Vault(source_dir=src, read_only=False, attachment_extensions=["png"])
    try:
        col.index.build_index()
        yield col
    finally:
        col.close()


def _client(store: TransferStore, vault: Vault) -> TestClient:
    handler = make_transfer_handler(store, vault_getter=lambda: vault)
    app = Starlette(
        routes=[
            Route(
                "/transfer/{token}",
                handler,
                methods=["GET", "POST", "PUT"],
            )
        ]
    )
    return TestClient(app, raise_server_exceptions=False)


def test_download_note_serves_content(vault):
    """GET on a download token returns the note body with markdown type."""
    store = TransferStore()
    rec = store.create("download", "note.md", False, 60)
    resp = _client(store, vault).get(f"/transfer/{rec.token}")
    assert resp.status_code == 200
    assert resp.text == "# Hello\n\nbody text\n"
    assert "text/markdown" in resp.headers["content-type"]
    assert "attachment" in resp.headers["content-disposition"]
    assert "note.md" in resp.headers["content-disposition"]


def test_download_attachment_serves_bytes(vault):
    """GET on an attachment download token returns decoded bytes."""
    store = TransferStore()
    rec = store.create("download", "pic.png", True, 60)
    resp = _client(store, vault).get(f"/transfer/{rec.token}")
    assert resp.status_code == 200
    assert resp.content == b"\x89PNG\r\n\x1a\nDATA"


def test_download_unknown_token_404(vault):
    """An unknown token yields 404."""
    store = TransferStore()
    resp = _client(store, vault).get("/transfer/does-not-exist")
    assert resp.status_code == 404


def test_download_is_one_time(vault):
    """A successful download consumes the token; a replay 404s."""
    store = TransferStore()
    rec = store.create("download", "note.md", False, 60)
    client = _client(store, vault)
    assert client.get(f"/transfer/{rec.token}").status_code == 200
    assert client.get(f"/transfer/{rec.token}").status_code == 404


def test_download_missing_file_404_and_not_consumed(vault):
    """A download whose file vanished 404s and does not burn the token."""
    store = TransferStore()
    rec = store.create("download", "note.md", False, 60)
    (vault.source_dir / "note.md").unlink()
    client = _client(store, vault)
    assert client.get(f"/transfer/{rec.token}").status_code == 404
    assert store.claim(rec.token, "download") is not None


def test_download_missing_attachment_404_and_not_consumed(vault):
    """A download whose attachment vanished 404s (ValueError) and isn't burned."""
    store = TransferStore()
    rec = store.create("download", "pic.png", True, 60)
    (vault.source_dir / "pic.png").unlink()
    client = _client(store, vault)
    assert client.get(f"/transfer/{rec.token}").status_code == 404
    assert store.claim(rec.token, "download") is not None


def test_download_vault_not_initialised_503():
    """An unavailable vault yields 503 and releases the token for retry."""
    store = TransferStore()
    rec = store.create("download", "note.md", False, 60)

    def _raise() -> Vault:
        raise RuntimeError("vault not initialised")

    handler = make_transfer_handler(store, vault_getter=_raise)
    app = Starlette(
        routes=[Route("/transfer/{token}", handler, methods=["GET", "POST", "PUT"])]
    )
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get(f"/transfer/{rec.token}").status_code == 503
    assert store.claim(rec.token, "download") is not None


def test_upload_note_commits_201(vault):
    """POST on an upload token writes the note and returns 201."""
    store = TransferStore()
    rec = store.create("upload", "out.md", False, 60, max_upload_bytes=1000)
    resp = _client(store, vault).post(f"/transfer/{rec.token}", content=b"# New\n\nx\n")
    assert resp.status_code == 201
    assert resp.json()["path"] == "out.md"
    assert vault.reader.read("out.md") is not None


def test_upload_note_strips_bom(vault, tmp_path):
    """An uploaded note body with a leading UTF-8 BOM is normalized away on write (#681).

    Asserts the on-disk bytes (not the read-back content): the #673 read path
    already strips a BOM, so only inspecting disk proves the *ingress* write
    dropped it.
    """
    store = TransferStore()
    rec = store.create("upload", "bom.md", False, 60, max_upload_bytes=1000)
    resp = _client(store, vault).post(
        f"/transfer/{rec.token}", content=b"\xef\xbb\xbf# New\n\nx\n"
    )
    assert resp.status_code == 201
    on_disk = (tmp_path / "vault" / "bom.md").read_bytes()
    assert not on_disk.startswith(b"\xef\xbb\xbf"), "ingested BOM not stripped on write"
    assert on_disk.startswith(b"# New")


def test_upload_attachment_via_put_alias(vault):
    """PUT is accepted as an upload alias and writes an attachment."""
    store = TransferStore()
    rec = store.create("upload", "shot.png", True, 60, max_upload_bytes=1000)
    resp = _client(store, vault).put(f"/transfer/{rec.token}", content=b"PNGDATA")
    assert resp.status_code == 201
    att = vault.reader.read_attachment("shot.png")
    assert base64.b64decode(att.content_base64) == b"PNGDATA"


def test_upload_oversized_content_length_413(vault):
    """A declared Content-Length over the cap is rejected with 413."""
    store = TransferStore()
    rec = store.create("upload", "out.md", False, 60, max_upload_bytes=10)
    resp = _client(store, vault).post(f"/transfer/{rec.token}", content=b"x" * 200)
    assert resp.status_code == 413


def test_upload_streamed_oversize_413(vault):
    """A chunked body exceeding the cap mid-stream is rejected with 413."""
    store = TransferStore()
    rec = store.create("upload", "out.md", False, 60, max_upload_bytes=100)

    def chunks():
        yield b"x" * 60
        yield b"x" * 60

    resp = _client(store, vault).post(f"/transfer/{rec.token}", content=chunks())
    assert resp.status_code == 413


def test_upload_413_does_not_burn_token(vault):
    """A rejected oversize upload frees the link to retry."""
    store = TransferStore()
    rec = store.create("upload", "out.md", False, 60, max_upload_bytes=100)
    client = _client(store, vault)
    assert client.post(f"/transfer/{rec.token}", content=b"x" * 200).status_code == 413
    assert client.post(f"/transfer/{rec.token}", content=b"hi").status_code == 201


def test_upload_is_one_time(vault):
    """A successful upload consumes the token."""
    store = TransferStore()
    rec = store.create("upload", "out.md", False, 60, max_upload_bytes=1000)
    client = _client(store, vault)
    assert client.post(f"/transfer/{rec.token}", content=b"a").status_code == 201
    assert client.post(f"/transfer/{rec.token}", content=b"b").status_code == 404


def test_kind_confusion_download_token_via_post_404(vault):
    """A download token cannot be used for upload."""
    store = TransferStore()
    rec = store.create("download", "note.md", False, 60)
    resp = _client(store, vault).post(f"/transfer/{rec.token}", content=b"x")
    assert resp.status_code == 404


def test_no_leak_uniform_404(vault):
    """Unknown, consumed, expired, and kind-mismatched tokens all 404 identically."""

    class _Clock:
        def __init__(self, t=1000.0):
            self.t = t

        def __call__(self):
            return self.t

    clock = _Clock()
    store = TransferStore(clock=clock)
    client = _client(store, vault)

    unknown = client.get("/transfer/unknown")

    consumed = store.create("download", "note.md", False, 60)
    client.get(f"/transfer/{consumed.token}")  # burn it
    consumed_resp = client.get(f"/transfer/{consumed.token}")

    expired = store.create("download", "note.md", False, 10)
    clock.t = 2000.0
    expired_resp = client.get(f"/transfer/{expired.token}")

    for resp in (unknown, consumed_resp, expired_resp):
        assert resp.status_code == 404
        assert resp.content == unknown.content


def test_upload_vault_not_initialised_503():
    """An unavailable vault on upload yields 503 and releases the token."""
    store = TransferStore()
    rec = store.create("upload", "out.md", False, 60, max_upload_bytes=1000)

    def _raise() -> Vault:
        raise RuntimeError("vault not initialised")

    handler = make_transfer_handler(store, vault_getter=_raise)
    app = Starlette(
        routes=[Route("/transfer/{token}", handler, methods=["GET", "POST", "PUT"])]
    )
    client = TestClient(app, raise_server_exceptions=False)
    assert client.post(f"/transfer/{rec.token}", content=b"x").status_code == 503
    assert store.claim(rec.token, "upload") is not None


def test_kind_confusion_upload_token_via_get_404(vault):
    """An upload token cannot be used for download."""
    store = TransferStore()
    rec = store.create("upload", "out.md", False, 60, max_upload_bytes=1000)
    resp = _client(store, vault).get(f"/transfer/{rec.token}")
    assert resp.status_code == 404


def test_upload_invalid_utf8_to_note_415(vault):
    """Non-UTF-8 bytes to a note destination yield 415 and free the token."""
    store = TransferStore()
    rec = store.create("upload", "out.md", False, 60, max_upload_bytes=1000)
    client = _client(store, vault)
    resp = client.post(f"/transfer/{rec.token}", content=b"\xff\xfe\xfd")
    assert resp.status_code == 415
    assert store.claim(rec.token, "upload") is not None


def test_large_attachment_round_trips_uncapped(vault):
    """A >1 MB attachment (over the old context cap) transfers both ways.

    Out-of-band transfer is gated only by the per-link size cap, never by the
    vault's context-size cap (relocated to the read/write tools in #634).
    """
    big = b"\x89PNG\r\n" + b"x" * (2 * 1024 * 1024)
    store = TransferStore()
    up = store.create("upload", "big.png", True, 60, max_upload_bytes=10 * 1024 * 1024)
    client = _client(store, vault)
    assert client.post(f"/transfer/{up.token}", content=big).status_code == 201
    down = store.create("download", "big.png", True, 60)
    resp = client.get(f"/transfer/{down.token}")
    assert resp.status_code == 200
    assert resp.content == big
