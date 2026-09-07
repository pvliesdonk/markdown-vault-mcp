"""Rebase conflicts on the OKF reserved files (#1395).

Real git, real rebase. When the server maintains ``index.md`` / ``log.md``,
they are projections of vault state rather than notes: a conflict on one is
resolved in place — upstream wins, ``log.md`` keeps both sides' bullets — with
no ``.conflict-mcp-*`` sibling and no ``conflict_with`` frontmatter, which is
what turned every collision into a permanently nonconformant bundle.
"""

from __future__ import annotations

import contextlib
import logging as _logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import frontmatter as fm
import pytest

from markdown_vault_mcp._okf_convention import build_projection_rules
from markdown_vault_mcp.git import GitWriteStrategy

if TYPE_CHECKING:
    from markdown_vault_mcp.git.conflict import ProjectionRules


@contextlib.contextmanager
def _caplog_at(level: int):  # type: ignore[no-untyped-def]
    """Collect records from the vault logger for the duration."""
    records: list[_logging.LogRecord] = []

    class _Sink(_logging.Handler):
        def emit(self, record: _logging.LogRecord) -> None:
            records.append(record)

    logger = _logging.getLogger("markdown_vault_mcp.vault")
    handler = _Sink(level)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


_ROOT_INDEX = '---\nokf_version: "0.2"\n---\n# Bundle\n'
_BASE_LOG = "# Log\n\n## 2026-09-07\n\n- base\n"
_BASE_INDEX = "# guides\n\n- [A](/guides/a.md)\n- [C](/guides/c.md)\n"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _commit_all(cwd: Path, message: str) -> None:
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-q", "-m", message)


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path, Path]:
    """``(work, other, bare)``: two clones of one bare remote sharing a base commit."""
    bare = tmp_path / "bare.git"
    bare.mkdir()
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(bare)],
        check=True,
        capture_output=True,
    )
    clones = []
    for name in ("work", "other"):
        clone = tmp_path / name
        subprocess.run(
            ["git", "clone", "-q", str(bare), str(clone)],
            check=True,
            capture_output=True,
        )
        _git(clone, "config", "user.email", f"{name}@test")
        _git(clone, "config", "user.name", name)
        _git(clone, "checkout", "-q", "-b", "main")
        clones.append(clone)
    work, other = clones
    _write(
        work,
        {
            "index.md": _ROOT_INDEX,
            "guides/log.md": _BASE_LOG,
            "guides/index.md": _BASE_INDEX,
        },
    )
    _commit_all(work, "base")
    _git(work, "push", "-q", "-u", "origin", "main")
    _git(other, "pull", "-q", "origin", "main")
    _git(other, "branch", "-q", "--set-upstream-to=origin/main")
    return work, other, bare


class _ActiveDetector:
    def state(self) -> object:
        return type("S", (), {"active": True})()


def _okf_rules(source_dir: Path | None = None) -> ProjectionRules | None:
    return build_projection_rules(
        detector=_ActiveDetector(),  # type: ignore[arg-type]
        enabled=True,
        source_dir=source_dir if source_dir is not None else Path("/"),
    )


def _diverge_reserved(work: Path, other: Path) -> None:
    """Both clones change the same log section and adjacent index entries."""
    _write(
        work,
        {
            "guides/log.md": "# Log\n\n## 2026-09-07\n\n- base\n- **Update**: wrote `guides/b.md`\n",
            "guides/index.md": "# guides\n\n- [A](/guides/a.md)\n- [B](/guides/b.md)\n- [C](/guides/c.md)\n",
        },
    )
    _commit_all(work, "write: guides/b.md")
    _write(
        other,
        {
            "guides/log.md": "# Log\n\n## 2026-09-07\n\n- base\n- edited a.md in Obsidian\n",
            "guides/index.md": "# guides\n\n- [A](/guides/a.md)\n- [Bb](/guides/bb.md)\n- [C](/guides/c.md)\n",
        },
    )
    _commit_all(other, "obsidian")
    _git(other, "push", "-q", "origin", "main")


def _strategy(work: Path) -> GitWriteStrategy:
    return GitWriteStrategy(
        token=None, enable_push=False, push_delay_s=0, repo_path=work
    )


class TestReservedFilesResolveInPlace:
    def test_all_reserved_conflict_leaves_no_sibling_and_merges_the_log(
        self, repos: tuple[Path, Path, Path]
    ) -> None:
        work, other, _ = repos
        _diverge_reserved(work, other)
        strategy = _strategy(work)
        strategy.set_projection_provider(_okf_rules)
        try:
            result = strategy.force_pull()
        finally:
            strategy.close()

        assert result.applied is True
        assert result.reason == "rebased", result
        assert result.conflict_files == ()
        assert not list(work.rglob("*.conflict-mcp-*.md"))
        log = (work / "guides" / "log.md").read_text(encoding="utf-8")
        assert "- edited a.md in Obsidian\n" in log
        assert "- **Update**: wrote `guides/b.md`\n" in log
        assert "conflict_with" not in log
        index = (work / "guides" / "index.md").read_text(encoding="utf-8")
        assert (
            index
            == "# guides\n\n- [A](/guides/a.md)\n- [Bb](/guides/bb.md)\n- [C](/guides/c.md)\n"
        )
        # Clean tree, rebase finished: the merged log is in the replayed commit.
        status = subprocess.run(
            ["git", "-C", str(work), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert status == ""

    def test_add_add_log_conflict_merges_under_one_header(
        self, repos: tuple[Path, Path, Path]
    ) -> None:
        work, other, _ = repos
        _write(work, {"notes/log.md": "# Log\n\n## 2026-09-07\n\n- ours\n"})
        _commit_all(work, "write: notes/x.md")
        _write(other, {"notes/log.md": "# Log\n\n## 2026-09-07\n\n- theirs\n"})
        _commit_all(other, "obsidian")
        _git(other, "push", "-q", "origin", "main")
        strategy = _strategy(work)
        strategy.set_projection_provider(_okf_rules)
        try:
            result = strategy.force_pull()
        finally:
            strategy.close()

        assert result.reason == "rebased", result
        log = (work / "notes" / "log.md").read_text(encoding="utf-8")
        assert log.count("# Log\n") == 1
        assert log.count("## 2026-09-07") == 1
        assert "- theirs\n" in log and "- ours\n" in log
        assert not list(work.rglob("*.conflict-mcp-*.md"))

    def test_mixed_conflict_siblings_only_the_note(
        self, repos: tuple[Path, Path, Path]
    ) -> None:
        work, other, _ = repos
        _diverge_reserved(work, other)
        _write(work, {"guides/note.md": "# local\n"})
        _commit_all(work, "write: guides/note.md")
        _write(other, {"guides/note.md": "# theirs\n"})
        _commit_all(other, "obsidian note")
        _git(other, "push", "-q", "origin", "main")
        strategy = _strategy(work)
        strategy.set_projection_provider(_okf_rules)
        try:
            result = strategy.force_pull()
        finally:
            strategy.close()

        assert result.reason == "conflicts_resolved_with_siblings", result
        siblings = [Path(p).name for p in result.conflict_files]
        assert len(siblings) == 1 and siblings[0].startswith("note.conflict-mcp-")
        assert not list(work.rglob("index.conflict-mcp-*.md"))
        assert not list(work.rglob("log.conflict-mcp-*.md"))
        log = (work / "guides" / "log.md").read_text(encoding="utf-8")
        assert "- edited a.md in Obsidian\n" in log
        assert "- **Update**: wrote `guides/b.md`\n" in log
        assert "conflict_with" in fm.loads((work / "guides" / "note.md").read_text())


class TestNotesKeepTheSiblingPolicy:
    """Without the server owning the reserved files they are ordinary notes."""

    @pytest.mark.parametrize(
        "provider", [None, lambda: None], ids=["unwired", "inactive"]
    )
    def test_reserved_names_still_get_siblings(
        self, repos: tuple[Path, Path, Path], provider: object
    ) -> None:
        work, other, _ = repos
        _diverge_reserved(work, other)
        strategy = _strategy(work)
        if provider is not None:
            strategy.set_projection_provider(provider)  # type: ignore[arg-type]
        try:
            result = strategy.force_pull()
        finally:
            strategy.close()

        assert result.reason == "conflicts_resolved_with_siblings", result
        assert list(work.rglob("index.conflict-mcp-*.md"))
        assert list(work.rglob("log.conflict-mcp-*.md"))
        assert (
            "conflict_with"
            in fm.loads((work / "guides" / "index.md").read_text()).metadata
        )


class TestResolveProjectionUnit:
    def test_unreadable_local_version_keeps_upstream_untouched(
        self, tmp_path: Path
    ) -> None:
        """``git show`` failed for the local side: upstream stays, no git call."""
        from markdown_vault_mcp.git import conflict

        (tmp_path / "log.md").write_text("# Log\n", encoding="utf-8")
        rules = _okf_rules(tmp_path)
        assert rules is not None
        conflict._resolve_projection(tmp_path, "log.md", None, rules, None)
        assert (tmp_path / "log.md").read_text(encoding="utf-8") == "# Log\n"


class TestVaultWiring:
    """The vault hands the strategy a provider that answers per pull."""

    def _provider(self, root: Path, **kw: object) -> object:
        from markdown_vault_mcp.vault import Vault

        # ``spec=`` both makes the mock satisfy ``ProjectionAware`` (a
        # runtime-checkable protocol reads ``dir()``, and a bare MagicMock
        # creates its attributes lazily) and pins that the real strategy
        # carries the hook.
        strategy = MagicMock(spec=GitWriteStrategy)
        col = Vault(source_dir=root, read_only=False, git_strategy=strategy, **kw)  # type: ignore[arg-type]
        try:
            strategy.set_projection_provider.assert_called_once()
            provider = strategy.set_projection_provider.call_args.args[0]
            return provider()
        finally:
            col.close()

    def test_rules_when_the_server_maintains_the_files(self, tmp_path: Path) -> None:
        (tmp_path / "index.md").write_text(_ROOT_INDEX, encoding="utf-8")
        rules = self._provider(tmp_path, okf_mode="on", okf_write=True)
        assert rules is not None
        assert rules.is_projection(tmp_path, "guides/log.md")  # type: ignore[attr-defined]

    def test_none_without_okf_write(self, tmp_path: Path) -> None:
        (tmp_path / "index.md").write_text(_ROOT_INDEX, encoding="utf-8")
        assert self._provider(tmp_path, okf_mode="on", okf_write=False) is None

    def test_none_when_the_vault_is_not_a_bundle(self, tmp_path: Path) -> None:
        assert self._provider(tmp_path, okf_mode="auto", okf_write=True) is None

    def test_a_store_without_the_hook_is_wired_around(self, tmp_path: Path) -> None:
        """An alternate store keeps the sibling policy instead of failing."""
        import logging

        from markdown_vault_mcp.vault import Vault

        (tmp_path / "index.md").write_text(_ROOT_INDEX, encoding="utf-8")

        class _StoreWithoutTheHook:
            def set_write_quiescer(self, **_kw: object) -> None: ...
            def close(self) -> None: ...

        with pytest.MonkeyPatch.context(), _caplog_at(logging.WARNING) as records:
            col = Vault(
                source_dir=tmp_path,
                read_only=False,
                okf_mode="on",
                okf_write=True,
                git_strategy=_StoreWithoutTheHook(),  # type: ignore[arg-type]
            )
            col.close()
        assert any("okf_projection_hook_unsupported" in r.getMessage() for r in records)


class TestAbortDiscardsInPlaceResolutions:
    """An abort undoes the merges, so the pull must not report success (#1395)."""

    def test_projection_resolved_then_aborted_fails_the_pull(
        self, repos: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from markdown_vault_mcp.git import conflict as conflict_mod

        work, other, _ = repos
        _diverge_reserved(work, other)
        _write(work, {"guides/note.md": "# local\n"})
        _commit_all(work, "write: guides/note.md")
        _write(other, {"guides/note.md": "# theirs\n"})
        _commit_all(other, "obsidian note")
        _git(other, "push", "-q", "origin", "main")
        head_before = subprocess.run(
            ["git", "-C", str(work), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        # Claim a note was saved and a projection resolved in place, without
        # continuing the rebase — so it is still in progress on return and
        # the abort discards the in-place resolution.
        def _stub_resolve(*_args: object, **_kwargs: object) -> object:
            return conflict_mod.ConflictResolution(
                [("guides/note.md", "# local\n")], ("guides/log.md",)
            )

        monkeypatch.setattr(conflict_mod, "resolve_rebase_conflicts", _stub_resolve)
        strategy = _strategy(work)
        strategy.set_projection_provider(_okf_rules)
        try:
            result = strategy.force_pull()
        finally:
            strategy.close()

        assert result.applied is False
        assert result.reason == "conflict_resolution_failed", result
        assert result.from_sha == result.to_sha == head_before
        assert not list(work.rglob("*.conflict-mcp-*.md"))


class TestProjectionScope:
    """A reserved name outside the vault belongs to no bundle we maintain."""

    def test_a_reserved_name_outside_the_vault_is_not_a_projection(
        self, tmp_path: Path
    ) -> None:
        git_root = tmp_path / "repo"
        vault = git_root / "vault"
        vault.mkdir(parents=True)
        rules = _okf_rules(vault)
        assert rules is not None
        assert rules.is_projection(git_root, "vault/guides/log.md")
        assert rules.is_projection(git_root, "vault/index.md")
        # Same basenames, elsewhere in the repository: someone else's files.
        assert not rules.is_projection(git_root, "index.md")
        assert not rules.is_projection(git_root, "docs/log.md")
        assert not rules.is_projection(git_root, "vault-notes/index.md")


class TestProjectionRootResolution:
    """Git reports repository-relative paths whatever tree it was run from."""

    def test_a_vault_below_the_repository_root_scopes_correctly(
        self, tmp_path: Path
    ) -> None:
        """The `force_*` entry points pass the vault, not the toplevel."""
        from markdown_vault_mcp.git import conflict

        repo = tmp_path / "repo"
        vault = repo / "vault"
        vault.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "-q", "-b", "main", str(repo)],
            check=True,
            capture_output=True,
        )
        # Asked from the vault, git still answers with the repository root.
        assert conflict.repo_root(vault, None).resolve() == repo.resolve()

        rules = _okf_rules(vault)
        assert rules is not None
        root = conflict.repo_root(vault, None)
        assert rules.is_projection(root, "vault/guides/log.md")
        assert not rules.is_projection(root, "docs/index.md")
        # The bug this pins: joining onto the vault made every path "inside".
        assert rules.is_projection(vault, "docs/index.md")

    def test_conflict_paths_are_addressed_from_the_toplevel(
        self, tmp_path: Path
    ) -> None:
        """Git reports `vault/log.md`; the operations must not build
        `vault/vault/log.md` under the configured working tree."""
        from markdown_vault_mcp.git import conflict

        repo = tmp_path / "repo"
        vault = repo / "vault"
        vault.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "-q", "-b", "main", str(repo)],
            check=True,
            capture_output=True,
        )
        (vault / "log.md").write_text("# Log\n", encoding="utf-8")
        top = conflict.repo_root(vault, None)
        # What the resolver and the sibling writer join their paths onto.
        assert (top / "vault/log.md").is_file()
        assert not (vault / "vault/log.md").exists()
