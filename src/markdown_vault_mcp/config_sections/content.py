"""Content-handling configuration (attachments, read limits, folders)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ContentConfig:
    """Attachment/note-read limits and template/prompt folder paths."""

    attachment_extensions: list[str] | None = None
    max_attachment_size_mb: float = 1.0  # MB; 0 = unlimited
    max_note_read_bytes: int = 262144  # 256 KB; 0 = unlimited
    templates_folder: str = "_templates"
    prompts_folder: str | None = None
