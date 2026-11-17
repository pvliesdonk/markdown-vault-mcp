"""Isolated real-Git fixtures for the optional local versioned store."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from tests.fixtures.git import _run_git as _git

if TYPE_CHECKING:
    from pathlib import Path

pygit2 = pytest.importorskip("pygit2")


@pytest.fixture
def isolated_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Path:
    """Create a synthetic repository without the operator's Git configuration."""
    for level in (
        pygit2.GIT_CONFIG_LEVEL_GLOBAL,
        pygit2.GIT_CONFIG_LEVEL_XDG,
        pygit2.GIT_CONFIG_LEVEL_SYSTEM,
    ):
        original = pygit2.option(pygit2.GIT_OPT_GET_SEARCH_PATH, level)
        pygit2.option(pygit2.GIT_OPT_SET_SEARCH_PATH, level, str(tmp_path))
        request.addfinalizer(
            lambda level=level, original=original: pygit2.option(
                pygit2.GIT_OPT_SET_SEARCH_PATH, level, original
            )
        )
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "core.hooksPath", ".git/hooks")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.com")
    (repo / "unrelated.md").write_text("original\n")
    _git(repo, "add", "unrelated.md")
    _git(repo, "commit", "-qm", "initial")
    (repo / "unrelated.md").write_text("staged\n")
    _git(repo, "add", "unrelated.md")
    return repo
