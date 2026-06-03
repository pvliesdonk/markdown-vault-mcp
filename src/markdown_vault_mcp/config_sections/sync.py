"""External-change-detection configuration (file watcher + webhook)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SyncConfig:
    """File-watcher + GitHub-webhook settings for external changes."""

    file_watcher_enabled: bool = True
    file_watcher_debounce_s: float = 2.0
    github_webhook_secret: str | None = None
