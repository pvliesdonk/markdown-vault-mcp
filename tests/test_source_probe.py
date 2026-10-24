"""The file-backed source probe (#1785)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.hashing import compute_file_hash
from markdown_vault_mcp.interfaces import SourceProbe
from markdown_vault_mcp.source_probe import FileSourceProbe
from markdown_vault_mcp.types import SourceState

if TYPE_CHECKING:
    from pathlib import Path


def test_it_satisfies_the_protocol(tmp_path: Path) -> None:
    assert isinstance(FileSourceProbe(tmp_path), SourceProbe)


def test_a_file_is_present_with_its_content_hash(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# A\n")
    assert FileSourceProbe(tmp_path).probe("a.md") == SourceState(
        "present", compute_file_hash(tmp_path / "a.md")
    )


@pytest.mark.parametrize("make", ["missing", "under_a_file", "directory"])
def test_no_file_there_is_absent(tmp_path: Path, make: str) -> None:
    if make == "under_a_file":
        (tmp_path / "x").write_text("")
        path = "x/a.md"
    elif make == "directory":
        (tmp_path / "a.md").mkdir()
        path = "a.md"
    else:
        (tmp_path / "other.md").write_text("")  # a vault, not an empty mount point
        path = "a.md"
    assert FileSourceProbe(tmp_path).probe(path).status == "absent"


def test_an_empty_root_is_unavailable(tmp_path: Path) -> None:
    # What an unmounted volume or an empty bind mount looks like (#1791).
    assert FileSourceProbe(tmp_path).probe("a.md").status == "unavailable"


def test_a_root_with_any_entry_answers_absent(tmp_path: Path) -> None:
    (tmp_path / ".obsidian").mkdir()
    assert FileSourceProbe(tmp_path).probe("a.md").status == "absent"


def test_a_root_that_cannot_be_listed_is_unavailable(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "x.md").write_text("")
    root.chmod(0o300)  # statable, not listable
    try:
        assert FileSourceProbe(root).probe("a.md").status == "unavailable"
    finally:
        root.chmod(0o755)


def test_a_refused_stat_is_unavailable(tmp_path: Path) -> None:
    shut = tmp_path / "shut"
    shut.mkdir()
    (shut / "a.md").write_text("# A\n")
    shut.chmod(0)
    try:
        assert FileSourceProbe(tmp_path).probe("shut/a.md").status == "unavailable"
    finally:
        shut.chmod(0o755)


@pytest.mark.parametrize("root", ["missing", "file"])
def test_a_root_that_is_not_a_directory_is_unavailable(
    tmp_path: Path, root: str
) -> None:
    base = tmp_path / "root"
    if root == "file":
        base.write_text("")
    assert FileSourceProbe(base).probe("a.md").status == "unavailable"


def test_a_name_that_only_matches_case_insensitively_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a case-insensitive file system "Foo.md" stats as "foo.md" (#1785)."""
    from pathlib import Path as _Path

    (tmp_path / "foo.md").write_text("# Foo\n")
    real_stat = _Path.stat

    def case_insensitive_stat(self: _Path, **kw: object):  # type: ignore[no-untyped-def]
        if self.name == "Foo.md":
            return real_stat(self.with_name("foo.md"), **kw)
        return real_stat(self, **kw)

    monkeypatch.setattr(_Path, "stat", case_insensitive_stat)
    assert FileSourceProbe(tmp_path).probe("Foo.md").status == "absent"
    assert FileSourceProbe(tmp_path).probe("foo.md").status == "present"


def test_a_directory_that_only_matches_case_insensitively_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder renamed "Notes" → "notes" leaves "Notes/a.md" statable (#1785)."""
    from pathlib import Path as _Path

    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.md").write_text("# A\n")
    real_stat = _Path.stat

    def case_insensitive_stat(self: _Path, **kw: object):  # type: ignore[no-untyped-def]
        return real_stat(_Path(str(self).replace("/Notes/", "/notes/")), **kw)

    monkeypatch.setattr(_Path, "stat", case_insensitive_stat)
    assert FileSourceProbe(tmp_path).probe("Notes/a.md").status == "absent"
    assert FileSourceProbe(tmp_path).probe("notes/a.md").status == "present"
