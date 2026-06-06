"""Content-handling configuration (attachments, read limits, folders)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContentConfig:
    """Attachment/note-read limits and template/prompt folder paths."""

    attachment_extensions: list[str] | None = None
    max_attachment_size_mb: float = 1.0  # MB; 0 = unlimited
    max_note_read_bytes: int = 262144  # 256 KB; 0 = unlimited
    templates_folder: str = "_templates"
    prompts_folder: str | None = None

    @classmethod
    def from_env(cls, prefix: str, source_dir: Path) -> ContentConfig:
        """Construct ContentConfig by reading ``{prefix}_*`` env vars.

        Invalid ``MAX_ATTACHMENT_SIZE_MB``/``MAX_NOTE_READ_BYTES`` values
        warn and reset to the default.  ``TEMPLATES_FOLDER`` has backslash and
        trailing-slash normalization applied.  ``PROMPTS_FOLDER`` is joined to
        ``source_dir`` when relative.

        Args:
            prefix: Env var prefix, e.g. ``"MARKDOWN_VAULT_MCP"``.
            source_dir: Vault root; used to resolve a relative prompts folder.

        Returns:
            Populated ContentConfig with defaults for unset vars.
        """
        from fastmcp_pvl_core import parse_list

        from markdown_vault_mcp.config_sections._helpers import (
            env,
        )

        # --- attachment_extensions ---
        raw_exts = (env(prefix, "ATTACHMENT_EXTENSIONS") or "").strip()
        if not raw_exts:
            attachment_extensions: list[str] | None = None
        elif raw_exts == "*":
            attachment_extensions = ["*"]
        else:
            attachment_extensions = parse_list(raw_exts) or None

        # --- max_attachment_size_mb ---
        raw_max_att = (env(prefix, "MAX_ATTACHMENT_SIZE_MB") or "").strip()
        if raw_max_att:
            try:
                max_attachment_size_mb = float(raw_max_att)
            except ValueError:
                logger.warning(
                    "from_env: invalid MAX_ATTACHMENT_SIZE_MB=%r, using default 1.0",
                    raw_max_att,
                )
                max_attachment_size_mb = 1.0
            else:
                if max_attachment_size_mb < 0:
                    logger.warning(
                        "from_env: MAX_ATTACHMENT_SIZE_MB=%r is negative, using default 1.0",
                        max_attachment_size_mb,
                    )
                    max_attachment_size_mb = 1.0
        else:
            max_attachment_size_mb = 1.0

        # --- max_note_read_bytes ---
        raw_max_note = (env(prefix, "MAX_NOTE_READ_BYTES") or "").strip()
        if raw_max_note:
            try:
                max_note_read_bytes = int(raw_max_note)
            except ValueError:
                logger.warning(
                    "from_env: invalid MAX_NOTE_READ_BYTES=%r, using default 262144",
                    raw_max_note,
                )
                max_note_read_bytes = 262144
            else:
                if max_note_read_bytes < 0:
                    logger.warning(
                        "from_env: MAX_NOTE_READ_BYTES=%r is negative, using default 262144",
                        max_note_read_bytes,
                    )
                    max_note_read_bytes = 262144
        else:
            max_note_read_bytes = 262144

        # --- templates_folder ---
        raw_templates = (env(prefix, "TEMPLATES_FOLDER") or "").strip()
        templates_folder = raw_templates.replace("\\", "/").strip("/") or "_templates"

        # --- prompts_folder ---
        raw_prompts = (env(prefix, "PROMPTS_FOLDER") or "").strip()
        if raw_prompts:
            pf = Path(raw_prompts.replace("\\", "/"))
            if not pf.is_absolute():
                pf = source_dir / pf
            prompts_folder: str | None = str(pf)
        else:
            prompts_folder = None

        return cls(
            attachment_extensions=attachment_extensions,
            max_attachment_size_mb=max_attachment_size_mb,
            max_note_read_bytes=max_note_read_bytes,
            templates_folder=templates_folder,
            prompts_folder=prompts_folder,
        )
