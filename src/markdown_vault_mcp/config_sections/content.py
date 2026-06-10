"""Content-handling configuration (attachments, read limits, folders)."""

from __future__ import annotations

# Imported at runtime (not under TYPE_CHECKING) so the frozen dataclass's field
# annotation stays resolvable if anything introspects it via get_type_hints.
# (TC003 is suppressed for this file in pyproject.toml, matching indexing.py.)
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from markdown_vault_mcp.exceptions import ConfigurationError


@dataclass(frozen=True)
class ContentConfig:
    """Attachment/note-read limits and template/prompt folder paths."""

    attachment_extensions: Sequence[str] | None = None
    max_attachment_size_mb: float = 1.0  # MB; 0 = unlimited
    max_note_read_bytes: int = 262144  # 256 KB; 0 = unlimited
    templates_folder: str = "_templates"
    prompts_folder: str | None = None

    def __post_init__(self) -> None:
        """Validate size limits (#638) and freeze attachment_extensions (#639).

        ``0`` is a valid sentinel for "unlimited"; only negative size values are
        rejected. ``attachment_extensions`` accepts any ``Sequence[str]`` but is
        stored as a tuple so the frozen config's contents cannot be mutated; a
        bare ``str``/``bytes`` is rejected (it would otherwise be silently split
        into characters).

        Raises:
            ConfigurationError: If ``max_attachment_size_mb`` or
                ``max_note_read_bytes`` is negative, or ``attachment_extensions``
                is a ``str``/``bytes`` instead of a sequence of strings.
        """
        if self.attachment_extensions is not None:
            if isinstance(self.attachment_extensions, (str, bytes)):
                raise ConfigurationError(
                    "attachment_extensions must be a sequence of strings, not a "
                    f"single {type(self.attachment_extensions).__name__}"
                )
            if not isinstance(self.attachment_extensions, tuple):
                object.__setattr__(
                    self, "attachment_extensions", tuple(self.attachment_extensions)
                )
        if self.max_attachment_size_mb < 0:
            raise ConfigurationError(
                "max_attachment_size_mb must be >= 0, got "
                f"{self.max_attachment_size_mb}"
            )
        if self.max_note_read_bytes < 0:
            raise ConfigurationError(
                f"max_note_read_bytes must be >= 0, got {self.max_note_read_bytes}"
            )

    @classmethod
    def from_env(cls, prefix: str, source_dir: Path) -> ContentConfig:
        """Construct ContentConfig by reading ``{prefix}_*`` env vars.

        ``TEMPLATES_FOLDER`` has backslash and trailing-slash normalization
        applied. ``PROMPTS_FOLDER`` is joined to ``source_dir`` when relative.

        Args:
            prefix: Env var prefix, e.g. ``"MARKDOWN_VAULT_MCP"``.
            source_dir: Vault root; used to resolve a relative prompts folder.

        Returns:
            Populated ContentConfig with defaults for unset vars.

        Raises:
            ConfigurationError: If ``MAX_ATTACHMENT_SIZE_MB`` /
                ``MAX_NOTE_READ_BYTES`` is non-numeric or negative.
        """
        from fastmcp_pvl_core import parse_list

        from markdown_vault_mcp.config_sections._helpers import env, env_float, env_int

        # --- attachment_extensions ---
        raw_exts = (env(prefix, "ATTACHMENT_EXTENSIONS") or "").strip()
        if not raw_exts:
            attachment_extensions: list[str] | None = None
        elif raw_exts == "*":
            attachment_extensions = ["*"]
        else:
            attachment_extensions = parse_list(raw_exts) or None

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
            max_attachment_size_mb=env_float(prefix, "MAX_ATTACHMENT_SIZE_MB", 1.0),
            max_note_read_bytes=env_int(prefix, "MAX_NOTE_READ_BYTES", 262144),
            templates_folder=templates_folder,
            prompts_folder=prompts_folder,
        )
