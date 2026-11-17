"""Content-handling configuration (attachments, read limits, folders)."""

from __future__ import annotations

# Imported at runtime (not under TYPE_CHECKING) so the frozen dataclass's field
# annotation stays resolvable if anything introspects it via get_type_hints.
# (TC003 is suppressed for this file in pyproject.toml, matching indexing.py.)
from collections.abc import Sequence
from dataclasses import dataclass

from markdown_vault_mcp.exceptions import ConfigurationError
from markdown_vault_mcp.okf_capabilities import (
    DEFAULT_VERIFY,
    DefaultVerify,
    OkfCapabilities,
)


@dataclass(frozen=True)
class ContentConfig:
    """Attachment/note-read limits, template/prompt folders, conventions file.

    ``conventions_file`` names the well-known per-folder conventions file
    (``None`` disables the folder-conventions feature entirely).

    ``okf_mode`` controls OKF (Open Knowledge Format) read semantics:
    ``"auto"`` follows the vault's ``okf_version`` declaration in the root
    ``index.md``, ``"off"`` never applies OKF semantics, ``"on"`` forces
    them without a declaration.

    ``okf_verify`` governs how the ``okf_verify`` tool attests a human review
    (only meaningful when ``okf_write`` is on, which gates the tool):
    ``"elicit"`` (default) requires an affirmative MCP elicitation and fails
    closed otherwise, ``"trust-auth"`` attributes to a token's ``sub`` with no
    confirmation and refuses service principals (client-ID-only identities
    including static bearer credentials, application-marked tokens, and
    ``service_subjects``), ``"off"`` hides the tool.

    ``service_subjects`` lists token ``sub`` values the operator declares are
    applications; the identity layer resolves them to service principals
    (#1480).
    """

    attachment_extensions: Sequence[str] | None = None
    max_attachment_size_mb: float = 1.0  # MB; 0 = unlimited
    max_note_read_bytes: int = 262144  # 256 KB; 0 = unlimited
    templates_folder: str = "_templates"
    prompts_folder: str | None = None
    conventions_file: str | None = "_conventions.md"
    okf_mode: str = "auto"
    okf_write: bool = False
    okf_verify: str = DEFAULT_VERIFY
    service_subjects: Sequence[str] | None = None
    okf_author: bool | None = None
    okf_index: bool | None = None
    okf_log: str | None = None
    okf_folders: Sequence[str] | None = None

    @property
    def okf_capabilities(self) -> OkfCapabilities:
        """Resolve configured authority without applying runtime restrictions."""
        return OkfCapabilities(
            author=self.okf_author if self.okf_author is not None else False,
            verify=("elicit" if self.okf_write else "off")
            if isinstance(self.okf_verify, DefaultVerify)
            else self.okf_verify,
            index=self.okf_index if self.okf_index is not None else self.okf_write,
            log=self.okf_log
            if self.okf_log is not None
            else ("writes" if self.okf_write else "off"),
            folders=tuple(self.okf_folders)
            if self.okf_folders is not None
            else (("**",) if self.okf_write else (".",)),
            legacy_stamping=self.okf_write,
        )

    def __post_init__(self) -> None:
        """Validate size limits (#638) and freeze attachment_extensions (#639).

        ``0`` is a valid sentinel for "unlimited"; only negative size values are
        rejected. ``attachment_extensions`` accepts any ``Sequence[str]`` but is
        stored as a tuple so the frozen config's contents cannot be mutated; a
        bare ``str``/``bytes`` is rejected (it would otherwise be silently split
        into characters).

        ``service_subjects`` is frozen the same way, each entry stripped and
        empty entries dropped (``None`` when none remain).

        Raises:
            ConfigurationError: If ``service_subjects`` is a ``str``/``bytes``
                or holds a non-string entry, or ``max_attachment_size_mb`` or
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
        if self.service_subjects is not None:
            if isinstance(self.service_subjects, (str, bytes)):
                raise ConfigurationError(
                    "service_subjects must be a sequence of strings, not a "
                    f"single {type(self.service_subjects).__name__}"
                )
            if not all(isinstance(s, str) for s in self.service_subjects):
                raise ConfigurationError("service_subjects entries must be strings")
            # Padded or empty entries would silently never match a sub.
            stripped = tuple(s.strip() for s in self.service_subjects if s.strip())
            object.__setattr__(self, "service_subjects", stripped or None)
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
        if self.okf_mode not in ("auto", "off", "on"):
            raise ConfigurationError(
                f"okf_mode must be one of auto/off/on, got {self.okf_mode!r}"
            )
        # The enforced write layer changes bytes / write outcomes; it is
        # meaningless without read semantics, so it cannot combine with
        # okf_mode=off (design §2 trust model / §6).
        if self.okf_write and self.okf_mode == "off":
            raise ConfigurationError(
                "okf_write=true requires okf_mode to be auto or on, not off"
            )
        if self.okf_verify not in ("elicit", "off", "trust-auth"):
            raise ConfigurationError(
                "okf_verify must be one of elicit/off/trust-auth, got "
                f"{self.okf_verify!r}"
            )
        if self.okf_log not in (None, "off", "events", "writes"):
            raise ConfigurationError("okf_log must be one of off/events/writes")
        if self.okf_folders is not None:
            if isinstance(self.okf_folders, (str, bytes)) or not all(
                isinstance(folder, str) and folder.strip()
                for folder in self.okf_folders
            ):
                raise ConfigurationError(
                    "okf_folders must be a sequence of non-empty globs"
                )
            object.__setattr__(self, "okf_folders", tuple(self.okf_folders))
        caps = self.okf_capabilities
        if self.okf_mode == "off" and (
            caps.author or caps.verify != "off" or caps.index or caps.log != "off"
        ):
            raise ConfigurationError(
                "OKF write capabilities require okf_mode to be auto or on"
            )
