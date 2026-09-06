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
  reach it and really does get the cap.  :func:`names_attachment` is a
  fourth site and is case-*insensitive* on both halves (``[[NOTE.MD]]`` is a
  note, ``[[PIC.PNG]]`` an attachment), so under the ``*`` wildcard a
  ``[[NOTE.MD]]`` reference is a note link to a file routing never indexes:
  a broken link, as it was before #1333, rather than a skipped one.
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
a bare wikilink, stripping it for a label). :func:`names_attachment` is the
one link-target question that does live here — whether a *reference* names an
attachment rather than a note, which is what decides that ``.md`` append and
whether the reference is a link at all (#1333). It is not the routing test:
it classifies a name in a document, not a path on disk.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from markdown_vault_mcp.types import DEFAULT_ATTACHMENT_EXTENSIONS

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "artifact_suffix",
    "canonical_attachment_extensions",
    "effective_attachment_extensions",
    "has_md_suffix",
    "is_allowed_artifact",
    "is_allowed_artifact_suffix",
    "is_note",
    "names_attachment",
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

    Configured values are normalized the way :func:`artifact_suffix` normalizes
    the path side — surrounding whitespace and leading dots stripped, then
    lower-cased — so ``pdf``, ``PDF`` and ``.pdf`` all name the same file type
    (#1239).  The wildcard ``*`` is unaffected.  An entry that normalizes to
    nothing (``"."``, ``" "``) is dropped rather than kept as ``""``, which
    :func:`artifact_suffix` returns for an extension-less path and which would
    silently allowlist files such as ``Makefile``.

    Args:
        attachment_extensions: User-configured extension list, in any case and
            with or without leading dots, or ``None`` to use the default set.

    Returns:
        Frozenset of lower-case extension strings (without leading dot).
        The special value ``frozenset(["*"])`` means all non-.md files.
    """
    if attachment_extensions is None:
        return DEFAULT_ATTACHMENT_EXTENSIONS
    normalized = (ext.strip().lstrip(".").lower() for ext in attachment_extensions)
    return frozenset(ext for ext in normalized if ext)


#: An extension-shaped suffix: what a file type looks like, used only under
#: the ``*`` attachment wildcard (see :func:`names_attachment`). One to eight
#: alphanumerics with at least one letter: ``png``, ``3gp`` and ``7z`` pass;
#: the ``12`` in the note title ``Python 3.12`` does not, because no file
#: type is digits alone.
_RE_EXTENSION_SHAPE = re.compile(r"(?=[0-9]*[A-Za-z])[A-Za-z0-9]{1,8}")


def canonical_attachment_extensions(extensions: frozenset[str]) -> str:
    """Return the allowlist's canonical string form, for build provenance.

    Link extraction consults the allowlist to tell a reference naming an
    attachment from one naming a note (#1333), which makes it a setting that
    changes how a note's stored rows derive from its bytes. The index records
    it, and a warm restart compares it, the way it already does for
    ``title_field`` and the curated field lists.

    The default set renders as the empty string rather than its members: an
    index built before this key existed reads back ``""``, and must compare
    equal to a default-configured server rather than force a rebuild on
    every such deployment.

    Every other allowlist renders as a sorted JSON list, so an *explicitly
    empty* one renders as ``[]``, not as ``""``. The two are different
    configurations that derive different rows — with nothing allowlisted,
    ``[[pic.png]]`` is a note reference storing ``pic.png.md`` — so
    collapsing them onto one value would let a switch between them keep the
    warm index and serve the previous interpretation forever. JSON rather
    than a delimiter-joined string because normalisation restricts a member
    very little (whitespace and leading dots only): a comma-joined form
    rendered ``["a,b"]`` and ``["a", "b"]`` identically, and a bare sentinel
    for the empty list was itself a configurable member.

    Args:
        extensions: The effective allowlist, as returned by
            :func:`effective_attachment_extensions`.

    Returns:
        ``""`` for the default set, else the sorted members as a JSON list
        (``[]`` for an explicitly empty allowlist).
    """
    if extensions == DEFAULT_ATTACHMENT_EXTENSIONS:
        return ""
    return json.dumps(sorted(extensions))


def names_attachment(target: str, extensions: frozenset[str]) -> bool:
    """Return whether a link target names an attachment rather than a note.

    The link graph is notes-only (#1333): a reference whose target names an
    attachment is not recorded as a link, at any extraction site. This is
    the classification that decides it, from the name alone — the file's
    presence on disk is not consulted, and the question is asked of the
    target with any fragment already split off.

    Not the routing test (:func:`is_note` + :func:`is_allowed_artifact`):
    that one answers what ``read`` serves, where under the ``*`` wildcard an
    extensionless ``Makefile`` really is an attachment. A *name* in a
    document under the same wildcard must not read the note title
    ``Version 2.0 plan`` as a ``0 plan`` attachment, nor ``Python 3.12`` as
    a ``12`` one, so here the wildcard additionally requires the suffix to
    look like an extension (:data:`_RE_EXTENSION_SHAPE`). The known cost is
    a title such as ``Release 2.0a``, whose ``0a`` does look like one.

    Args:
        target: Link target with any fragment already split off, as written
            (wikilink) or percent-decoded (markdown / reference link).
        extensions: The effective attachment allowlist, as returned by
            :func:`effective_attachment_extensions`.

    Returns:
        ``True`` when *target* carries a suffix naming an attachment. Always
        ``False`` for a ``.md`` target and for a target with no suffix.
    """
    if has_md_suffix(target):
        # A ``.md`` target is a note in every configuration, including under
        # the ``*`` wildcard, where ``md`` would otherwise be an
        # extension-shaped suffix that matches everything.
        return False
    suffix = artifact_suffix(target)
    if not suffix:
        return False
    if "*" in extensions:
        # The wildcard means "every non-.md file", which says nothing about
        # which *suffixes* are file types; taken literally it reads the note
        # title ``Version 2.0 plan`` as a ``0 plan`` attachment and
        # ``Python 3.12`` as a ``12`` one.
        return _RE_EXTENSION_SHAPE.fullmatch(suffix) is not None
    return suffix in extensions
