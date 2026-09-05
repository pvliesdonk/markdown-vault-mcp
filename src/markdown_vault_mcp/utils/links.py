"""Link target helpers: decoding, replacement computation, substitution.

:func:`decode_link_target` is shared with link extraction; the rest compute
replacement link targets and apply substitutions in file content when a note
is renamed within the vault.
"""

from __future__ import annotations

import os.path as osp
import re
from pathlib import Path
from urllib.parse import quote, unquote

#: Characters left unescaped when re-encoding a destination that the author
#: already wrote percent-encoded. Only ``/`` — it is the path separator, and
#: encoding it would change the link's structure rather than its spelling.
_QUOTE_SAFE = "/"

#: An encoded path separator, which makes a destination unresolvable rather
#: than merely undecoded. A percent-escaped reserved character is data, not
#: structure, so ``dir%2Fnote.md`` names one path segment containing a slash.
#: Decoding it would point the link at the unrelated note at ``dir/note.md``;
#: keeping the raw spelling would point it at a file literally called
#: ``dir%2Fnote.md``. Both are files the destination does not name, so
#: :func:`decode_link_target` refuses it outright.
_RE_ENCODED_SEPARATOR = re.compile(r"%2[Ff]")


def decode_link_target(target: str) -> str | None:
    """Percent-decode a link destination, or refuse it.

    CommonMark defines a markdown destination as a URL, so its escapes name
    the same file the literal spelling does (#1332). Two escapes name no file
    at all, and are refused rather than decoded:

    * **An encoded separator** (``%2F``). It is path *data*, not a path
      separator, so the destination names one path segment containing a
      slash — which no file can be called.
    * **An escape sequence that is not valid UTF-8** (``bad%FF.md``). It
      names bytes that are not a UTF-8 name. ``unquote`` would substitute
      U+FFFD, inventing one, and collapse distinct malformed sequences onto
      it.

    Refusing returns ``None`` rather than the raw string, because the raw
    string is itself a valid path: a vault containing a file literally called
    ``dir%2Fnote.md`` would otherwise match it, giving a destination that
    names nothing a resolved target and a backlink to an unrelated note. A
    file genuinely called that is reached by encoding the percent
    (``dir%252Fnote.md``), which decodes here without being refused.

    Args:
        target: The destination's path portion, fragment already split off.

    Returns:
        The decoded path, or ``None`` when the destination carries an escape
        that names no possible file.
    """
    if _RE_ENCODED_SEPARATOR.search(target):
        return None
    try:
        return unquote(target, errors="strict")
    except UnicodeDecodeError:
        return None


def compute_new_raw_target(
    link_type: str,
    raw_target: str,
    fragment: str | None,
    new_path: str,
    source_path: str = "",
    old_path: str = "",
) -> str:
    """Compute the replacement raw_target string when a file is renamed.

    Args:
        link_type: One of ``"markdown"``, ``"reference"``, ``"wikilink"``.
        raw_target: The literal link string stored in the source file.
        fragment: The heading fragment (``#heading``) of the link, if any.
        new_path: The vault-relative path of the renamed file (e.g.
            ``"notes/new-name.md"``).
        source_path: Vault-relative path of the file that contains the link.
            Required for correct relative-path handling in markdown and
            reference links (cross-directory links would otherwise be silently
            broken).
        old_path: Vault-relative path of the file being renamed.  Used to
            detect whether *raw_target* was written as a vault-root-relative
            or source-directory-relative path.

    Returns:
        The replacement raw_target string to write into the source file,
        written in the same shape *and the same spelling* the original used:
        a destination the author percent-encoded is re-encoded, one written
        literally stays literal (#1105, #1332).
    """
    if link_type == "wikilink":
        # Determine whether the original wikilink included the .md extension.
        old_path_part = raw_target.split("#")[0]
        if old_path_part.lower().endswith(".md"):
            new_path_part = new_path
        else:
            new_path_part = new_path[:-3]
        return new_path_part + ("#" + fragment if fragment else "")
    else:
        # markdown and reference links, in one of three shapes:
        #   "/folder/target.md"  leading-slash root-relative (#969, and what
        #                        OKF recommends and its tooling emits)
        #   "folder/target.md"   root-relative, matching old_path
        #   "../target.md"       relative to the source file's directory
        # Each is rewritten in its own shape: the link still resolves either
        # way, but silently converting one spelling to another undoes a
        # vault's OKF link conformance on any rename or folder move (#1105).
        raw_path_part = raw_target.split("#")[0]
        # The shape test below compares the destination to old_path, and
        # old_path is never encoded. Comparing the encoded spelling therefore
        # never matched, so a root-relative encoded link fell into the
        # relative-to-source branch and came back rewritten as a relative
        # one — the same fidelity defect as #1105, reached by a different
        # route (#1332). Decode for the comparison; re-encode the answer only
        # if the author was encoding. A refused destination names no file, so
        # there is no encoding convention to preserve and it is compared as
        # written — such a link is never stored, so this arm is defensive.
        decoded_path_part = decode_link_target(raw_path_part) or raw_path_part
        was_encoded = decoded_path_part != raw_path_part

        if raw_path_part.startswith("/"):
            new_path_part = "/" + new_path
        elif source_path and old_path and decoded_path_part != old_path:
            # Relative-to-source link: compute the correct new relative path so
            # cross-directory links continue to resolve after the rename.
            source_dir = str(Path(source_path).parent)
            new_rel = osp.relpath(new_path, source_dir)
            # os.path.relpath uses OS separators on Windows; normalise to /.
            new_path_part = new_rel.replace("\\", "/")
        else:
            new_path_part = new_path
        if was_encoded:
            new_path_part = quote(new_path_part, safe=_QUOTE_SAFE)
        return new_path_part + ("#" + fragment if fragment else "")


def apply_link_replacement(
    content: str, link_type: str, old_raw: str, new_raw: str
) -> str:
    """Replace a single link target occurrence in file content.

    Args:
        content: Full file content to modify.
        link_type: One of ``"markdown"``, ``"reference"``, ``"wikilink"``.
        old_raw: The original raw_target string to find.
        new_raw: The replacement raw_target string.

    Returns:
        Updated content with all occurrences of *old_raw* replaced.
    """
    if link_type == "markdown":
        # Negative lookbehind (?<!!) excludes image links ![](url) — the `!`
        # immediately before `[` is the discriminator. Anchored to [text]( so
        # bare (old_raw) occurrences in plain text are also excluded.
        # Captures and preserves optional link title (e.g. "title" or 'title').
        # NOTE: operates on raw file content; occurrences inside backtick code
        # spans would also be rewritten. Risk is low in practice.
        return re.sub(
            r"(?<!!)(\[[^\]]*?\])\(" + re.escape(old_raw) + r"((?:\s[^)]*)?)\)",
            lambda m: m.group(1) + "(" + new_raw + m.group(2) + ")",
            content,
        )
    elif link_type == "reference":
        # Match reference definition lines: [id]: url optional-title
        # Anchored to line start with MULTILINE so we don't match inline text.
        return re.sub(
            r"^(\[.*?\]:\s+)" + re.escape(old_raw) + r"([ \t].*|$)",
            lambda m: m.group(1) + new_raw + m.group(2),
            content,
            flags=re.MULTILINE,
        )
    elif link_type == "wikilink":
        return re.sub(
            r"\[\[" + re.escape(old_raw) + r"(\|[^\]]*)?\]\]",
            lambda m: "[[" + new_raw + (m.group(1) or "") + "]]",
            content,
        )
    return content
