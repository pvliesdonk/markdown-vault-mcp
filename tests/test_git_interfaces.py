"""Tests for the versioning seam: the git facet protocols (#1229).

Two things are pinned here.  First, that ``GitWriteStrategy`` really does
satisfy each facet, so the annotations the consumers now carry are not merely
decorative.  Second — the point of the seam — that the consumers accept *any*
object of the right shape, not only that one class: the ``git_sync`` gate used
to run ``isinstance(strategy, GitWriteStrategy)`` and read a private
attribute, so a fake proves the coupling is genuinely gone.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.git import (
    GitWriteStrategy,
    HistorySource,
    Syncer,
    VersionedStore,
    Versioner,
)

if TYPE_CHECKING:
    from tests.fixtures.git import GitRepoPair


class TestProtocolConformance:
    """``GitWriteStrategy`` implements all three facets."""

    @pytest.mark.parametrize(
        "protocol",
        [HistorySource, Syncer, Versioner, VersionedStore],
        ids=["history", "syncer", "versioner", "store"],
    )
    def test_strategy_satisfies_facet(self, protocol: type) -> None:
        """The one implementation satisfies each narrow interface.

        Constructed without ``repo_path`` so nothing touches the filesystem —
        conformance is about the shape, not about having a repository.
        """
        assert isinstance(GitWriteStrategy(), protocol)

    def test_unrelated_object_does_not_satisfy_syncer(self) -> None:
        """The runtime check is a real check, not a rubber stamp."""
        assert not isinstance(object(), Syncer)

    def test_history_source_does_not_imply_syncer(self) -> None:
        """The facets are independent: satisfying one is not satisfying all.

        This is what makes the split worth having — a history-only backend is
        expressible.
        """

        class HistoryOnly:
            def get_file_history(self, *args: Any, **kwargs: Any) -> list[Any]: ...
            def get_file_diff(self, *args: Any, **kwargs: Any) -> str: ...

        assert isinstance(HistoryOnly(), HistorySource)
        assert not isinstance(HistoryOnly(), Syncer)


class TestPromotedPublicSurface:
    """The members ``_server_tools/git.py`` used to reach for privately."""

    def test_is_managed_reflects_construction(self) -> None:
        """``is_managed`` replaces the private ``_managed`` read."""
        assert GitWriteStrategy(managed=True).is_managed is True
        assert GitWriteStrategy(managed=False).is_managed is False

    def test_resolve_force_repo_returns_configured_path(
        self, git_repo_pair: GitRepoPair
    ) -> None:
        """The working tree comes back when one was configured."""
        strategy = GitWriteStrategy(repo_path=git_repo_pair.local_path)
        assert strategy.resolve_force_repo() == git_repo_pair.local_path

    def test_resolve_force_repo_raises_without_repo_path(self) -> None:
        """Without a configured tree the force_* methods have nothing to act on."""
        with pytest.raises(RuntimeError, match="repo_path"):
            GitWriteStrategy().resolve_force_repo()

    def test_head_sha_returns_current_revision(
        self, git_repo_pair: GitRepoPair
    ) -> None:
        """``head_sha`` reads the checked-out revision of the given tree."""
        strategy = GitWriteStrategy(repo_path=git_repo_pair.local_path)
        sha = strategy.head_sha(git_repo_pair.local_path)
        assert re.fullmatch(r"[0-9a-f]{40}", sha)

    def test_branch_name_returns_checked_out_branch(
        self, git_repo_pair: GitRepoPair
    ) -> None:
        """``branch_name`` holds the body the git_sync tool used to inline."""
        strategy = GitWriteStrategy(repo_path=git_repo_pair.local_path)
        assert strategy.branch_name(git_repo_pair.local_path) == "main"


class _FakeSyncer:
    """A minimal stand-in that satisfies :class:`Syncer` without inheriting it.

    Only the members the managed-mode gate touches need real behaviour; the
    rest exist so the structural check passes.
    """

    def __init__(self, *, is_managed: bool) -> None:
        self._is_managed = is_managed

    @property
    def is_managed(self) -> bool:
        return self._is_managed

    def force_pull(self, *, dry_run: bool = False) -> Any: ...
    def force_push(self, *, dry_run: bool = False) -> Any: ...
    def sync_once(self, repo_path: Path) -> bool: ...
    def start(self, **kwargs: Any) -> None: ...
    def stop(self) -> None: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...
    def set_write_quiescer(self, *args: Any) -> None: ...
    def resolve_force_repo(self) -> Path: ...
    def head_sha(self, git_root: Path) -> str: ...
    def branch_name(self, git_root: Path) -> str: ...


class TestManagedGateAcceptsAnySyncer:
    """The ``git_sync`` gate depends on the seam, not on the class."""

    def test_fake_syncer_passes_the_managed_gate(self) -> None:
        """A non-GitWriteStrategy store in managed mode is accepted.

        Before #1229 this raised, because the gate ran an ``isinstance``
        against the concrete class.
        """
        from markdown_vault_mcp._server_tools.git import _resolve_managed_strategy

        fake = _FakeSyncer(is_managed=True)
        vault = type("_V", (), {"_git_strategy": fake})()

        assert _resolve_managed_strategy(vault) is fake  # type: ignore[arg-type]

    def test_unmanaged_syncer_still_rejected(self) -> None:
        """Widening the type did not widen what counts as managed."""
        from markdown_vault_mcp._server_tools.git import _resolve_managed_strategy

        vault = type("_V", (), {"_git_strategy": _FakeSyncer(is_managed=False)})()

        with pytest.raises(ValueError, match="managed git deployment"):
            _resolve_managed_strategy(vault)  # type: ignore[arg-type]

    def test_absent_strategy_still_rejected(self) -> None:
        """A vault with no git store at all keeps failing the same way."""
        from markdown_vault_mcp._server_tools.git import _resolve_managed_strategy

        vault = type("_V", (), {"_git_strategy": None})()

        with pytest.raises(ValueError, match="managed git deployment"):
            _resolve_managed_strategy(vault)  # type: ignore[arg-type]


def test_interfaces_module_imports_nothing_at_runtime() -> None:
    """The seam stays dependency-free so any driver can depend on it.

    ``git/`` already may not import ``fastmcp``; this narrows it further for
    the interface module, whose whole point is to be importable without
    dragging in an implementation.
    """
    source = (
        Path(__file__).parent.parent
        / "src"
        / "markdown_vault_mcp"
        / "git"
        / "interfaces.py"
    ).read_text()
    runtime_imports = [
        line
        for line in source.splitlines()
        if re.match(r"^(import|from)\s", line)
        if "__future__" not in line and "typing" not in line
    ]
    assert runtime_imports == [], (
        f"git/interfaces.py gained runtime imports: {runtime_imports}"
    )
