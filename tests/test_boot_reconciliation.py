"""Boot-time reconciliation of offline changes (#665).

A warm restart short-circuits ``build_index_async()`` in O(1) via the FTS
sentinel (#526), so nothing scans the filesystem at boot.  The lifespan now
also submits an incremental ``reindex_async()`` job behind the build: files
added, modified, or deleted while no server was running are reconciled as
soon as the writer drains, instead of staying invisible until an unrelated
watcher event, git pull, or manual reindex.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from fastmcp import Client

from markdown_vault_mcp.vault import Vault
from tests.conftest import _parse_tool_data, wait_for_mcp_writer_drain

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _make_vault_dir(tmp_path: Path, n_docs: int = 3) -> Path:
    """Create a vault directory with *n_docs* indexable notes."""
    vault = tmp_path / "vault"
    vault.mkdir()
    for i in range(n_docs):
        (vault / f"note_{i}.md").write_text(
            f"# Note {i}\n\nOriginal body {i}.\n", encoding="utf-8"
        )
    return vault


def _set_env(monkeypatch: pytest.MonkeyPatch, vault: Path, tmp_path: Path) -> None:
    """Point the server env at *vault* with persistent index + state files."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(tmp_path / "fts.db"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tmp_path / "s.json"))


def _prebuild(vault: Path, tmp_path: Path) -> int:
    """Simulate a previous server run: full build into the persistent index."""
    pre = Vault(
        source_dir=vault,
        index_path=tmp_path / "fts.db",
        state_path=tmp_path / "s.json",
    )
    pre.index.build_index()
    count = len(pre._fts.list_notes())
    pre.close()
    return count


class TestWarmBootReconciliation:
    """Warm boots must apply the offline delta after startup settles."""

    def test_offline_add_modify_delete_reconciled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from markdown_vault_mcp.server import make_server

        vault = _make_vault_dir(tmp_path, n_docs=3)
        _prebuild(vault, tmp_path)

        # Offline changes while no server runs.
        (vault / "offline_added.md").write_text(
            "# Offline\n\nWritten while the server was down.\n", encoding="utf-8"
        )
        (vault / "note_1.md").write_text(
            "# Note 1\n\nModified offline body.\n", encoding="utf-8"
        )
        (vault / "note_2.md").unlink()

        _set_env(monkeypatch, vault, tmp_path)
        server = make_server()

        async def _run() -> tuple[dict[str, Any], list[str], list[str]]:
            async with Client(server) as client:
                await wait_for_mcp_writer_drain(client)
                status_res = await client.call_tool("get_index_status", {})
                added = await client.call_tool("search", {"query": "Offline"})
                modified = await client.call_tool(
                    "search", {"query": "Modified offline body"}
                )
                return (
                    status_res.structured_content or {},
                    [r["path"] for r in _parse_tool_data(added)],
                    [r["path"] for r in _parse_tool_data(modified)],
                )

        status, added_paths, modified_paths = asyncio.run(_run())
        # 3 originals - 1 deleted + 1 added = 3 documents.
        assert status["documents_indexed"] == 3
        assert "offline_added.md" in added_paths
        assert "note_1.md" in modified_paths

    def test_unchanged_vault_zero_reupserts_and_zero_reparse(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Warm boot of an unchanged vault applies zero upserts and re-parses
        nothing — including files skipped for missing required frontmatter."""
        from unittest.mock import patch

        import markdown_vault_mcp.managers.embeddings as embeddings_module
        import markdown_vault_mcp.managers.index as index_module
        from markdown_vault_mcp.fts_index import FTSIndex
        from markdown_vault_mcp.server import make_server

        vault = _make_vault_dir(tmp_path, n_docs=3)
        (vault / "no_frontmatter.md").write_text(
            "# Skipped\n\nIntentionally lacks required frontmatter.\n",
            encoding="utf-8",
        )
        for i in range(3):
            (vault / f"note_{i}.md").write_text(
                f"---\ntitle: Note {i}\n---\n# Note {i}\n\nbody {i}\n",
                encoding="utf-8",
            )
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_REQUIRED_FIELDS", "title")

        pre = Vault(
            source_dir=vault,
            index_path=tmp_path / "fts.db",
            state_path=tmp_path / "s.json",
            required_frontmatter=["title"],
        )
        pre.index.build_index()
        pre.close()

        _set_env(monkeypatch, vault, tmp_path)
        server = make_server()

        upsert_paths: list[str] = []
        original_upsert = FTSIndex.upsert_note

        def tracking_upsert(self: FTSIndex, note: Any) -> int:
            upsert_paths.append(note.path)
            return original_upsert(self, note)

        parse_calls: list[str] = []
        original_parse = index_module.parse_note

        def tracking_parse(abs_path: Any, source_dir: Any, chunk_strategy: Any):
            parse_calls.append(str(abs_path))
            return original_parse(abs_path, source_dir, chunk_strategy)

        async def _run() -> dict[str, Any]:
            async with Client(server) as client:
                await wait_for_mcp_writer_drain(client)
                res = await client.call_tool("get_index_status", {})
                return res.structured_content or {}

        with (
            patch.object(FTSIndex, "upsert_note", tracking_upsert),
            patch.object(index_module, "parse_note", tracking_parse),
            # The embeddings lifecycle parses through its own module since
            # the #1157 split; cover that path with the same tracker.
            patch.object(embeddings_module, "parse_note", tracking_parse),
        ):
            status = asyncio.run(_run())

        assert status["documents_indexed"] == 3
        assert upsert_paths == [], f"warm boot re-upserted {upsert_paths}"
        assert parse_calls == [], f"warm boot re-parsed {parse_calls}"

    def test_warm_boot_lifespan_submits_boot_reindex(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The lifespan logs the boot Reindex submission after BuildIndex."""
        import logging

        from markdown_vault_mcp.server import make_server

        vault = _make_vault_dir(tmp_path, n_docs=1)
        _prebuild(vault, tmp_path)
        _set_env(monkeypatch, vault, tmp_path)
        server = make_server()
        caplog.set_level(logging.INFO)

        async def _run() -> None:
            async with Client(server) as client:
                await wait_for_mcp_writer_drain(client)

        asyncio.run(_run())
        messages = [record.message for record in caplog.records]
        build_idx = next(
            i for i, m in enumerate(messages) if "Submitted BuildIndex job" in m
        )
        reindex_idx = next(
            i for i, m in enumerate(messages) if "Submitted boot Reindex job" in m
        )
        assert build_idx < reindex_idx, "reindex must be enqueued after the build"


class TestColdBootReconciliation:
    """Cold boots must not pay for the reconciliation pass twice."""

    def test_cold_boot_no_double_full_scan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The boot reindex after a cold build upserts/parses nothing extra.

        The full build records tracker state (including skipped files), so
        the queued reindex job degenerates to a hash scan: every path is
        upserted exactly once, and the skipped file is parsed exactly once
        (by scan_directory during the build).
        """
        from unittest.mock import patch

        from markdown_vault_mcp.fts_index import FTSIndex
        from markdown_vault_mcp.server import make_server

        vault = _make_vault_dir(tmp_path, n_docs=4)
        _set_env(monkeypatch, vault, tmp_path)
        server = make_server()

        upsert_paths: list[str] = []
        original_upsert = FTSIndex.upsert_note

        def tracking_upsert(self: FTSIndex, note: Any) -> int:
            upsert_paths.append(note.path)
            return original_upsert(self, note)

        async def _run() -> dict[str, Any]:
            async with Client(server) as client:
                await wait_for_mcp_writer_drain(client)
                res = await client.call_tool("get_index_status", {})
                return res.structured_content or {}

        with patch.object(FTSIndex, "upsert_note", tracking_upsert):
            status = asyncio.run(_run())

        assert status["documents_indexed"] == 4
        assert sorted(upsert_paths) == sorted(f"note_{i}.md" for i in range(4)), (
            f"cold boot upserted each path more than once: {sorted(upsert_paths)}"
        )
        assert status["last_reindex_error"] is None


class TestConcurrentWarmBoots:
    """Concurrent server instances booting against the same index must not
    fail with lock errors (production pattern: several MCP clients spawn
    their own server processes against one shared vault)."""

    def test_two_concurrent_warm_boots_no_lock_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from markdown_vault_mcp.server import make_server

        vault = _make_vault_dir(tmp_path, n_docs=3)
        _prebuild(vault, tmp_path)
        (vault / "offline_added.md").write_text(
            "# Offline\n\nadded while down\n", encoding="utf-8"
        )

        _set_env(monkeypatch, vault, tmp_path)
        server_a = make_server()
        server_b = make_server()

        async def _boot(server: Any) -> dict[str, Any]:
            async with Client(server) as client:
                await wait_for_mcp_writer_drain(client, timeout=15.0)
                res = await client.call_tool("get_index_status", {})
                return res.structured_content or {}

        async def _run() -> tuple[dict[str, Any], dict[str, Any]]:
            return await asyncio.gather(_boot(server_a), _boot(server_b))

        status_a, status_b = asyncio.run(_run())
        for status in (status_a, status_b):
            assert status["status"] == "queryable"
            assert status["last_reindex_error"] is None
            assert status["error"] is None
            assert status["documents_indexed"] == 4
