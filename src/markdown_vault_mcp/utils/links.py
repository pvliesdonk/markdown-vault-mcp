"""Link target helpers: decoding, replacement computation, substitution.

:func:`decode_link_target` is shared with link extraction; the rest compute
replacement link targets and apply substitutions in file content when a note
is renamed within the vault.
"""

from __future__ import annotations

import os.path as osp
import re
from html.entities import html5 as _HTML5_ENTITIES
from pathlib import Path
from urllib.parse import quote, unquote

#: Characters left unescaped when re-encoding a destination that the author
#: already wrote percent-encoded. Only ``/`` — it is the path separator, and
#: encoding it would change the link's structure rather than its spelling.
_QUOTE_SAFE = "/"

#: Escapes that are refused rather than decoded, because decoding would name
#: something no file can be: an encoded path separator (``%2F`` is path
#: *data*, so ``dir%2Fnote.md`` must not become a link to the unrelated note
#: at ``dir/note.md``) and an encoded NUL (no file system allows one in a
#: name). Either case, ``decode_link_target`` leaves the whole destination
#: as written.
_RE_REFUSED_ESCAPE = re.compile(r"%(?:2[Ff]|00)")


def decode_link_target(target: str) -> str:
    """Percent-decode a link destination, or leave it as written.

    CommonMark defines a markdown destination as a URL, so its escapes name
    the same file the literal spelling does (#1332): ``b%5B1%5D.md`` is
    ``b[1].md``. Three escapes are refused, and a refusal leaves the *whole*
    destination exactly as written, so it stays visible to
    ``get_broken_links`` and never resolves onto an unrelated note:

    * an encoded separator (``%2F``) — path data, not structure;
    * an encoded NUL (``%00``) — no file system allows it in a name;
    * an escape sequence that is not valid UTF-8 (``bad%FF.md``) —
      ``unquote`` would substitute U+FFFD, inventing a name and collapsing
      distinct malformed spellings onto one target.

    A ``%`` not followed by two hex digits is not an escape and passes
    through untouched, as does ``+``, which is a space only in form
    encoding.

    Args:
        target: The destination's path portion, fragment already split off.

    Returns:
        The decoded path, or *target* unchanged when it carries a refused
        escape.
    """
    if _RE_REFUSED_ESCAPE.search(target):
        return target
    try:
        return unquote(target, errors="strict")
    except UnicodeDecodeError:
        return target


#: A backslash before an ASCII punctuation character is an escape (CommonMark
#: §2.4); before anything else it is a literal backslash (Ex. 13).
_RE_BACKSLASH_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")


#: An entity or numeric character reference (CommonMark §2.5): a valid HTML5
#: entity name, or a decimal (1-7 digits) or hexadecimal (1-6 digits)
#: reference, always closed by ``;``. Anything else stays literal, which is
#: stricter than ``html.unescape`` — that follows HTML5's legacy list and
#: reads ``&not`` without a semicolon as ``¬``.
_RE_CHARACTER_REFERENCE = re.compile(
    r"&(?:#[xX](?P<hex>[0-9A-Fa-f]{1,6})|#(?P<dec>[0-9]{1,7})|(?P<name>[A-Za-z][A-Za-z0-9]{1,31}));"
)
#: Escapes and references in one pass, the escape leftmost, so ``\\&#46;``
#: yields a literal ``&`` followed by ``#46;`` rather than an escaped ``&``
#: that is then re-read as the start of a reference (CommonMark resolves
#: §2.4 and §2.5 together).
_RE_ESCAPE_OR_REFERENCE = re.compile(
    rf"(?P<esc>{_RE_BACKSLASH_ESCAPE.pattern})|{_RE_CHARACTER_REFERENCE.pattern}"
)


def _decode_reference(match: re.Match[str]) -> str:
    if match.group("esc") is not None:
        return match.group("esc")[1]
    if (name := match.group("name")) is not None:
        return _HTML5_ENTITIES.get(name + ";", match.group(0))
    codepoint = int(
        match.group("hex") or match.group("dec"), 16 if match.group("hex") else 10
    )
    # U+0000, a surrogate, and anything outside Unicode become U+FFFD
    # (§2.5, "invalid Unicode code points"); a lone surrogate would not
    # even survive the trip into SQLite (UTF-8 refuses it).
    if codepoint == 0 or 0xD800 <= codepoint <= 0xDFFF or codepoint > 0x10FFFF:
        return "\ufffd"
    return chr(codepoint)


def _decode_escapes_and_entities(text: str) -> str:
    return _RE_ESCAPE_OR_REFERENCE.sub(_decode_reference, text)


#: A numeric character reference's body after its ``&#`` (§2.5).
_RE_NUMERIC_REFERENCE_TAIL = re.compile(r"#(?:[xX][0-9A-Fa-f]{1,6}|[0-9]{1,7});")


def _is_escaped(written: str, pos: int) -> bool:
    """Whether an odd run of backslashes precedes *pos* (§2.4)."""
    backslashes = 0
    while pos - backslashes - 1 >= 0 and written[pos - backslashes - 1] == "\\":
        backslashes += 1
    return backslashes % 2 == 1


def _is_fragment_marker(written: str, pos: int) -> bool:
    """Whether the ``#`` at *pos* begins the fragment rather than the name.

    Not when it is escaped (``\\#``; ``\\\\#`` is an escaped backslash then a
    marker, §2.4), and not when it is the ``#`` of a well-formed numeric
    character reference whose ``&`` is not itself escaped (``&#46;``, §2.5).
    """
    if _is_escaped(written, pos):
        return False
    return not (
        pos > 0
        and written[pos - 1] == "&"
        and not _is_escaped(written, pos - 1)
        and _RE_NUMERIC_REFERENCE_TAIL.match(written, pos) is not None
    )


def split_markdown_fragment(written: str) -> tuple[str, str | None]:
    """Split a markdown destination, brackets already off, at its fragment.

    Runs on the text *as written*, before any decoding, so an encoded or
    escaped ``#`` stays in the name (#1332, #1353). Extraction and the
    rename path both use it, so the two cannot disagree on where a
    destination ends.

    Args:
        written: The destination without its ``<…>`` brackets, title
            excluded.

    Returns:
        ``(path_part, fragment)``; *fragment* is ``None`` when absent or
        empty.
    """
    pos = written.find("#")
    while pos != -1 and not _is_fragment_marker(written, pos):
        pos = written.find("#", pos + 1)
    if pos == -1:
        return written, None
    return written[:pos], written[pos + 1 :] or None


def is_anchor_destination(raw: str) -> bool:
    """Whether a destination, as written, names only a fragment of its note.

    ``#h`` and ``<#h>`` are same-document anchors; ``%23x.md``, ``\\#x.md``
    and ``&#35;x.md`` are files whose name begins with a literal ``#`` and
    must be judged on the written spelling, before any decoding turns that
    ``#`` into a leading character (#1353).

    Args:
        raw: The destination exactly as written, title excluded.

    Returns:
        ``True`` when the written path part is empty.
    """
    written = raw[1:-1] if is_pointy_destination(raw) else raw
    path_part, _fragment = split_markdown_fragment(written)
    return not path_part


def is_pointy_destination(raw: str) -> bool:
    """Return whether *raw* is written in CommonMark's ``<…>`` destination form.

    Args:
        raw: A destination exactly as written, title excluded.

    Returns:
        ``True`` for ``<my note.md>``; ``False`` for a plain destination.
    """
    return len(raw) >= 2 and raw[0] == "<" and raw[-1] == ">"


def decode_markdown_destination(raw: str) -> str:
    """Decode a markdown destination, as written, into the name it spells.

    CommonMark's inline-link destination has five spellings beyond the
    literal one (#1353; ``docs/design/reference/commonmark-gfm.md``, "Inline
    links"), decoded in the order ``docs/design/design.md`` fixes: the
    ``<…>`` form loses its brackets, backslash escapes and entity references
    are decoded (``\\(`` → ``(``, ``&amp;`` → ``&``, ``&#46;`` → ``.``; an
    invalid entity stays literal), and only then the URL layer's
    percent-escapes through :func:`decode_link_target`, with its refusals.
    Extraction and the rename path both call this, so they cannot disagree
    on what a spelling names.

    Args:
        raw: The destination exactly as written, title excluded, fragment
            included.

    Returns:
        The decoded destination, fragment still attached.
    """
    inner = raw[1:-1] if is_pointy_destination(raw) else raw
    return decode_link_target(_decode_escapes_and_entities(inner))


def _escape_meaning_changers(name: str, *, pointy: bool) -> str:
    """Backslash-escape the characters that would re-parse a rewritten link.

    A rename introduces no escaping the author did not use — with one
    bounded exception: a new name containing ``#`` would be read as a
    fragment (and a name *beginning* with one as a same-document anchor),
    and ``<`` / ``>`` inside the ``<…>`` form would end it early. Left
    literal, the rewritten link would silently point elsewhere rather than
    show up broken, so exactly those characters are escaped (#1353).

    Args:
        name: The new destination path, not percent-encoded.
        pointy: Whether it will be wrapped in ``<…>``.

    Returns:
        *name* with ``#`` (and, in the pointy form, ``<`` and ``>``) escaped.
    """
    name = name.replace("#", "\\#")
    if pointy:
        name = name.replace("<", "\\<").replace(">", "\\>")
    return name


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
        # A ``<…>`` destination keeps its brackets in raw_target (they are
        # what the file holds); take them off for the shape test and put
        # them back on the answer, so a rename keeps the form that let the
        # author write a space (#1353).
        pointy = is_pointy_destination(raw_target)
        written = raw_target[1:-1] if pointy else raw_target
        raw_path_part, _ = split_markdown_fragment(written)
        # The shape test below compares the destination to old_path, and
        # old_path is never encoded. Comparing the encoded spelling therefore
        # never matched, so a root-relative encoded link fell into the
        # relative-to-source branch and came back rewritten as a relative
        # one — the same fidelity defect as #1105, reached by a different
        # route (#1332). Decode for the comparison — escapes and entities
        # too (#1353); re-encode the answer only if the author was
        # percent-encoding.
        decoded_path_part = decode_markdown_destination(raw_path_part)
        was_encoded = decode_link_target(raw_path_part) != raw_path_part

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
        else:
            new_path_part = _escape_meaning_changers(new_path_part, pointy=pointy)
        new_raw = new_path_part + ("#" + fragment if fragment else "")
        return f"<{new_raw}>" if pointy else new_raw


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
