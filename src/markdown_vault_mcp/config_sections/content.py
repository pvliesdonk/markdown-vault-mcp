"""Content-handling configuration (attachments, read limits, folders)."""

from __future__ import annotations

# Imported at runtime (not under TYPE_CHECKING) so the frozen dataclass's field
# annotation stays resolvable if anything introspects it via get_type_hints.
# (TC003 is suppressed for this file in pyproject.toml, matching indexing.py.)
from collections.abc import Sequence
from dataclasses import dataclass

from markdown_vault_mcp.exceptions import ConfigurationError


@dataclass(frozen=True)
class ContentConfig:
    """Attachment/note-read limits, template/prompt folders, conventions file.

    ``conventions_file`` names the well-known per-folder conventions file
    (``None`` disables the folder-conventions feature entirely).
    """

    attachment_extensions: Sequence[str] | None = None
    max_attachment_size_mb: float = 1.0  # MB; 0 = unlimited
    max_note_read_bytes: int = 262144  # 256 KB; 0 = unlimited
    templates_folder: str = "_templates"
    prompts_folder: str | None = None
    conventions_file: str | None = "_conventions.md"

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
        if self.conventions_file is not None:
            cf = self.conventions_file
            if "/" in cf or "\\" in cf or not cf.endswith(".md"):
                raise ConfigurationError(
                    "conventions_file must be a bare '.md' filename "
                    f"(no path separators), got {cf!r}"
                )
            # The filename is used verbatim as an fnmatch exclude pattern;
            # metacharacters would invert the exclusion (the real file gets
            # indexed while unrelated matching notes silently vanish).
            if any(ch in cf for ch in "*?[]"):
                raise ConfigurationError(
                    "conventions_file must not contain fnmatch metacharacters "
                    f"(*, ?, [, ]), got {cf!r}"
                )
