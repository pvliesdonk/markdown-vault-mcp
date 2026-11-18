from pathlib import Path

from watchdog.events import DirModifiedEvent

from markdown_vault_mcp._file_watcher import (
    _ATTRIBUTE_EVENT_CLASSES,
    _VaultEventHandler,
    _WatchRoot,
)


def test_legacy_directory_metadata(tmp_path: Path) -> None:
    folder = tmp_path / "locked"
    folder.mkdir()
    calls: list[str] = []
    handler = _VaultEventHandler(
        lambda: calls.append("search"),
        tmp_path,
        observe=lambda: calls.append("observe"),
        root_info=_WatchRoot(tmp_path, True, False),
        sync=lambda _path, _rebuild: False,
    )
    handler.on_any_event(DirModifiedEvent(str(folder)))
    assert calls == ["observe"]
    assert isinstance(_ATTRIBUTE_EVENT_CLASSES, list)
