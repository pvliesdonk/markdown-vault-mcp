"""Tests for markdown_vault_mcp.utils.fs discovery + directory pruning."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.utils import is_path_excluded
from markdown_vault_mcp.utils.fs import (
    _dir_prune_rules,
    _should_prune_dir,
    iter_markdown_files,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

# The 23 exclude patterns from the live md-recall deployment: the two shapes
# this fix must prune are anchored ``PREFIX/**`` and any-depth ``**/NAME/**``.
LIVE_EXCLUDES = [
    "Library/**",
    ".Trash/**",
    ".cache/**",
    ".npm/**",
    "go/**",
    ".cargo/**",
    ".rustup/**",
    ".codex/**",
    ".vscode/**",
    ".oh-my-zsh/**",
    ".claude/plugins/**",
    "Downloads/**",
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
    "**/.git/**",
    "**/site-packages/**",
    "**/dist/**",
    "**/build/**",
    "**/.terraform/**",
    "**/.pytest_cache/**",
    "**/vendor/**",
    "**/.markdown_vault_mcp/**",
]


def _touch(root: Path, rel: str) -> None:
    """Create an empty ``.md`` file at *rel* under *root*, making parents."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# x\n", encoding="utf-8")


def _rels(root: Path, patterns: Sequence[str] | None) -> set[str]:
    """Discover markdown files under *root* and return relative POSIX paths."""
    return {p.relative_to(root).as_posix() for p in iter_markdown_files(root, patterns)}


# ---------------------------------------------------------------------------
# iter_markdown_files
# ---------------------------------------------------------------------------


class TestIterMarkdownFiles:
    def test_root_level_md_matched(self, tmp_path: Path) -> None:
        _touch(tmp_path, "top.md")
        _touch(tmp_path, "notes/nested.md")
        assert _rels(tmp_path, LIVE_EXCLUDES) == {"top.md", "notes/nested.md"}

    def test_file_in_excluded_subtree_not_discovered(self, tmp_path: Path) -> None:
        _touch(tmp_path, "keep.md")
        _touch(tmp_path, "proj/node_modules/dep.md")
        _touch(tmp_path, "a/b/.venv/lib/pkg.md")
        _touch(tmp_path, "go/pkg/mod.md")
        _touch(tmp_path, "Library/cache.md")
        assert _rels(tmp_path, LIVE_EXCLUDES) == {"keep.md"}

    def test_nested_non_excluded_file_discovered(self, tmp_path: Path) -> None:
        _touch(tmp_path, "notes/deep/sub/here.md")
        _touch(tmp_path, "proj/src/module/impl.md")
        assert _rels(tmp_path, LIVE_EXCLUDES) == {
            "notes/deep/sub/here.md",
            "proj/src/module/impl.md",
        }

    def test_anchored_prefix_dir_itself_pruned(self, tmp_path: Path) -> None:
        # ``.claude/plugins/**`` fully covers the ``.claude/plugins`` directory
        # even though that directory path does not itself match the filter.
        _touch(tmp_path, ".claude/keep.md")
        _touch(tmp_path, ".claude/plugins/cache/x.md")
        assert _rels(tmp_path, LIVE_EXCLUDES) == {".claude/keep.md"}

    def test_top_level_anydepth_dir_not_pruned(self, tmp_path: Path) -> None:
        # ``**/node_modules/**`` needs a slash before the segment, so a
        # top-level ``node_modules`` is NOT excluded and must be discovered;
        # pruning it would drop a file the per-file filter keeps.
        _touch(tmp_path, "node_modules/toplevel.md")
        _touch(tmp_path, "proj/node_modules/nested.md")
        discovered = _rels(tmp_path, LIVE_EXCLUDES)
        assert "node_modules/toplevel.md" in discovered
        assert "proj/node_modules/nested.md" not in discovered
        # Ground-truth: this matches raw-glob-then-filter exactly.
        truth = {
            p.relative_to(tmp_path).as_posix()
            for p in tmp_path.glob("**/*.md")
            if p.is_file()
            and not is_path_excluded(p.relative_to(tmp_path).as_posix(), LIVE_EXCLUDES)
        }
        assert discovered == truth

    def test_unknown_shape_pattern_does_not_prune(self, tmp_path: Path) -> None:
        # An exact-file exclude (no ``/**`` suffix) is not a recognised prune
        # shape, so the ``build`` directory is descended rather than pruned.
        # The per-file filter then drops only the exact match, and a sibling
        # under the same directory survives — the walker must not have pruned.
        _touch(tmp_path, "build/artifact.md")
        _touch(tmp_path, "build/keep.md")
        patterns = ["build/artifact.md"]
        raw = _rels(tmp_path, patterns)
        # The walker prunes nothing for this shape: it yields both files.
        assert raw == {"build/artifact.md", "build/keep.md"}
        # The per-file filter (the correctness layer) drops only the exact
        # match, keeping the sibling — exactly like the raw glob would.
        filtered = {r for r in raw if not is_path_excluded(r, patterns)}
        assert filtered == {"build/keep.md"}

    def test_no_patterns_discovers_everything(self, tmp_path: Path) -> None:
        _touch(tmp_path, "top.md")
        _touch(tmp_path, "node_modules/dep.md")
        _touch(tmp_path, ".venv/lib.md")
        assert _rels(tmp_path, None) == {
            "top.md",
            "node_modules/dep.md",
            ".venv/lib.md",
        }

    def test_non_md_files_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "note.md").write_text("# x\n", encoding="utf-8")
        (tmp_path / "image.png").write_text("binary", encoding="utf-8")
        (tmp_path / "README.txt").write_text("text", encoding="utf-8")
        assert _rels(tmp_path, LIVE_EXCLUDES) == {"note.md"}

    def test_permission_error_dir_suppressed(self, tmp_path: Path) -> None:
        # os.walk swallows OSError from unreadable dirs, matching glob. The
        # readable sibling is still discovered; the scan does not crash.
        _touch(tmp_path, "readable/ok.md")
        locked = tmp_path / "locked"
        locked.mkdir()
        _touch(tmp_path, "locked/hidden.md")
        locked.chmod(0o000)
        try:
            discovered = _rels(tmp_path, LIVE_EXCLUDES)
        finally:
            locked.chmod(0o755)
        assert "readable/ok.md" in discovered

    @pytest.mark.skipif(
        sys.version_info < (3, 13),
        reason="symlink following gated on recurse_symlinks (3.13+), matching glob",
    )
    def test_symlinked_directory_followed(self, tmp_path: Path) -> None:
        # Mirrors scanner's #508 behaviour: on 3.13+ a symlinked subdir is
        # followed, so a note reached only via the link is discovered.
        real = tmp_path / "real"
        real.mkdir()
        (real / "linked.md").write_text("# Linked\n", encoding="utf-8")
        vault = tmp_path / "vault"
        vault.mkdir()
        try:
            (vault / "via_symlink").symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlink creation not supported here: {exc}")
        assert "via_symlink/linked.md" in _rels(vault, LIVE_EXCLUDES)


# ---------------------------------------------------------------------------
# Prune-rule derivation (pure functions)
# ---------------------------------------------------------------------------


class TestDirPruneRules:
    def test_live_patterns_split_into_two_shapes(self) -> None:
        anchored, anydepth = _dir_prune_rules(LIVE_EXCLUDES)
        assert "go" in anchored
        assert ".claude/plugins" in anchored
        assert "node_modules" in anydepth
        assert ".venv" in anydepth
        # Every live pattern is one of the two recognised shapes.
        assert len(anchored) + len(anydepth) == len(LIVE_EXCLUDES)

    @pytest.mark.parametrize(
        "pattern",
        [
            "build/*.md",  # file glob, not a subtree
            "*.tmp",  # bare file glob
            "**/*.md",  # the discovery glob itself, not an exclude subtree
            "**/logs/*.log",  # any-depth file glob
            "a/*/b/**",  # wildcard in the anchored prefix
            "**/no_trailing",  # no /** suffix
        ],
    )
    def test_unknown_shapes_yield_no_rules(self, pattern: str) -> None:
        anchored, anydepth = _dir_prune_rules([pattern])
        assert anchored == []
        assert anydepth == []

    def test_should_prune_matches_file_filter_on_live_patterns(self) -> None:
        # The invariant that makes pruning safe: whenever _should_prune_dir is
        # True for a directory, the real per-file filter excludes every file
        # beneath it. Check a spread of directory shapes.
        anchored, anydepth = _dir_prune_rules(LIVE_EXCLUDES)
        dirs = [
            "",
            "notes",
            "notes/sub",
            "go",
            "go/pkg/mod",
            "Library",
            "Library/x",
            ".claude",
            ".claude/plugins",
            ".claude/plugins/cache",
            "node_modules",  # top-level: must NOT prune
            "a/node_modules",
            "a/b/node_modules",
            "proj/.venv",
            "site-packages",  # top-level: must NOT prune
            "w/site-packages",
        ]
        for d in dirs:
            pruned = _should_prune_dir(d, anchored, anydepth)
            children = [
                f"{d}/f.md" if d else "f.md",
                f"{d}/sub/deep.md" if d else "x.md",
            ]
            all_excluded = all(is_path_excluded(c, LIVE_EXCLUDES) for c in children)
            if pruned:
                assert all_excluded, f"pruned {d!r} but a child is not excluded"

    def test_root_never_pruned(self) -> None:
        anchored, anydepth = _dir_prune_rules(LIVE_EXCLUDES)
        assert _should_prune_dir("", anchored, anydepth) is False


# ---------------------------------------------------------------------------
# Cross-check: pruning walk == raw glob then filter (ground truth)
# ---------------------------------------------------------------------------


def test_prune_walk_equals_glob_then_filter(tmp_path: Path) -> None:
    layout = [
        "top.md",
        "notes/a.md",
        "notes/sub/b.md",
        "go/pkg/x.md",
        "Library/y.md",
        ".claude/plugins/z.md",
        ".claude/keep.md",
        "proj/node_modules/dep.md",
        "proj/src/c.md",
        "node_modules/toplevel.md",
        "a/.venv/lib/d.md",
        "a/real.md",
        "Downloads/e.md",
        "site-packages/toplevel2.md",
        "w/site-packages/f.md",
    ]
    for rel in layout:
        _touch(tmp_path, rel)

    def rel_of(path: Path) -> str:
        return path.relative_to(tmp_path).as_posix()

    truth = sorted(
        rel_of(p)
        for p in tmp_path.glob("**/*.md")
        if p.is_file() and not is_path_excluded(rel_of(p), LIVE_EXCLUDES)
    )
    walked = sorted(
        rel_of(p)
        for p in iter_markdown_files(tmp_path, LIVE_EXCLUDES)
        if not is_path_excluded(rel_of(p), LIVE_EXCLUDES)
    )
    assert walked == truth
