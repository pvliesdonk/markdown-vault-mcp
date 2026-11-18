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

import logging
from datetime import date
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import frontmatter as fm
import pytest

from markdown_vault_mcp._okf_convention import ConventionMaintainer
from markdown_vault_mcp._okf_write import okf_write_suppressed
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import wait_for_writer_drain
from tests.fixtures.git import make_superproject

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
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

    def generate_index(
        self, *, folder: str = "", designated: Callable[[str], bool] | None = None
    ) -> None:
        del designated
        if self.raise_on_index:
            raise RuntimeError("boom")
        self.index_calls.append(folder)


class _FakeDoc:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []
        self.write_kwargs: list[dict[str, object]] = []
        self.replacements: list[tuple[str, str | None, str]] = []
        self.raise_on_write = False
        #: Stands in for an existing log.md: ``(body, frontmatter)`` or None.
        self.existing: tuple[str, dict[str, Any]] | None = None

    def in_submodule(self, _path: str) -> bool:
        "Return false for a document manager without a git guard."
        return False

    def read(self, _path: str) -> Any:
        """Model ``DocumentManager.read``: ``content`` is the *whole file*.

        The frontmatter block is serialised back into ``content`` exactly as
        the real read hands it back. A double that returned the body alone is
        what let #1391 stay green: the maintainer fed the block back to a
        ``write(frontmatter=...)``, which stacked a second one.
        """
        if self.existing is None:
            return None
        body, metadata = self.existing
        raw = fm.dumps(fm.Post(body, **metadata)) if metadata else body
        return type("N", (), {"content": raw, "frontmatter": metadata})()

    def replace_body(self, path: str, original: str | None, body: str) -> bool:
        self.replacements.append((path, original, body))
        self.write(path, body)
        return original is not None and original.startswith("---")

    def write(self, path: str, content: str, **kw: object) -> None:
        if self.raise_on_write:
            raise RuntimeError("disk full")
        self.writes.append((path, content))
        self.write_kwargs.append(kw)


def _maintainer(
    *,
    active: bool = True,
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


def _maintain(m: ConventionMaintainer, path: str) -> None:
    m.maintain(path, "write")


def _refresh(m: ConventionMaintainer, path: str) -> None:
    m.refresh_indexes(["guides"], trigger_paths=[path])


class TestMaintainerNativePathGuard:
    """The reserved-file guard keeps the caller's native ``Path`` parsing (#1268).

    No Windows host runs this suite, so ``PureWindowsPath`` stands in for the
    module's ``Path`` to model a native backslash path a Windows caller accepts.
    """

    @pytest.mark.parametrize("entry", [_maintain, _refresh])
    @pytest.mark.parametrize("path", [r"guides\index.md", r"guides\log.md"])
    def test_windows_reserved_path_triggers_nothing(
        self, monkeypatch: pytest.MonkeyPatch, entry: Any, path: str
    ) -> None:
        monkeypatch.setattr("markdown_vault_mcp._okf_convention.Path", PureWindowsPath)
        m, doc, migrate = _maintainer()
        entry(m, path)
        assert doc.writes == [] and migrate.index_calls == []

    @pytest.mark.parametrize("entry", [_maintain, _refresh])
    def test_windows_note_path_still_triggers(
        self, monkeypatch: pytest.MonkeyPatch, entry: Any
    ) -> None:
        monkeypatch.setattr("markdown_vault_mcp._okf_convention.Path", PureWindowsPath)
        m, doc, migrate = _maintainer()
        entry(m, r"guides\note.md")
        assert doc.writes or migrate.index_calls


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


class TestMaintainedLogFrontmatter:
    """Maintained logs retain their existing header and never add one."""

    def test_existing_header_is_retained(self) -> None:
        m, doc, _ = _maintainer()
        doc.existing = ("# Log\n", {"title": "Change history", "type": "log"})
        m.maintain("note.md", "write")
        path, original, body = doc.replacements[0]
        assert path == "log.md"
        assert original == fm.dumps(
            fm.Post("# Log\n", title="Change history", type="log")
        )
        assert not body.startswith("---")


# ---------------------------------------------------------------------------
# Integration through a writable, OKF-active vault
# ---------------------------------------------------------------------------

_ROOT_INDEX = '---\nokf_version: "0.2"\ntitle: Root\n---\n# Root\n'


def _build_vault(
    source: Path,
    *,
    okf_write: bool,
    okf_mode: str = "on",
    required_frontmatter: list[str] | None = None,
) -> Vault:
    col = Vault(
        source_dir=source,
        settings=VaultSettings(
            read_only=False,
            okf_mode=okf_mode,
            okf_write=okf_write,
            required_frontmatter=required_frontmatter,
        ),
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
            settings=VaultSettings(
                read_only=False,
                write_protect_existing=True,
                okf_mode="on",
                okf_write=True,
            ),
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

    def test_editing_a_reserved_file_does_not_recurse(
        self, enforced_vault: Vault
    ) -> None:
        # A direct write to a reserved file must not itself trigger maintenance
        # (which would append a log entry about the log). Seed a note first.
        enforced_vault.writer.write("guides/note.md", "# N\n")
        wait_for_writer_drain(enforced_vault)
        log_before = _content(enforced_vault, "guides/log.md")
        enforced_vault.writer.write(
            "guides/index.md", "# Manual index\n", allow_protected=True
        )
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


class TestUpkeepUnderRequiredFrontmatter:
    """Generated navigation and history remain indexed without concept fields."""

    def test_maintained_reserved_files_are_indexed(self, tmp_path: Path) -> None:
        root = tmp_path / "gated_vault"
        (root / "guides").mkdir(parents=True)
        (root / "index.md").write_text(_ROOT_INDEX, encoding="utf-8")
        col = _build_vault(root, okf_write=True, required_frontmatter=["title"])
        try:
            col.writer.write("guides/playbook.md", "---\ntitle: P\n---\n# P\n")
            wait_for_writer_drain(col)

            assert not (root / "guides" / "log.md").read_text().startswith("---")
        finally:
            col.close()

    @pytest.mark.parametrize(
        "header", [b"---\r\ntitle: 'Legacy' # retained\r\n---\r\n", b"---\n---\n"]
    )
    def test_log_upkeep_preserves_header_bytes(
        self, tmp_path: Path, header: bytes
    ) -> None:
        """Retain headers without stacking another block (#1391)."""
        root = tmp_path / "header_bytes"
        (root / "guides").mkdir(parents=True)
        (root / "index.md").write_text(_ROOT_INDEX, encoding="utf-8")
        log = root / "guides" / "log.md"
        log.write_bytes(header + b"# Log\r\n")
        col = _build_vault(root, okf_write=True)
        try:
            col.writer.write("guides/a.md", "---\ntitle: A\n---\n# A\n")
            wait_for_writer_drain(col)
            assert log.read_bytes().startswith(header)
            assert log.read_bytes().count(b"---") == 2
        finally:
            col.close()

    def test_repeated_writes_keep_the_log_body_only(self, tmp_path: Path) -> None:
        """Repeated writes retain all entries in a body-only log."""
        root = tmp_path / "gated_vault_thrice"
        (root / "guides").mkdir(parents=True)
        (root / "index.md").write_text(_ROOT_INDEX, encoding="utf-8")
        col = _build_vault(root, okf_write=True, required_frontmatter=["title"])
        try:
            for name in ("a", "b", "c"):
                col.writer.write(
                    f"guides/{name}.md", f"---\ntitle: {name}\n---\n# {name}\n"
                )
                wait_for_writer_drain(col)

            text = (root / "guides" / "log.md").read_text(encoding="utf-8")
            assert not text.startswith("---")
            assert text.count("- **Update**:") == 3
        finally:
            col.close()


def test_unreadable_folder_log_is_not_overwritten(enforced_vault: Vault) -> None:
    """Log upkeep never takes an unreadable log.md for an absent one (#1608)."""
    log = enforced_vault.source_dir / "guides" / "log.md"
    unreadable = b"# caf\xe9 log\n"
    log.write_bytes(unreadable)
    enforced_vault.writer.write("guides/playbook.md", "# Playbook\n\nSteps.\n")
    wait_for_writer_drain(enforced_vault)
    assert log.read_bytes() == unreadable


# ---------------------------------------------------------------------------
# #1609: delete / rename / move_folder refresh the affected index.md files
# ---------------------------------------------------------------------------


def _index_links_broken(vault: Vault) -> list[str]:
    """Broken links whose source is a generated index.md."""
    return [
        f"{b.source_path} -> {b.target_path}"
        for b in vault.graph.get_broken_links()
        if b.source_path.endswith("index.md")
    ]


class TestIndexRefreshOnStructuralChanges:
    def test_move_folder_forwards_maintainer_folders(
        self, enforced_vault: Vault, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        enforced_vault.writer.write("guides/sub/child/a.md", "# A\n\nx\n")
        wait_for_writer_drain(enforced_vault)
        maintainer = enforced_vault.writer._convention_maintainer
        assert maintainer is not None
        folders = ["b", "b/child"]
        walk = Mock(return_value=folders)
        refresh = Mock()
        monkeypatch.setattr(maintainer, "subtree_folders", walk)
        monkeypatch.setattr(maintainer, "refresh_indexes", refresh)

        enforced_vault.writer.move_folder("guides/sub", "b", allow_protected=True)

        walk.assert_called_once_with("b")
        refresh.assert_called_once_with(
            [*folders, "guides"],
            trigger_paths=["guides/sub", "b"],
            create=[""],
        )

    def test_delete_drops_the_note_from_its_folder_index(
        self, enforced_vault: Vault
    ) -> None:
        enforced_vault.writer.write("guides/a.md", "# A\n\nx\n")
        enforced_vault.writer.write("guides/b.md", "# B\n\ny\n")
        enforced_vault.writer.delete("guides/a.md")
        wait_for_writer_drain(enforced_vault)
        index = _content(enforced_vault, "guides/index.md") or ""
        assert "/guides/a.md" not in index
        assert "/guides/b.md" in index
        assert _index_links_broken(enforced_vault) == []

    def test_rename_across_folders_updates_both_indexes(
        self, enforced_vault: Vault
    ) -> None:
        enforced_vault.writer.write("guides/a.md", "# A\n\nx\n")
        enforced_vault.writer.write("other/keep.md", "# Keep\n\nk\n")
        enforced_vault.writer.rename("guides/a.md", "other/a.md")
        wait_for_writer_drain(enforced_vault)
        assert "/guides/a.md" not in (_content(enforced_vault, "guides/index.md") or "")
        assert "/other/a.md" in (_content(enforced_vault, "other/index.md") or "")
        assert _index_links_broken(enforced_vault) == []

    def test_move_folder_refreshes_the_moved_subtree_and_both_parents(
        self, enforced_vault: Vault
    ) -> None:
        enforced_vault.writer.write("guides/sub/a.md", "# A\n\nx\n")
        enforced_vault.writer.write("guides/top.md", "# Top\n\nt\n")
        enforced_vault.writer.write("archive/old.md", "# Old\n\no\n")
        enforced_vault.writer.move_folder(
            "guides/sub", "archive/sub", allow_protected=True
        )
        wait_for_writer_drain(enforced_vault)
        moved = _content(enforced_vault, "archive/sub/index.md") or ""
        assert "/archive/sub/a.md" in moved
        assert "/guides/sub/" not in moved
        assert "sub/index.md" not in (_content(enforced_vault, "guides/index.md") or "")
        assert "sub/index.md" in (_content(enforced_vault, "archive/index.md") or "")
        assert _index_links_broken(enforced_vault) == []


class TestRefreshIndexesGuards:
    def _maintainer(
        self, *, active: bool = True
    ) -> tuple[ConventionMaintainer, _FakeMigrate]:
        migrate = _FakeMigrate()
        maintainer = ConventionMaintainer(
            doc_mgr=_FakeDoc(),  # type: ignore[arg-type]
            okf_migrate=migrate,  # type: ignore[arg-type]
            detector=_FakeDetector(active),  # type: ignore[arg-type]
        )
        return maintainer, migrate

    def test_each_folder_is_refreshed_once_in_order(self) -> None:
        maintainer, migrate = self._maintainer()
        maintainer.refresh_indexes(["b", "a", "b", ""], trigger_paths=["b/x.md"])
        assert migrate.index_calls == ["b", "a", ""]

    def test_inactive_vault_is_a_noop(self) -> None:
        maintainer, migrate = self._maintainer(active=False)
        maintainer.refresh_indexes(["a"], trigger_paths=["a/x.md"])
        assert migrate.index_calls == []

    def test_suppressed_change_is_a_noop(self) -> None:
        maintainer, migrate = self._maintainer()
        with okf_write_suppressed():
            maintainer.refresh_indexes(["a"], trigger_paths=["a/x.md"])
        assert migrate.index_calls == []

    def test_a_reserved_file_change_triggers_nothing(self) -> None:
        """A reserved-file change never triggers its own regeneration (#1414)."""
        maintainer, migrate = self._maintainer()
        maintainer.refresh_indexes(["a"], trigger_paths=["a/index.md"])
        assert migrate.index_calls == []

    def test_one_failure_does_not_stop_the_others(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        maintainer, migrate = self._maintainer()
        migrate.raise_on_index = True
        maintainer.refresh_indexes(["a", "b"], trigger_paths=["a/x.md"])
        assert migrate.index_calls == []  # both attempted, both raised, none raised out
        failures = [
            r for r in caplog.records if r.msg.startswith("okf_convention_index_failed")
        ]
        assert len(failures) == 2
        assert all(r.levelno == logging.WARNING and r.exc_info for r in failures)


class TestMaintainerFolderWalks:
    @pytest.mark.parametrize(
        ("root", "expected"),
        [
            ("b", ["b", "b/alpha", "b/subway", "b/subway/deep"]),
            ("b/sub", []),
            ("b/sub/deeper", []),
            ("", ["", "b", "b/alpha", "b/subway", "b/subway/deep"]),
        ],
    )
    def test_subtree_folders_omits_submodules(
        self, tmp_path: Path, root: str, expected: list[str]
    ) -> None:
        source = make_superproject(tmp_path, sub="b/sub")
        for rel in ("b/sub/deeper", "b/subway/deep", "b/alpha", "b/.hidden/child"):
            (source / rel).mkdir(parents=True, exist_ok=True)
        vault = Vault(
            source_dir=source,
            settings=VaultSettings(read_only=False),
            git_strategy=GitWriteStrategy(
                enable_pull=False, enable_push=False, git_lfs=False, repo_path=source
            ),
        )
        try:
            maintainer = ConventionMaintainer(
                doc_mgr=vault._doc_mgr,
                okf_migrate=_FakeMigrate(),  # type: ignore[arg-type]
                detector=_FakeDetector(True),  # type: ignore[arg-type]
                source_dir=source,
            )
            assert maintainer.subtree_folders(root) == expected
        finally:
            vault.close()

    def _maintainer(self, source_dir: Path | None) -> ConventionMaintainer:
        return ConventionMaintainer(
            doc_mgr=_FakeDoc(),  # type: ignore[arg-type]
            okf_migrate=_FakeMigrate(),  # type: ignore[arg-type]
            detector=_FakeDetector(True),  # type: ignore[arg-type]
            source_dir=source_dir,
        )

    def test_subtree_folders_skips_hidden_folders(self, tmp_path: Path) -> None:
        for rel in ("a/b/c", "a/.git/objects", "a/d"):
            (tmp_path / rel).mkdir(parents=True)
        assert self._maintainer(tmp_path).subtree_folders("a") == [
            "a",
            "a/b",
            "a/d",
            "a/b/c",
        ]

    def test_without_a_vault_directory_only_the_root_is_known(self) -> None:
        maintainer = self._maintainer(None)
        assert maintainer.subtree_folders("a") == ["a"]
        assert maintainer.indexed_anchor("a/b") == "a/b"

    def test_indexed_anchor_is_the_nearest_indexed_level(self, tmp_path: Path) -> None:
        (tmp_path / "a" / "b" / "c").mkdir(parents=True)
        (tmp_path / "a" / "index.md").write_text("# a\n", encoding="utf-8")
        maintainer = self._maintainer(tmp_path)
        assert maintainer.indexed_anchor("a/b/c") == "a"
        assert maintainer.indexed_anchor("x/y") == ""

    def test_a_refused_stat_counts_as_indexed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The refresh is attempted and logs its own failure (#1625)."""

        def refuse(path: Path) -> bool:
            raise PermissionError(path)

        monkeypatch.setattr(
            "markdown_vault_mcp._okf_convention.is_regular_file", refuse
        )
        assert self._maintainer(tmp_path).indexed_anchor("a/b") == "a/b"


class TestIndexRefreshEdgeCases:
    def test_move_reserved_named_folders_preserves_handwritten_index(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "vault"
        (root / "index.md").mkdir(parents=True)
        (root / "index.md/note.md").write_text("# Note\n", encoding="utf-8")
        (root / "index.md/index.md").write_text(
            "# Precious index\n\nUnique hand-written content.\n", encoding="utf-8"
        )
        vault = Vault(
            source_dir=root,
            settings=VaultSettings(read_only=False, okf_mode="on", okf_write=True),
        )
        vault.index.build_index()
        try:
            vault.writer.move_folder("index.md/", "log.md/", allow_protected=True)
            wait_for_writer_drain(vault)
            assert (root / "log.md/note.md").is_file()
            assert "Unique hand-written content." in (
                root / "log.md/index.md"
            ).read_text(encoding="utf-8")
        finally:
            vault.close()

    def test_renaming_a_note_onto_a_reserved_name_keeps_its_body(
        self, enforced_vault: Vault
    ) -> None:
        """No regeneration may overwrite the file the rename just placed."""
        enforced_vault.writer.write("guides/a.md", "# A\n\nbody A precious\n")
        enforced_vault.writer.rename(
            "guides/a.md", "newf/index.md", allow_protected=True
        )
        wait_for_writer_drain(enforced_vault)
        assert "body A precious" in (_content(enforced_vault, "newf/index.md") or "")

    def test_writing_into_a_new_nested_folder_lists_every_level(
        self, enforced_vault: Vault
    ) -> None:
        enforced_vault.writer.write("new/sub/a.md", "# A\n\nx\n")
        wait_for_writer_drain(enforced_vault)
        assert "(/new/index.md)" in (_content(enforced_vault, "index.md") or "")
        assert "(/new/sub/index.md)" in (_content(enforced_vault, "new/index.md") or "")
        assert "/new/sub/a.md" in (_content(enforced_vault, "new/sub/index.md") or "")
        assert _index_links_broken(enforced_vault) == []

    def test_renaming_a_reserved_file_triggers_nothing(
        self, enforced_vault: Vault
    ) -> None:
        """A reserved-file change never triggers its own regeneration (#1414)."""
        enforced_vault.writer.write("guides/a.md", "# A\n\nx\n")
        enforced_vault.writer.rename(
            "guides/index.md", "guides/was-index.md", allow_protected=True
        )
        wait_for_writer_drain(enforced_vault)
        assert _content(enforced_vault, "guides/index.md") is None

    def test_rename_into_a_nested_new_folder_leaves_no_broken_pointer(
        self, enforced_vault: Vault
    ) -> None:
        enforced_vault.writer.write("guides/a.md", "# A\n\nx\n")
        enforced_vault.writer.rename("guides/a.md", "deep/er/a.md")
        wait_for_writer_drain(enforced_vault)
        assert "/deep/er/a.md" in (_content(enforced_vault, "deep/er/index.md") or "")
        assert "(/deep/index.md)" in (_content(enforced_vault, "index.md") or "")
        assert _index_links_broken(enforced_vault) == []

    def test_move_folder_into_a_nested_new_parent_leaves_no_broken_pointer(
        self, enforced_vault: Vault
    ) -> None:
        enforced_vault.writer.write("guides/sub/a.md", "# A\n\nx\n")
        enforced_vault.writer.move_folder("guides/sub", "top/sub", allow_protected=True)
        wait_for_writer_drain(enforced_vault)
        assert "(/top/index.md)" in (_content(enforced_vault, "index.md") or "")
        top = _content(enforced_vault, "top/index.md") or ""
        assert "(/top/sub/index.md)" in top
        assert _index_links_broken(enforced_vault) == []
