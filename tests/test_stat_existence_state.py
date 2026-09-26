"""The server's own files: a refused stat is a fault, not absence (#1625).

Part 2 of #1625: the vector sidecars, tracker state, the OKF audit, the
conventions listing, user prompts and the configured vault directory.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores permission bits",
)


@pytest.fixture
def shut(tmp_path: Path) -> Iterator[Path]:
    """A directory that exists but cannot be searched, restored afterwards."""
    d = tmp_path / "shut"
    d.mkdir()
    (d / "inner").mkdir()
    (d / "inner" / "a.md").write_text("# A\n", encoding="utf-8")
    d.chmod(0)
    yield d
    d.chmod(0o755)


def test_vector_loader_refuses_instead_of_cold_building(shut: Path) -> None:
    """A cold build here would later overwrite the real store (#819)."""
    from markdown_vault_mcp.managers._vector_loader import load_or_self_heal

    rebuild = MagicMock()
    set_vectors = MagicMock()
    with pytest.raises(PermissionError):
        load_or_self_heal(
            embeddings_path=shut / "embeddings",
            embedding_provider=MagicMock(),
            get_vectors=lambda: None,
            set_vectors=set_vectors,
            rebuild=rebuild,
            logger=logging.getLogger("test"),
        )
    rebuild.assert_not_called()
    set_vectors.assert_not_called()


def test_okf_audit_of_an_unreadable_vault_is_a_fault(shut: Path) -> None:
    from markdown_vault_mcp.okf import audit_bundle

    with pytest.raises(PermissionError):
        audit_bundle(shut / "inner")


def test_conventions_listing_of_an_unreadable_vault_is_a_fault(shut: Path) -> None:
    from markdown_vault_mcp.conventions import ConventionsResolver

    with pytest.raises(PermissionError):
        ConventionsResolver(shut / "inner", filename="_conventions.md").list_folders()


def test_prompts_folder_warning_names_the_real_reason(
    shut: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from markdown_vault_mcp.prompts import _load_user_prompt_defs

    with caplog.at_level(logging.WARNING):
        assert _load_user_prompt_defs(str(shut / "inner")) == {}
    assert any("reason=unreadable" in r.getMessage() for r in caplog.records)


def test_source_dir_problem_tells_unreadable_from_missing(
    shut: Path, tmp_path: Path
) -> None:
    from markdown_vault_mcp.config_sections._assembly import source_dir_problem

    def config(path: Path) -> MagicMock:
        cfg = MagicMock()
        cfg.git.repo_url = None
        cfg.source_dir = path
        return cfg

    assert source_dir_problem(config(shut / "inner"))[0] == "unreadable"  # type: ignore[index]
    assert source_dir_problem(config(tmp_path / "nope"))[0] == "missing"  # type: ignore[index]
    assert source_dir_problem(config(tmp_path)) is None


def test_tracker_state_warns_on_load_and_refuses_reset(
    shut: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from markdown_vault_mcp.tracker import ChangeTracker

    tracker = ChangeTracker(shut / "state.json")
    with caplog.at_level(logging.WARNING):
        assert tracker._load_state() == ({}, {}, {})
    assert any("state_file_read_failed" in r.getMessage() for r in caplog.records)
    with pytest.raises(PermissionError):
        tracker.reset()
