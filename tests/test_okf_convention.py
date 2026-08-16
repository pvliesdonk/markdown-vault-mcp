"""Tests for OKF enforced-write convention maintenance (#964, design §6 5b).

Two layers:

- Unit tests of :class:`ConventionMaintainer` against fakes, covering the
  guards (operation, suppression, reserved file, inactive) and the
  failure-isolation contract (a secondary-write failure degrades to a WARNING
  and never propagates).
- Integration tests through a writable, OKF-active vault with ``OKF_WRITE`` on,
  covering the real ``log.md`` append and ``index.md`` refresh riding a primary
  write, and that the layer stays off when disabled.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp._okf_convention import ConventionMaintainer
from markdown_vault_mcp._okf_write import okf_write_suppressed
from markdown_vault_mcp.vault import Vault
from tests.conftest import wait_for_writer_drain

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_FIXED = date(2026, 8, 9)


# ---------------------------------------------------------------------------
# Unit: ConventionMaintainer guards + failure isolation (fakes)
# ---------------------------------------------------------------------------


class _FakeDetector:
    def __init__(self, active: bool) -> None:
        self._active = active

    def state(self) -> Any:
        return type("S", (), {"active": self._active})()


class _FakeMigrate:
    def __init__(self) -> None:
        self.index_calls: list[str] = []
        self.raise_on_index = False

    def generate_index(self, *, folder: str = "") -> None:
        if self.raise_on_index:
            raise RuntimeError("boom")
        self.index_calls.append(folder)


class _FakeDoc:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []
        self.raise_on_write = False

    def read(self, _path: str) -> None:
        return None

    def write(self, path: str, content: str, **_kw: object) -> None:
        if self.raise_on_write:
            raise RuntimeError("disk full")
        self.writes.append((path, content))


def _maintainer(
    *, active: bool = True
) -> tuple[ConventionMaintainer, _FakeDoc, _FakeMigrate]:
    doc, migrate = _FakeDoc(), _FakeMigrate()
    m = ConventionMaintainer(
        doc_mgr=doc,  # type: ignore[arg-type]
        okf_migrate=migrate,  # type: ignore[arg-type]
        detector=_FakeDetector(active),  # type: ignore[arg-type]
        sync_index=lambda: None,
        today=lambda: _FIXED,
    )
    return m, doc, migrate


class TestMaintainerGuards:
    def test_write_triggers_log_and_index(self) -> None:
        m, doc, migrate = _maintainer()
        m.maintain("guides/note.md", "write")
        assert doc.writes == [
            (
                "guides/log.md",
                "# Log\n\n## 2026-08-09\n\n- **Update**: wrote `guides/note.md`\n",
            )
        ]
        assert migrate.index_calls == ["guides"]

    def test_edit_uses_edited_verb(self) -> None:
        m, doc, _ = _maintainer()
        m.maintain("note.md", "edit")
        assert "edited `note.md`" in doc.writes[0][1]
        # Root-level note → root log.md.
        assert doc.writes[0][0] == "log.md"

    @pytest.mark.parametrize("operation", ["delete", "rename"])
    def test_non_content_operations_are_noops(self, operation: str) -> None:
        m, doc, migrate = _maintainer()
        m.maintain("note.md", operation)  # type: ignore[arg-type]
        assert doc.writes == [] and migrate.index_calls == []

    @pytest.mark.parametrize("name", ["index.md", "log.md", "guides/log.md"])
    def test_reserved_file_writes_are_noops(self, name: str) -> None:
        m, doc, migrate = _maintainer()
        m.maintain(name, "write")
        assert doc.writes == [] and migrate.index_calls == []

    def test_inactive_vault_is_a_noop(self) -> None:
        m, doc, migrate = _maintainer(active=False)
        m.maintain("note.md", "write")
        assert doc.writes == [] and migrate.index_calls == []

    def test_suppressed_write_is_a_noop(self) -> None:
        m, doc, migrate = _maintainer()
        with okf_write_suppressed():
            m.maintain("note.md", "write")
        assert doc.writes == [] and migrate.index_calls == []


class TestMaintainerFailureIsolation:
    def test_index_failure_does_not_block_log_or_raise(self) -> None:
        m, doc, migrate = _maintainer()
        migrate.raise_on_index = True
        m.maintain("note.md", "write")  # must not raise
        # The log append happened before the index refresh failed.
        assert doc.writes and doc.writes[0][0] == "log.md"

    def test_log_failure_does_not_block_index_or_raise(self) -> None:
        m, doc, migrate = _maintainer()
        doc.raise_on_write = True
        m.maintain("note.md", "write")  # must not raise
        # Log write raised, but the index refresh still ran.
        assert migrate.index_calls == [""]


# ---------------------------------------------------------------------------
# Integration through a writable, OKF-active vault
# ---------------------------------------------------------------------------

_ROOT_INDEX = '---\nokf_version: "0.2"\ntitle: Root\n---\n# Root\n'


def _build_vault(source: Path, *, okf_write: bool, okf_mode: str = "on") -> Vault:
    col = Vault(
        source_dir=source, read_only=False, okf_mode=okf_mode, okf_write=okf_write
    )
    col.index.build_index()
    return col


@pytest.fixture
def enforced_vault(tmp_path: Path) -> Iterator[Vault]:
    root = tmp_path / "vault"
    (root / "guides").mkdir(parents=True)
    (root / "index.md").write_text(_ROOT_INDEX, encoding="utf-8")
    col = _build_vault(root, okf_write=True)
    try:
        yield col
    finally:
        col.close()


def _content(vault: Vault, path: str) -> str | None:
    note = vault.reader.read(path)
    return note.content if note is not None else None


class TestConventionMaintenanceIntegration:
    def test_write_creates_folder_log_and_index(self, enforced_vault: Vault) -> None:
        enforced_vault.writer.write("guides/playbook.md", "# Playbook\n\nSteps.\n")
        wait_for_writer_drain(enforced_vault)
        log = _content(enforced_vault, "guides/log.md")
        assert log is not None
        assert "## " + date.today().isoformat() in log
        assert "wrote `guides/playbook.md`" in log
        index = _content(enforced_vault, "guides/index.md")
        assert index is not None
        # The drain makes the just-written note appear in the refreshed listing.
        assert "[Playbook](/guides/playbook.md)" in index

    def test_log_and_index_upkeep_survive_write_protection(
        self, tmp_path: Path
    ) -> None:
        """Convention upkeep rewrites files it has read, so the guard exempts it."""
        root = tmp_path / "protected_vault"
        (root / "guides").mkdir(parents=True)
        (root / "index.md").write_text(_ROOT_INDEX, encoding="utf-8")
        col = Vault(
            source_dir=root,
            read_only=False,
            write_protect_existing=True,
            okf_mode="on",
            okf_write=True,
        )
        col.index.build_index()
        try:
            col.writer.write("guides/a.md", "# A\n")
            wait_for_writer_drain(col)
            col.writer.write("guides/b.md", "# B\n")
            wait_for_writer_drain(col)

            log = _content(col, "guides/log.md")
            assert log is not None
            assert log.count("- **Update**:") == 2
        finally:
            col.close()

    def test_second_same_day_write_appends_bullet(self, enforced_vault: Vault) -> None:
        enforced_vault.writer.write("guides/a.md", "# A\n")
        wait_for_writer_drain(enforced_vault)
        enforced_vault.writer.write("guides/b.md", "# B\n")
        wait_for_writer_drain(enforced_vault)
        log = _content(enforced_vault, "guides/log.md")
        assert log is not None
        assert log.count("- **Update**:") == 2
        assert "wrote `guides/a.md`" in log and "wrote `guides/b.md`" in log

    def test_concurrent_writes_do_not_lose_log_entries(
        self, enforced_vault: Vault
    ) -> None:
        # Concurrent writes into the same folder each append to guides/log.md.
        # The shared write lock serialises the read-modify-write, so every
        # bullet survives (no lost update).
        import concurrent.futures

        n = 8

        def _write(i: int) -> None:
            enforced_vault.writer.write(f"guides/n{i}.md", f"# N{i}\n")

        with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
            list(pool.map(_write, range(n)))
        wait_for_writer_drain(enforced_vault)
        log = _content(enforced_vault, "guides/log.md")
        assert log is not None
        assert log.count("- **Update**:") == n
        for i in range(n):
            assert f"wrote `guides/n{i}.md`" in log

    def test_reserved_files_are_not_provenance_stamped(
        self, enforced_vault: Vault
    ) -> None:
        enforced_vault.writer.write("guides/note.md", "# N\n")
        wait_for_writer_drain(enforced_vault)
        # The secondary writes are suppressed: no generated/verified churn.
        log_note = enforced_vault.reader.read("guides/log.md")
        index_note = enforced_vault.reader.read("guides/index.md")
        assert log_note is not None and "generated" not in log_note.frontmatter
        assert index_note is not None and "generated" not in index_note.frontmatter

    def test_editing_a_reserved_file_does_not_recurse(
        self, enforced_vault: Vault
    ) -> None:
        # A direct write to a reserved file must not itself trigger maintenance
        # (which would append a log entry about the log). Seed a note first.
        enforced_vault.writer.write("guides/note.md", "# N\n")
        wait_for_writer_drain(enforced_vault)
        log_before = _content(enforced_vault, "guides/log.md")
        enforced_vault.writer.write("guides/index.md", "# Manual index\n")
        wait_for_writer_drain(enforced_vault)
        # log.md unchanged by the reserved-file write.
        assert _content(enforced_vault, "guides/log.md") == log_before

    def test_disabled_when_okf_write_off(self, tmp_path: Path) -> None:
        root = tmp_path / "v2"
        (root / "guides").mkdir(parents=True)
        (root / "index.md").write_text(_ROOT_INDEX, encoding="utf-8")
        col = _build_vault(root, okf_write=False)
        try:
            col.writer.write("guides/note.md", "# N\n")
            wait_for_writer_drain(col)
            assert col.reader.read("guides/log.md") is None
        finally:
            col.close()

    def test_inactive_bundle_skips_maintenance(self, tmp_path: Path) -> None:
        # OKF_WRITE on but no declaration and mode auto → not active.
        root = tmp_path / "v3"
        (root / "guides").mkdir(parents=True)
        col = _build_vault(root, okf_write=True, okf_mode="auto")
        try:
            col.writer.write("guides/note.md", "# N\n")
            wait_for_writer_drain(col)
            assert col.reader.read("guides/log.md") is None
        finally:
            col.close()
