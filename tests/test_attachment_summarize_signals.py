"""Attachments and summarize tell a caller's mistake from a fault (#1608)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.exceptions import (
    DocumentNotFoundError,
    DocumentUnreadableError,
    InvalidRequestError,
)
from tests.test_managers_artifacts import _store
from tests.test_summarize import FakeSummarizer, make_vault  # noqa: F401

if TYPE_CHECKING:
    from pathlib import Path

    from tests.test_summarize import VaultFactory

_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def test_note_path_is_an_invalid_request(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    with pytest.raises(InvalidRequestError):
        store.validate_path("note.md")


def test_disallowed_extension_is_an_invalid_request(tmp_path: Path) -> None:
    store, _ = _store(tmp_path, extensions=["png"])
    with pytest.raises(InvalidRequestError) as exc:
        store.validate_path("a.xyz")
    # Operator configuration is named as the operator's, never as a step.
    assert "operator: MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS" in str(exc.value)
    assert "Set " not in str(exc.value)


@pytest.mark.parametrize("call", ["size", "read"])
def test_missing_attachment_is_not_found(tmp_path: Path, call: str) -> None:
    store, _ = _store(tmp_path)
    with pytest.raises(DocumentNotFoundError):
        getattr(store, call)("gone.png")


@pytest.mark.parametrize("call", ["size", "read"])
def test_folder_named_like_an_attachment_is_not_found(
    tmp_path: Path, call: str
) -> None:
    store, _ = _store(tmp_path)
    (tmp_path / "album.png").mkdir()
    with pytest.raises(DocumentNotFoundError):
        getattr(store, call)("album.png")


@pytest.mark.skipif(_ROOT, reason="root ignores permission bits")
@pytest.mark.parametrize("call", ["size", "read"])
def test_unreadable_attachment_is_a_fault_not_absence(
    tmp_path: Path, call: str
) -> None:
    store, _ = _store(tmp_path)
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "a.png").write_bytes(b"x")
    locked.chmod(0)
    try:
        with pytest.raises(ValueError, match="cannot be read") as exc:
            getattr(store, call)("locked/a.png")
        assert not isinstance(exc.value, InvalidRequestError)
    finally:
        locked.chmod(0o755)


@pytest.mark.parametrize(
    ("paths", "kwargs"),
    [
        (["alpha.md"], {"mode": "nope"}),
        ([], {}),
        (["alpha.md"], {"max_notes": 0}),
        (["no-such-folder"], {}),
        (["missing.md"], {}),
    ],
    ids=["mode", "no paths", "max_notes", "no notes found", "all missing"],
)
def test_summarize_caller_mistakes(
    make_vault: VaultFactory,  # noqa: F811
    paths: list[str],
    kwargs: dict[str, object],
) -> None:
    vault = make_vault(summarizer=FakeSummarizer())
    with pytest.raises(InvalidRequestError):
        vault.summarizer.summarize(paths, **kwargs)  # type: ignore[arg-type]


def test_summarize_all_unreadable_is_a_fault(
    make_vault: VaultFactory,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = make_vault(summarizer=FakeSummarizer(), notes={"a.md": "# A\n\nx"})

    def unreadable(path: str, **_: object) -> None:
        raise DocumentUnreadableError(path, "boom")

    monkeypatch.setattr(vault._doc_mgr, "read", unreadable)
    with pytest.raises(ValueError, match="server cannot read them") as exc:
        vault.summarizer.summarize(["a.md"])
    assert not isinstance(exc.value, InvalidRequestError)


def test_summarize_does_not_skip_a_fault_as_a_bad_note(
    make_vault: VaultFactory,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = make_vault(summarizer=FakeSummarizer(), notes={"a.md": "# A\n\nx"})

    def broken(_path: str, **_: object) -> None:
        raise ValueError("internal failure")

    monkeypatch.setattr(vault._doc_mgr, "read", broken)
    with pytest.raises(ValueError, match="internal failure"):
        vault.summarizer.summarize(["a.md"])


def test_attachment_removed_before_its_read_is_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _ = _store(tmp_path)
    (tmp_path / "a.png").write_bytes(b"x")

    def vanished(self: Path) -> bytes:
        raise FileNotFoundError(self)

    monkeypatch.setattr(type(tmp_path), "read_bytes", vanished)
    with pytest.raises(DocumentNotFoundError):
        store.read("a.png")


def test_summarize_skips_a_path_with_a_nul_byte(
    make_vault: VaultFactory,  # noqa: F811
) -> None:
    """One malformed path is skipped, not a fault that drops the good note (#1636)."""
    vault = make_vault(summarizer=FakeSummarizer(), notes={"a.md": "# A\n\nx"})
    result = vault.summarizer.summarize(["a.md", "b\x00.md"])
    assert [s.path for s in result.sources] == ["a.md"]
