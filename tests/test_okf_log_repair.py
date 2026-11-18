"""Synthetic legacy repair: exact reviewed removal, retention, and failure safety."""

from __future__ import annotations

import stat
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import pytest

from markdown_vault_mcp._okf_log_repair import LogRepairInterrupted
from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    DocumentNotFoundError,
    DocumentUnreadableError,
    InvalidRequestError,
    NoteTooLargeError,
    ReadOnlyError,
    SubmodulePathError,
)
from markdown_vault_mcp.facets.writer import WriterFacet
from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.hashing import compute_etag
from markdown_vault_mcp.managers import document as document_module
from markdown_vault_mcp.managers.document import DocumentManager
from markdown_vault_mcp.scanner import HeadingChunker

if TYPE_CHECKING:
    from collections.abc import Iterator

UNIT = b"---\ntitle: Log\n---\n\n"
SUFFIX = b"# Log\n\n## 2026-09-07\n\n- **Update**: wrote `a.md`\n"
STACKED = Path("tests/fixtures/okf_log_repair/stacked.md.txt").read_bytes()
BACKUP = "log.md.frontmatter-repair.bak"


@pytest.fixture
def manager(tmp_path: Path) -> Iterator[DocumentManager]:
    """Provide only synthetic note data and an isolated index."""
    (tmp_path / "log.md").write_bytes(STACKED)
    fts = FTSIndex(db_path=":memory:")
    yield DocumentManager(
        fts=fts,
        source_dir=tmp_path,
        write_lock=threading.RLock(),
        chunk_strategy=HeadingChunker(),
        read_only=False,
        write_protect_existing=True,
    )
    fts.close()


def _reviewed(manager: DocumentManager, path: str = "log.md") -> str:
    preview = manager.okf_preview_log_repair(path)
    assert preview.status == "review_required"
    return preview.etag


def test_historical_fixture_preview_and_exact_apply(
    manager: DocumentManager, tmp_path: Path
) -> None:
    assert STACKED == UNIT * 3 + SUFFIX
    writer = WriterFacet(manager)
    preview = writer.okf_preview_log_repair("log.md")
    assert preview.status == "review_required"
    assert preview.removed_blocks == 2
    assert preview.removal_start == len(UNIT)
    assert preview.removal_end == len(UNIT) * 3
    assert preview.removal_text == (UNIT * 2).decode()
    assert preview.etag == compute_etag(STACKED)
    assert preview.new_etag == compute_etag(UNIT + SUFFIX)
    assert "origin" in preview.explanation
    assert not (tmp_path / BACKUP).exists()
    result = writer.okf_apply_log_repair("log.md", etag=_reviewed(manager))
    assert result.recovery_path == BACKUP
    assert result.etag == preview.new_etag
    assert (tmp_path / "log.md").read_bytes() == UNIT + SUFFIX
    assert (tmp_path / BACKUP).read_bytes() == STACKED
    assert stat.S_IMODE((tmp_path / BACKUP).stat().st_mode) == 0o600
    assert writer.okf_preview_log_repair("log.md").status == "no_candidate"


@pytest.mark.parametrize("prefix", [b"", b"\xef\xbb\xbf"])
@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_preserves_opening_metadata_and_suffix_bytes(
    manager: DocumentManager, tmp_path: Path, prefix: bytes, newline: bytes
) -> None:
    unit = (
        (
            '---\ntitle: "Lög" # authored comment\nverified: [{by: "human:fixture"}]\n'
            'generated: {by: "process:old"}\n---\n\n'
        )
        .encode()
        .replace(b"\n", newline)
    )
    suffix = "# Lög\r\n\n  trailing spaces  \r\n\n---\nquoted\n---".encode()
    original = prefix + unit * 3 + suffix
    (tmp_path / "log.md").write_bytes(original)
    manager.okf_apply_log_repair("log.md", etag=_reviewed(manager))
    assert (tmp_path / "log.md").read_bytes() == prefix + unit + suffix
    assert (tmp_path / BACKUP).read_bytes() == original


@pytest.mark.parametrize(
    "raw",
    [
        b"# Log\n",
        UNIT + SUFFIX,
        b"```yaml\n" + UNIT * 3,
        UNIT + b"authored explanation\n" + UNIT,
        UNIT + UNIT.replace(b"Log", b"Other") + SUFFIX,
        UNIT * 2 + b"---\ntitle: Other\n---\n\n" + SUFFIX,
        b"---\nbad: [\n---\n\n" * 3,
        b"---\nupdated: 2026-02-30\n---\n\n" * 3,
        b"---\ndate: 2026-99-99\n---\n\n" * 3,
        b"---\n- list\n---\n\n" * 3,
        UNIT.replace(b"\n\n", b"\n") * 3,
    ],
)
def test_unsupported_or_ambiguous_shapes_are_left_alone(
    manager: DocumentManager, tmp_path: Path, raw: bytes
) -> None:
    (tmp_path / "log.md").write_bytes(raw)
    preview = manager.okf_preview_log_repair("log.md")
    assert preview.status == "no_candidate"
    assert preview.new_etag is None
    assert (tmp_path / "log.md").read_bytes() == raw
    assert not (tmp_path / BACKUP).exists()


def test_apply_without_a_candidate_is_refused(
    manager: DocumentManager, tmp_path: Path
) -> None:
    raw = UNIT + SUFFIX
    (tmp_path / "log.md").write_bytes(raw)
    with pytest.raises(InvalidRequestError, match="no supported repeated"):
        manager.okf_apply_log_repair("log.md", etag=compute_etag(raw))
    assert (tmp_path / "log.md").read_bytes() == raw
    assert not (tmp_path / BACKUP).exists()


def test_stale_and_replayed_etag(manager: DocumentManager, tmp_path: Path) -> None:
    etag = _reviewed(manager)
    (tmp_path / "log.md").write_bytes(STACKED + b"new entry\n")
    with pytest.raises(ConcurrentModificationError):
        manager.okf_apply_log_repair("log.md", etag=etag)
    assert not (tmp_path / BACKUP).exists()
    manager.okf_apply_log_repair("log.md", etag=_reviewed(manager))
    with pytest.raises(ConcurrentModificationError):
        manager.okf_apply_log_repair("log.md", etag=etag)


@pytest.mark.parametrize("collision", ["equal", "different", "directory", "symlink"])
def test_every_recovery_collision_is_refused(
    manager: DocumentManager, tmp_path: Path, collision: str
) -> None:
    backup = tmp_path / BACKUP
    if collision == "directory":
        backup.mkdir()
    elif collision == "symlink":
        backup.symlink_to(tmp_path / "missing")
    else:
        backup.write_bytes(STACKED if collision == "equal" else b"precious")
    with pytest.raises(InvalidRequestError, match="recovery"):
        manager.okf_apply_log_repair("log.md", etag=_reviewed(manager))
    assert (tmp_path / "log.md").read_bytes() == STACKED
    if collision in {"equal", "different"}:
        assert backup.read_bytes() == (STACKED if collision == "equal" else b"precious")
    if collision == "symlink":
        assert backup.is_symlink()
        assert not (tmp_path / "missing").exists()


def test_read_only_can_preview_but_cannot_apply(
    manager: DocumentManager, tmp_path: Path
) -> None:
    manager._read_only = True
    etag = _reviewed(manager)
    with pytest.raises(ReadOnlyError):
        manager.okf_apply_log_repair("log.md", etag=etag)
    assert not (tmp_path / BACKUP).exists()


@pytest.mark.parametrize("path", ["../log.md", "/log.md", "index.md", "note.md"])
def test_path_scope(manager: DocumentManager, path: str) -> None:
    with pytest.raises(InvalidRequestError):
        manager.okf_preview_log_repair(path)


def test_read_limit_and_missing_or_invalid_file(
    manager: DocumentManager, tmp_path: Path
) -> None:
    manager._max_note_read_bytes = 1
    with pytest.raises(NoteTooLargeError):
        manager.okf_preview_log_repair("log.md")
    manager._max_note_read_bytes = 0
    (tmp_path / "log.md").write_bytes(b"\xff")
    with pytest.raises(DocumentUnreadableError):
        manager.okf_preview_log_repair("log.md")
    (tmp_path / "log.md").unlink()
    with pytest.raises(DocumentNotFoundError):
        manager.okf_preview_log_repair("log.md")
    (tmp_path / "log.md").mkdir()
    with pytest.raises(DocumentNotFoundError):
        manager.okf_preview_log_repair("log.md")


def test_notifications_precede_target(manager: DocumentManager, tmp_path: Path) -> None:
    seen: list[str] = []

    def notify(path: Path, content: str, operation: str) -> None:
        seen.append(path.name)
        if path.name == BACKUP:
            assert (tmp_path / "log.md").read_bytes() == STACKED
            assert content == ""
        else:
            assert content.encode() == UNIT + SUFFIX
        assert operation == "write"

    manager._notifier._callback = notify
    dirty = Mock()
    manager._mark_paths_dirty = dirty
    manager.okf_apply_log_repair("log.md", etag=_reviewed(manager))
    assert seen == [BACKUP, "log.md"]
    dirty.assert_called_once_with(["log.md"])


@pytest.mark.parametrize(
    "stage", ["retention", "chmod", "backup_notify", "replace", "target_notify"]
)
def test_partial_failure_retains_evidence_and_reports_state(
    manager: DocumentManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    etag = _reviewed(manager)
    if stage == "retention":
        original_open = Path.open

        def fail_open(path: Path, *args: Any, **kwargs: Any) -> Any:
            if path.name == BACKUP and args == ("xb",):
                path.write_bytes(b"partial")
                raise OSError("disk full")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fail_open)
    elif stage == "chmod":
        monkeypatch.setattr(Path, "chmod", Mock(side_effect=OSError("chmod failed")))
    elif stage == "replace":
        monkeypatch.setattr(document_module, "atomic_write", Mock(side_effect=OSError))
    else:

        def fail_notify(path: Path, _content: str, _operation: str) -> None:
            if (path.name == BACKUP) == (stage == "backup_notify"):
                raise RuntimeError("callback failed")

        manager._notifier._callback = fail_notify
    with pytest.raises(LogRepairInterrupted) as caught:
        manager.okf_apply_log_repair("log.md", etag=etag)
    assert caught.value.recovery_path == BACKUP
    assert caught.value.target_may_have_changed == (
        stage in {"replace", "target_notify"}
    )
    expected = UNIT + SUFFIX if stage == "target_notify" else STACKED
    assert (tmp_path / "log.md").read_bytes() == expected
    retained = {"retention": b"partial", "chmod": b""}.get(stage, STACKED)
    assert (tmp_path / BACKUP).read_bytes() == retained


def test_target_is_rechecked_after_retention(
    manager: DocumentManager, tmp_path: Path
) -> None:
    def external_edit(_path: Path, _content: str, _operation: str) -> None:
        (tmp_path / "log.md").write_bytes(b"external edit")

    manager._notifier._callback = external_edit
    with pytest.raises(LogRepairInterrupted, match="recovery"):
        manager.okf_apply_log_repair("log.md", etag=_reviewed(manager))
    assert (tmp_path / "log.md").read_bytes() == b"external edit"
    assert (tmp_path / BACKUP).read_bytes() == STACKED


def test_ordinary_write_never_cleans_stacked_blocks(
    manager: DocumentManager, tmp_path: Path
) -> None:
    manager.write("log.md", STACKED.decode(), if_match=compute_etag(STACKED))
    assert (tmp_path / "log.md").read_bytes() == STACKED
    assert not (tmp_path / BACKUP).exists()


def test_resolved_target_must_still_be_log(
    manager: DocumentManager, tmp_path: Path
) -> None:
    (tmp_path / "log.md").rename(tmp_path / "note.md")
    (tmp_path / "log.md").symlink_to(tmp_path / "note.md")
    with pytest.raises(InvalidRequestError, match=r"must name log\.md"):
        manager.okf_preview_log_repair("log.md")
    assert (tmp_path / "note.md").read_bytes() == STACKED


def test_absolute_in_vault_input_is_refused(
    manager: DocumentManager, tmp_path: Path
) -> None:
    with pytest.raises(InvalidRequestError, match="must be vault-relative"):
        manager.okf_preview_log_repair(str(tmp_path / "log.md"))


def test_nested_log_collision_names_exact_recovery_path(
    manager: DocumentManager, tmp_path: Path
) -> None:
    (tmp_path / "guides").mkdir()
    (tmp_path / "guides/log.md").write_bytes(STACKED)
    (tmp_path / "guides" / BACKUP).write_bytes(b"preserve me")
    with pytest.raises(
        InvalidRequestError, match=r"guides/log\.md\.frontmatter-repair\.bak"
    ):
        manager.okf_apply_log_repair(
            "guides/log.md", etag=_reviewed(manager, "guides/log.md")
        )
    assert (tmp_path / "guides/log.md").read_bytes() == STACKED


def test_nested_repository_gate_precedes_retention(
    manager: DocumentManager, tmp_path: Path
) -> None:
    etag = _reviewed(manager)
    guard = Mock()
    guard.refuse.side_effect = SubmodulePathError("nested repository")
    manager._submodule_guard = guard
    with pytest.raises(SubmodulePathError):
        manager.okf_apply_log_repair("log.md", etag=etag)
    assert not (tmp_path / BACKUP).exists()


def test_failed_retention_verification_keeps_target(
    manager: DocumentManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_read = Path.read_bytes

    def incorrect_copy(path: Path) -> bytes:
        return b"incomplete" if path.name == BACKUP else original_read(path)

    monkeypatch.setattr(Path, "read_bytes", incorrect_copy)
    with pytest.raises(LogRepairInterrupted):
        manager.okf_apply_log_repair("log.md", etag=_reviewed(manager))
    assert original_read(tmp_path / "log.md") == STACKED
    assert original_read(tmp_path / BACKUP) == STACKED
