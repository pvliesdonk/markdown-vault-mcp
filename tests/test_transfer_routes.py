"""Integration tests for the /transfer/{token} route (#622)."""

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
    (vault._source_dir / "note.md").unlink()
    client = _client(store, vault)
    assert client.get(f"/transfer/{rec.token}").status_code == 404
    assert store.claim(rec.token, "download") is not None


def test_download_missing_attachment_404_and_not_consumed(vault):
    """A download whose attachment vanished 404s (ValueError) and isn't burned."""
    store = TransferStore()
    rec = store.create("download", "pic.png", True, 60)
    (vault._source_dir / "pic.png").unlink()
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
