"""Receipt ordering, persistence and partial-failure outcomes of okf_verify.

A local review receipt is evidence that this server recorded a review, so it
is written only after the guarded note write succeeds, and a failure on
either side is reported as what actually happened.
"""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from markdown_vault_mcp._okf_receipts import ReceiptNotRecordedError, ReceiptStore
from markdown_vault_mcp._okf_review import concept_snapshot
from markdown_vault_mcp.config_sections.vault_settings import VaultSettings
from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    StoreUnsupportedSchemaError,
)
from markdown_vault_mcp.vault import Vault
from tests.server_factory import make_server

from .conftest import wait_for_mcp_writer_drain

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_NOTE = "---\ntype: concept\n---\nBody\n"


def _rows(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["rows"]


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text(_NOTE, encoding="utf-8")
    (root / "index.md").write_text(
        '---\nokf_version: "0.2"\n---\n# Bundle\n', encoding="utf-8"
    )
    return root


@pytest.fixture
def vault(vault_root: Path, tmp_path: Path) -> Iterator[Vault]:
    instance = Vault(
        source_dir=vault_root,
        settings=VaultSettings(read_only=False, state_path=tmp_path / "state"),
    )
    try:
        yield instance
    finally:
        instance.close()


def _receipts(tmp_path: Path) -> Path:
    return tmp_path / "state.receipts.json"


class TestReceiptStore:
    def test_two_stores_on_one_path_retain_both_receipts(self, tmp_path: Path) -> None:
        path = tmp_path / "receipts.json"
        first, second = (
            ReceiptStore(path, tmp_path / "v"),
            ReceiptStore(path, tmp_path / "v"),
        )
        loaded = first._load
        other: list[threading.Thread] = []

        def load_then_race() -> list[dict[str, Any]]:
            rows = loaded()
            if not other:
                thread = threading.Thread(target=second.append, args=({"id": "b"},))
                other.append(thread)
                thread.start()
                # A shared path lock keeps the second append waiting here.
                thread.join(timeout=0.5)
            return rows

        first._load = load_then_race  # type: ignore[method-assign]
        first.append({"id": "a"})
        other[0].join()
        assert {row["id"] for row in _rows(path)} == {"a", "b"}

    def test_concurrent_appends_use_unique_temporaries(self, tmp_path: Path) -> None:
        path = tmp_path / "receipts.json"
        stores = [ReceiptStore(path, tmp_path / "v") for _ in range(4)]
        errors: list[BaseException] = []

        def run(store: ReceiptStore, name: int) -> None:
            try:
                for index in range(25):
                    store.append({"id": f"{name}-{index}"})
            except BaseException as exc:  # pragma: no cover - failure detail
                errors.append(exc)

        threads = [
            threading.Thread(target=run, args=(store, n))
            for n, store in enumerate(stores)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert len(_rows(path)) == 100
        assert sorted(p.name for p in tmp_path.iterdir()) == ["receipts.json"]

    def test_reload_appends_to_existing_store(self, tmp_path: Path) -> None:
        path = tmp_path / "receipts.json"
        ReceiptStore(path, tmp_path / "v").append({"id": "a"})
        ReceiptStore(path, tmp_path / "v").append({"id": "b"})
        assert [row["id"] for row in _rows(path)] == ["a", "b"]

    @pytest.mark.parametrize(
        "raw",
        [
            b"\xff\xfe not utf-8",
            b"not json",
            b'{"version": 2, "rows": []}',
            b'{"version": 1, "rows": {}}',
            b'{"version": 1, "rows": ["row"]}',
            b"[]",
        ],
    )
    def test_corrupt_state_is_refused(self, tmp_path: Path, raw: bytes) -> None:
        path = tmp_path / "receipts.json"
        path.write_bytes(raw)
        store = ReceiptStore(path, tmp_path / "v")
        with pytest.raises(StoreUnsupportedSchemaError):
            store.check()
        with pytest.raises(StoreUnsupportedSchemaError):
            store.append({"id": "a"})
        assert path.read_bytes() == raw


class TestWriterOrdering:
    def test_stale_guarded_write_records_no_receipt(
        self, vault: Vault, vault_root: Path, tmp_path: Path
    ) -> None:
        snapshot = concept_snapshot(_NOTE)
        write = vault.writer.write

        def race(path: str, text: str, **kwargs: Any) -> Any:
            (vault_root / "note.md").write_text(_NOTE + "edit\n", encoding="utf-8")
            return write(path, text, **kwargs)

        vault.writer.write = race  # type: ignore[method-assign]
        with pytest.raises(ConcurrentModificationError):
            vault.writer.okf_verify("note.md", "alice", snapshot)
        assert "verified" not in (vault_root / "note.md").read_text(encoding="utf-8")
        assert not _receipts(tmp_path).exists()

    def test_failed_note_write_records_no_receipt(
        self, vault: Vault, vault_root: Path, tmp_path: Path
    ) -> None:
        snapshot = concept_snapshot(_NOTE)
        vault_root.chmod(0o555)
        try:
            with pytest.raises(OSError):
                vault.writer.okf_verify("note.md", "alice", snapshot)
        finally:
            vault_root.chmod(0o755)
        assert (vault_root / "note.md").read_text(encoding="utf-8") == _NOTE
        assert not _receipts(tmp_path).exists()

    def test_receipt_failure_after_note_write_is_distinct(
        self, vault: Vault, vault_root: Path
    ) -> None:
        snapshot = concept_snapshot(_NOTE)
        store = vault.writer._receipt_store
        assert store is not None

        def fail(_receipt: dict[str, Any]) -> None:
            raise OSError("disk full")

        store.append = fail  # type: ignore[method-assign]
        with pytest.raises(ReceiptNotRecordedError, match=r"note\.md"):
            vault.writer.okf_verify("note.md", "alice", snapshot)
        assert "human:alice" in (vault_root / "note.md").read_text(encoding="utf-8")

    def test_success_writes_note_then_receipt(
        self, vault: Vault, vault_root: Path, tmp_path: Path
    ) -> None:
        snapshot = concept_snapshot(_NOTE)
        result = vault.writer.okf_verify("note.md", "alice", snapshot)
        assert result == {
            "path": "note.md",
            "verifier": "human:alice",
            "verified_count": 1,
        }
        (row,) = _rows(_receipts(tmp_path))
        assert row["snapshot"] == snapshot and row["by"] == "human:alice"
        assert concept_snapshot((vault_root / "note.md").read_text("utf-8")) == snapshot


@pytest.fixture
def _env(
    _clear_env: None,
    vault_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_root))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_VERIFY", "elicit")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tmp_path / "state"))


async def _confirm(*_args: Any) -> bool:
    return True


async def _verify(snapshot: dict[str, str]) -> None:
    async with Client(make_server(), mode="legacy", elicitation_handler=_confirm) as c:
        await wait_for_mcp_writer_drain(c)
        await c.call_tool(
            "okf_verify", {"path": "note.md", "reviewed_snapshot": snapshot}
        )


@pytest.mark.usefixtures("_env")
class TestToolOutcomes:
    async def test_note_write_oserror_names_note_and_asks_reread(
        self, vault_root: Path, tmp_path: Path
    ) -> None:
        vault_root.chmod(0o555)
        try:
            with pytest.raises(ToolError) as caught:
                await _verify(concept_snapshot(_NOTE))
        finally:
            vault_root.chmod(0o755)
        message = str(caught.value)
        assert "note.md" in message and "Read the note again" in message
        assert "nothing was written" not in message and "unavailable" not in message
        assert not _receipts(tmp_path).exists()

    async def test_callback_oserror_after_replace_is_not_nothing_written(
        self, vault_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from markdown_vault_mcp.managers._write_notifier import WriteNotifier

        def fail(*_args: Any, **_kwargs: Any) -> None:
            raise OSError("callback failed")

        monkeypatch.setattr(WriteNotifier, "fire", fail)
        with pytest.raises(ToolError) as caught:
            await _verify(concept_snapshot(_NOTE))
        message = str(caught.value)
        assert "nothing was written" not in message and "unavailable" not in message
        assert "Read the note again" in message
        assert "human:local" in (vault_root / "note.md").read_text(encoding="utf-8")
        assert not _receipts(tmp_path).exists()

    async def test_receipt_failure_after_note_write_says_note_written(
        self, vault_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(*_args: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(ReceiptStore, "append", fail)
        with pytest.raises(ToolError) as caught:
            await _verify(concept_snapshot(_NOTE))
        message = str(caught.value)
        assert "recorded in note 'note.md'" in message
        assert "no local receipt" in message and "nothing was written" not in message
        assert "human:local" in (vault_root / "note.md").read_text(encoding="utf-8")

    async def test_unreadable_receipt_state_refused_before_prompt(
        self, tmp_path: Path, vault_root: Path
    ) -> None:
        _receipts(tmp_path).mkdir()
        with pytest.raises(ToolError, match="nothing was written"):
            await _verify(concept_snapshot(_NOTE))
        assert (vault_root / "note.md").read_text(encoding="utf-8") == _NOTE

    async def test_receipt_state_corrupted_during_confirmation(
        self, tmp_path: Path, vault_root: Path
    ) -> None:
        async def corrupt(*_args: Any) -> bool:
            _receipts(tmp_path).write_text("not json", encoding="utf-8")
            return True

        async with Client(
            make_server(), mode="legacy", elicitation_handler=corrupt
        ) as client:
            with pytest.raises(ToolError, match=r"corrupt.*nothing was written"):
                await client.call_tool(
                    "okf_verify",
                    {"path": "note.md", "reviewed_snapshot": concept_snapshot(_NOTE)},
                )
        assert (vault_root / "note.md").read_text(encoding="utf-8") == _NOTE


def test_facet_without_receipt_store_refuses(vault: Vault) -> None:
    from markdown_vault_mcp.exceptions import InvalidRequestError
    from markdown_vault_mcp.facets.writer import WriterFacet

    facet = WriterFacet(vault._doc_mgr)
    with pytest.raises(InvalidRequestError, match="not configured"):
        facet.require_reviewable("note.md", concept_snapshot(_NOTE))
