"""Synthetic metadata-only coverage for the local UTC window command."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from markdown_vault_mcp._file_inventory import ScanLimits, Window, scan_current_files
from markdown_vault_mcp.cli import app
from markdown_vault_mcp.config_sections.vault_settings import VaultSettings

_START = "2026-11-14T00:00:00Z"
_END = "2026-11-15T00:00:00Z"
_NANOSECOND = 1_000_000_000
_START_NS = 1794614400 * _NANOSECOND


def _file(root: Path, name: str, stamp: int = _START_NS) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    os.utime(path, ns=(stamp, stamp))
    return path


def test_inventory_uses_only_current_eligible_metadata(tmp_path: Path) -> None:
    for name in (
        "note.md",
        "untyped.md",
        "broken.md",
        "image.PDF",
        "denied.exe",
        ".hidden.md",
        ".hidden/note.md",
        "excluded/note.md",
        "_conventions.md",
        "sub/_conventions.md",
        "UPPER.MD",
    ):
        _file(tmp_path, name)
    _file(tmp_path, "broken.md").write_bytes(b"---\ninvalid: [")
    os.utime(tmp_path / "broken.md", ns=(_START_NS, _START_NS))
    settings = VaultSettings(
        required_frontmatter=["required"],
        exclude_patterns=["excluded/**"],
        conventions_file="_conventions.md",
        attachment_extensions=["pdf"],
    )
    with patch.object(Path, "open", side_effect=AssertionError("contents opened")):
        result = scan_current_files(
            tmp_path, Window(_START, _END), settings, ScanLimits()
        )
    assert result["complete"] is True
    assert result["incomplete_reasons"] == []
    assert result["items"] == [
        {"path": name, "kind": kind, "modified_at": _START}
        for name, kind in [
            ("broken.md", "markdown"),
            ("image.PDF", "attachment"),
            ("note.md", "markdown"),
            ("untyped.md", "markdown"),
        ]
    ]
    assert result["source_dir"] == str(tmp_path)
    assert result["basis"] == "current_file_mtime"


def test_half_open_nanosecond_bounds_and_offsets(tmp_path: Path) -> None:
    for name, delta in [
        ("before.md", -1),
        ("start.md", 0),
        ("inside.md", 1),
        ("end.md", 2),
    ]:
        _file(tmp_path, name, _START_NS + delta)
    window = Window("2026-11-14T01:00:00+01:00", "2026-11-14T00:00:00.000000002Z")
    result = scan_current_files(tmp_path, window, VaultSettings(), ScanLimits())
    assert [item["path"] for item in result["items"]] == ["inside.md", "start.md"]
    assert result["start"] == _START
    assert result["items"][0]["modified_at"] == "2026-11-14T00:00:00.000000001Z"
    subnano = Window(
        "2026-11-14T00:00:00.0000000001Z", "2026-11-14T00:00:00.0000000011Z"
    )
    assert [
        item["path"]
        for item in scan_current_files(
            tmp_path, subnano, VaultSettings(), ScanLimits()
        )["items"]
    ] == ["inside.md"]


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-11-14", _END),
        ("2026-11-14T00:00:00", _END),
        ("2026-02-30T00:00:00Z", _END),
        (_END, _START),
        (_START, _START),
        (_START + ".123", _END),
        ("2026-11-14T00:00:60Z", _END),
        ("0001-01-01T00:00:00+01:00", _END),
    ],
)
def test_refuses_invalid_or_ambiguous_bounds(start: str, end: str) -> None:
    with pytest.raises(ValueError):
        Window(start, end)


def test_scan_limits_keep_partial_json(tmp_path: Path) -> None:
    _file(tmp_path, "a.md")
    _file(tmp_path, "b.md")
    result = scan_current_files(
        tmp_path, Window(_START, _END), VaultSettings(), ScanLimits(entries=1)
    )
    assert result["complete"] is False
    assert result["incomplete_reasons"] == ["scan_limit"]
    assert len(result["items"]) == 1
    with patch(
        "markdown_vault_mcp._file_inventory.time.monotonic", side_effect=[0, 11]
    ):
        result = scan_current_files(
            tmp_path, Window(_START, _END), VaultSettings(), ScanLimits()
        )
    assert result["incomplete_reasons"] == ["deadline"]
    assert result["items"] == []


def test_unavailable_subtree_has_no_path_diagnostic(tmp_path: Path) -> None:
    _file(tmp_path, "visible.md")
    _file(tmp_path, "unavailable/note.md")
    _file(tmp_path, ".hidden/note.md")
    _file(tmp_path, "excluded/note.md")
    original = os.open

    def observed(path: str, flags: int, *, dir_fd: int | None = None) -> int:
        assert path not in {".hidden", "excluded"}
        if path == "unavailable":
            raise PermissionError("private diagnostic must not be emitted")
        return original(path, flags, dir_fd=dir_fd)

    with patch("markdown_vault_mcp._file_inventory.os.open", side_effect=observed):
        result = scan_current_files(
            tmp_path,
            Window(_START, _END),
            VaultSettings(exclude_patterns=["excluded/**"]),
            ScanLimits(),
        )
    assert result["complete"] is False
    assert result["incomplete_reasons"] == ["directory_unavailable"]
    assert [item["path"] for item in result["items"]] == ["visible.md"]
    assert "private diagnostic" not in json.dumps(result)
    assert "unavailable/" not in json.dumps(result)


def test_cli_no_index_or_content_collaborators(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _file(tmp_path, "note.md")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SERVER_URL", "https://example.test/mcp")
    args = ["changed-files", "--start", _START, "--end", _END]
    refused = CliRunner().invoke(app, args)
    assert refused.exit_code == 2
    assert "local only" in refused.output
    with (
        patch(
            "markdown_vault_mcp.cli._build_vault",
            side_effect=AssertionError("vault built"),
        ),
        patch(
            "markdown_vault_mcp.config_sections._assembly.to_vault_instances",
            side_effect=AssertionError("collaborators built"),
        ),
    ):
        result = CliRunner().invoke(app, [*args, "--local"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["source_dir"] == str(tmp_path)
    assert payload["items"][0]["path"] == "note.md"
    limited = CliRunner().invoke(app, [*args, "--local", "--max-entries", "1"])
    assert limited.exit_code == 1
    assert json.loads(limited.stdout)["complete"] is False
    missing = CliRunner().invoke(
        app, [*args, "--local", "--source-dir", str(tmp_path / "absent")]
    )
    assert missing.exit_code == 1
    assert "unavailable" in missing.output


def test_metadata_failures_and_symlinks_are_generic(tmp_path: Path) -> None:
    entries = []
    for name, mode in [
        ("link.md", stat.S_IFLNK),
        ("pipe", stat.S_IFIFO),
        (".hidden", stat.S_IFLNK),
        ("excluded.md", stat.S_IFLNK),
        ("unreadable.md", stat.S_IFREG),
    ]:
        entry = MagicMock(spec=os.DirEntry)
        entry.name, entry.path = name, str(tmp_path / name)
        entry.is_dir.return_value = False
        entry.stat.return_value = SimpleNamespace(st_mode=mode)
        if name == "unreadable.md":
            entry.stat.side_effect = PermissionError("never print this path")
        entries.append(entry)
    stream = MagicMock()
    stream.__enter__.return_value = iter(entries)
    with patch(
        "markdown_vault_mcp._file_inventory.os.scandir", return_value=stream
    ) as walk:
        result = scan_current_files(
            tmp_path,
            Window(_START, _END),
            VaultSettings(exclude_patterns=["excluded.md"]),
            ScanLimits(),
        )
    assert walk.call_count == 1
    assert result["items"] == []
    assert result["incomplete_reasons"] == ["entry_unavailable", "unsupported_symlink"]
    assert result["complete"] is False
    entries[2].stat.assert_not_called()
    entries[3].stat.assert_not_called()
    assert "unreadable" not in json.dumps(result)


def test_directory_pruning_does_not_hide_a_same_named_file(tmp_path: Path) -> None:
    _file(tmp_path, "archive")
    result = scan_current_files(
        tmp_path,
        Window(_START, _END),
        VaultSettings(exclude_patterns=["archive/**"], attachment_extensions=["*"]),
        ScanLimits(),
    )
    assert result["complete"] is True
    assert result["items"][0]["path"] == "archive"


@pytest.mark.parametrize(
    "option",
    [
        ["--max-entries", "0"],
        ["--max-entries", "1000001"],
        ["--max-seconds", "nan"],
        ["--max-seconds", "inf"],
    ],
)
def test_cli_rejects_invalid_limits(option: list[str]) -> None:
    result = CliRunner().invoke(
        app, ["changed-files", "--start", _START, "--end", _END, "--local", *option]
    )
    assert result.exit_code == 2


def test_matching_directory_name_does_not_exclude_descendants(tmp_path: Path) -> None:
    _file(tmp_path, "archive/note.md")
    result = scan_current_files(
        tmp_path,
        Window(_START, _END),
        VaultSettings(exclude_patterns=["archive"]),
        ScanLimits(),
    )
    assert result["complete"] is True
    assert result["items"][0]["path"] == "archive/note.md"


def test_no_follow_directory_primitives_and_unsupported_platform(
    tmp_path: Path,
) -> None:
    _file(tmp_path, "sub/note.md")
    original = os.open
    with patch("markdown_vault_mcp._file_inventory.os.open", wraps=original) as opened:
        result = scan_current_files(
            tmp_path, Window(_START, _END), VaultSettings(), ScanLimits()
        )
    assert result["complete"] is True
    for call in opened.call_args_list:
        assert call.args[1] & os.O_NOFOLLOW
        assert call.args[1] & os.O_DIRECTORY
        if call.args[0] != tmp_path.anchor:
            assert isinstance(call.kwargs["dir_fd"], int)
    with (
        patch("markdown_vault_mcp._file_inventory._FD_WALK_SUPPORTED", False),
        patch(
            "markdown_vault_mcp._file_inventory.os.open",
            side_effect=AssertionError("unsupported traversal"),
        ),
    ):
        result = scan_current_files(
            tmp_path, Window(_START, _END), VaultSettings(), ScanLimits()
        )
    assert result["incomplete_reasons"] == ["unsupported_platform"]
    assert result["complete"] is False
    assert result["items"] == []
