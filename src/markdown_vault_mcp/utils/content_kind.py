"""Which kind of thing a vault path names (#1235).

Every routing test that asks *"is this path a markdown note?"* lives here, so
there is one spelling of the question instead of one per call site.

**Three axes** ride on the ``.md`` extension.  They ask the same question and
share :func:`is_note`; what differs — and what must **not** be unified — is
what a *non-note* means:

* **ARTIFACT** — an attachment.  Decides which CRUD path a write takes and
  which validator runs (``DocumentManager``, ``ArtifactStore``, the transfer
  sink, the ``read``/``write``/``fetch`` tools).
* **FOLDER** — a directory scope: ``DocumentManager.get_toc`` and
  ``SummarizeManager._expand_path`` treat a non-note path as a subtree prefix,
  and ``ConventionsResolver`` folds a note path to its parent folder.
* **INDEXABILITY** — not a candidate for the index: ``IndexManager``,
  ``EmbeddingsManager``, ``utils.fs.iter_markdown_files``.

Collapsing those three into one tri-state would make the folder sites claim a
path is "an artifact" when they mean "a folder", so the distinction is kept in
prose rather than in the type.

**Two divergences are deliberate and PRESERVED here, not unified** (#1240):

* **Case.**  Routing is case-sensitive (:func:`is_note`).
  ``SearchManager._attachment_info`` is case-*insensitive* and uses
  :func:`has_md_suffix`.  ``DocumentManager.read``'s note-size cap uses a third
  spelling (``path.lower().endswith(".md")``) and stays inline, because that
  method validates no extension at all — a file named ``NOTE.MD`` really does
  reach it and really does get the cap.
* **Resolution.**  Some callers take the extension from the raw caller string
  (before the traversal guard), others from the ``.resolve()``d path (after).
  Symlinks make those differ: ``link.pdf -> target.bin`` is ``pdf`` by name and
  ``bin`` by target.  Nothing here resolves — each caller passes the object
  whose extension it means.

There is deliberately no ``is_attachment(path, extensions)`` helper. It reads
like the routing test and is not one — the tool layer and the transfer sink
route on ``not is_note(path)`` alone, so a non-allowlisted file still reaches
the artifact branch and is rejected there with the error naming
``MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS``. A caller that genuinely wants
"non-note and allowlisted" composes the two predicates at the point of use,
where the surrounding branch shows which it meant. The private method this
module replaced had exactly that misleading name and zero callers (#1235).

Not owned here, because they are not path predicates: glob patterns that state
the same rule in another language (``scanner``'s ``**/*.md``, ``tracker``'s
comparison against it), and name normalization / display (appending ``.md`` to
a bare wikilink, stripping it for a label).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from markdown_vault_mcp.types import DEFAULT_ATTACHMENT_EXTENSIONS

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "artifact_suffix",
    "effective_attachment_extensions",
    "has_md_suffix",
    "is_allowed_artifact",
    "is_allowed_artifact_suffix",
    "is_note",
]


def is_note(path: str) -> bool:
    """Return whether *path* names a markdown note.

    The routing test: case-**sensitive**, matching every dispatch site in the
    codebase.  Use :func:`has_md_suffix` where case-insensitivity is intended.

    Args:
        path: A vault-relative path.

    Returns:
        ``True`` when *path* ends in ``.md``.
    """
    return path.endswith(".md")


def has_md_suffix(path: str | Path) -> bool:
    """Return whether *path* has a ``.md`` suffix, ignoring case.

    Not a substitute for :func:`is_note`: it accepts ``NOTE.MD``, and it reads
    the suffix through :class:`~pathlib.Path`, so a file literally named
    ``.md`` has no suffix and does not match.

    Args:
        path: A vault-relative path.

    Returns:
        ``True`` when the suffix is ``.md`` in any case.
    """
    return Path(path).suffix.lower() == ".md"


def artifact_suffix(path: str | Path) -> str:
    """Return *path*'s extension, lower-cased and without the leading dot.

    Does not resolve symlinks.  Pass a ``str`` to test the caller's spelling,
    or an already-resolved :class:`~pathlib.Path` to test the on-disk target —
    the two differ for a symlink, and that choice belongs to the caller.

    Args:
        path: A vault-relative path or a resolved path.

    Returns:
        The extension, or ``""`` when there is none.
    """
    return Path(path).suffix.lstrip(".").lower()


def is_allowed_artifact_suffix(suffix: str, extensions: frozenset[str]) -> bool:
    """Return whether a bare *suffix* is in the attachment allowlist.

    Args:
        suffix: A dot-less, lower-cased extension, as from
            :func:`artifact_suffix`.
        extensions: The effective allowlist; ``{"*"}`` allows everything.

    Returns:
        ``True`` when the suffix is allowed.
    """
    return "*" in extensions or suffix in extensions


def is_allowed_artifact(path: str | Path, extensions: frozenset[str]) -> bool:
    """Return whether *path*'s extension is in the attachment allowlist.

    Says nothing about ``.md`` — a note passes this test whenever ``md`` is
    allowlisted.  Combine with :func:`is_note` when both matter, at the point
    of use rather than through a helper (see the module docstring).

    Args:
        path: A vault-relative path or a resolved path (see
            :func:`artifact_suffix` on which to pass).
        extensions: The effective allowlist.

    Returns:
        ``True`` when the extension is allowed.
    """
    return is_allowed_artifact_suffix(artifact_suffix(path), extensions)


def effective_attachment_extensions(
    attachment_extensions: Sequence[str] | None,
) -> frozenset[str]:
    """Return the effective set of allowed attachment extensions.

    Note the configured values are used verbatim: unlike the path side, they
    are not lower-cased or dot-stripped, so a config of ``["PDF"]`` or
    ``[".pdf"]`` matches nothing. That asymmetry is pre-existing and pinned by
    a test rather than fixed here (#1239).

    Args:
        attachment_extensions: User-configured extension list, or ``None``
            to use the default set.

    Returns:
        Frozenset of lower-case extension strings (without leading dot).
        The special value ``frozenset(["*"])`` means all non-.md files.
    """
    if attachment_extensions is None:
        return DEFAULT_ATTACHMENT_EXTENSIONS
    return frozenset(attachment_extensions)
