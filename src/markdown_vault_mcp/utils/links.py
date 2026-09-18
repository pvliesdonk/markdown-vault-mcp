"""Link target helpers: decoding, encoding, replacement, substitution.

:func:`decode_link_target` is shared with link extraction and
:func:`encode_plain_destination` with every site that *writes* a markdown
destination; the rest compute replacement link targets and apply
substitutions in file content when a note is renamed within the vault.
"""

from __future__ import annotations

import os.path as osp
import re
from html.entities import html5 as _HTML5_ENTITIES
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, TypeVar
from urllib.parse import quote, unquote

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

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


#: What a *plain* (unbracketed) markdown destination cannot hold: the space
#: and the ASCII control characters (CommonMark §6.3, "Link destination").
#: A literal space does not merely look wrong — it ends the destination and
#: starts the title slot, so the link stops being a link (#1494).
_RE_PLAIN_DESTINATION_ILLEGAL = re.compile(r"[\x00-\x20\x7f]")


def encode_plain_destination(path: str) -> str:
    """Percent-encode what a plain markdown destination cannot hold.

    The counterpart to :func:`decode_link_target`, used wherever the server
    *writes* a destination rather than reads one: a vault path is not a
    markdown destination, and interpolating one into ``[t](…)`` verbatim
    emits invalid markdown for every note or folder whose name carries a
    space (#1494). Obsidian's own link writer does the same thing —
    ``[Three laws of motion](Projects/Three%20laws%20of%20motion.md)``
    (``docs/design/reference/obsidian-markdown.md``, "Markdown links
    Obsidian writes") — so the output matches what a vault's other writer
    produces.

    Only the characters §6.3 forbids are touched. Everything else is left
    literal, so the destination stays readable and
    :func:`decode_link_target` reads back exactly *path*: a ``%`` already in
    the name is not an escape (it is not followed by two hex digits once the
    space beside it becomes ``%20``), and ``/`` is structure, never encoded.

    A NUL is encoded like any other control character, to ``%00``, which
    :func:`decode_link_target` then refuses — the link shows up broken
    instead of resolving onto a name no file system allows. No path from a
    real vault reaches that case.

    Args:
        path: A destination's path portion (and fragment, if any) as the
            name spells it, not percent-encoded.

    Returns:
        *path* with spaces and ASCII control characters percent-encoded.
    """
    return _RE_PLAIN_DESTINATION_ILLEGAL.sub(
        lambda match: f"%{ord(match.group()):02X}", path
    )


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


#: How deep the destination parser balances parentheses before giving up —
#: three, the depth §6.3's own examples reach, a departure recorded in
#: ``docs/design/design.md``. :func:`_parentheses_parse_plainly` mirrors it
#: on the write side, so the two cannot disagree about which names the plain
#: form can hold; ``tests/test_links_generated_destinations.py`` pins the
#: agreement.
_MAX_PLAIN_PAREN_DEPTH = 3


def _parentheses_parse_plainly(name: str) -> bool:
    """Whether *name*'s parentheses survive a plain destination unescaped.

    CommonMark §6.3 admits parentheses in the plain form only escaped or in
    balanced pairs, and this project's parser balances them to
    :data:`_MAX_PLAIN_PAREN_DEPTH`.

    A parenthesis that is already backslash-escaped is exempt from the
    balance requirement — §6.3 admits it "escaped **or** balanced" — so it
    is skipped rather than counted, the way :func:`_is_fragment_marker`
    skips an escaped ``#``. Counting it would judge an author's legal
    ``a\\(b`` unbalanced and send it to be escaped again.

    Args:
        name: A destination as it is written, escapes included.

    Returns:
        ``True`` when the unescaped parentheses are balanced and no deeper
        than the parser goes, so escaping them would be noise.
    """
    depth = 0
    for pos, char in enumerate(name):
        if char not in "()" or _is_escaped(name, pos):
            continue
        if char == "(":
            depth += 1
            if depth > _MAX_PLAIN_PAREN_DEPTH:
                return False
        else:
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _escape_meaning_changers(name: str, *, pointy: bool) -> str:
    """Backslash-escape the characters that would re-parse a rewritten link.

    A rename introduces no escaping the author did not use — except where
    the new name would otherwise be read as something other than itself.
    Three cases qualify, and nothing else is touched:

    * ``#`` is always escaped: it would be read as a fragment, and a name
      *beginning* with one as a same-document anchor (#1353).
    * ``<`` and ``>`` are escaped inside the ``<…>`` form, which they would
      end early (#1353).
    Parentheses are *not* handled here: §6.3 balances them across the whole
    destination, fragment included, so the decision cannot be made on the
    path alone — :func:`escape_unparsable_parentheses` makes it on the
    assembled string (#1516).

    Args:
        name: The new destination path, not percent-encoded.
        pointy: Whether it will be wrapped in ``<…>``.

    Returns:
        *name* with the characters that would re-point it escaped.
    """
    name = name.replace("#", "\\#")
    if pointy:
        name = name.replace("<", "\\<").replace(">", "\\>")
    return name


def escape_unparsable_parentheses(written: str) -> str:
    """Escape a plain destination's parentheses when they would not parse.

    The third repair in the re-pointing class, and the one that cannot be
    made per-part: CommonMark §6.3 reads a destination's parentheses as one
    balanced run, so ``a(b.md#c)d`` parses — the fragment closes what the
    path opened — while escaping the path's ``(`` alone would unbalance it
    and break a link that worked. The test therefore runs on the assembled
    destination, and when it fails every parenthesis is escaped, the
    author's fragment included: at that point the alternative is not a
    tidier link but no link (#1516).

    Left alone when they parse, so a balanced ``a(b).md`` stays literal.

    Only *unescaped* parentheses are escaped. A blind replace would turn an
    author's existing ``\\(`` into ``\\\\(`` — an escaped backslash followed by
    a bare parenthesis — which is not the same destination and, on a
    fragment carrying one, not a link at all.

    Args:
        written: The assembled destination — path, marker and fragment —
            as it is written, escapes included.

    Returns:
        *written*, with every unescaped parenthesis escaped if any of them
        would not parse.
    """
    if _parentheses_parse_plainly(written):
        return written
    out: list[str] = []
    for pos, char in enumerate(written):
        if char in "()" and not _is_escaped(written, pos):
            out.append("\\")
        out.append(char)
    return "".join(out)


def build_plain_destination(path: str, fragment: str | None = None) -> str:
    """Render a vault path as a destination a markdown reader parses.

    The write-side entry point for every site that *generates* a link
    rather than rewriting one: it applies the same two repairs the rename
    path uses — :func:`_escape_meaning_changers` for what would re-point the
    link, :func:`encode_plain_destination` for what a plain destination
    cannot hold at all — so a generated destination and a rewritten one
    cannot disagree about what a name needs (#1494, #1513).

    The fragment is attached between the two repairs: after the ``#``
    escape, so the marker that separates them stays a marker while a ``#``
    inside either part does not become one, and before the parenthesis
    test, which §6.3 runs on the whole destination.

    Args:
        path: The destination's path portion, as the vault spells it.
        fragment: The heading fragment, without its ``#``, if any.

    Returns:
        The destination, ready to interpolate between ``(`` and ``)``.
    """
    written = _escape_meaning_changers(path, pointy=False)
    if fragment:
        written += "#" + fragment
    return encode_plain_destination(escape_unparsable_parentheses(written))


def escape_link_text(text: str) -> str:
    """Backslash-escape the brackets that would end a link's text early.

    A generated link's text is a title or an alias, not markdown the author
    wrote, so a ``]`` in it ends the text and the line stops being a link;
    an unmatched ``[`` re-opens it and a spec reader shows different text
    than intended (#1513). Backslashes go first, or an escape this function
    adds could be neutralised by one already in *text*.

    Escaping makes the line valid CommonMark, which is what Obsidian and the
    docs site read. This project's own scanner still does not index a link
    whose text carries an escaped ``]`` — its link-text pattern is not
    escape-aware — so such an entry renders correctly but contributes no
    edge to the link graph until #1517 is fixed.

    Args:
        text: The display text, as the title or alias spells it.

    Returns:
        *text* with ``\\``, ``[`` and ``]`` backslash-escaped.
    """
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


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
        literally stays literal (#1105, #1332) — except where writing the
        new name literally would not parse, when a space or an ASCII control
        character is percent-encoded so the rewrite stays a link (#1494).
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
        # The fragment is the author's spelling, not a name this rewrite
        # introduces, so it is re-attached as found and never reformatted.
        # The two plain-form repairs below still reach it, because both are
        # properties of the destination as a whole rather than of the part
        # that changed: a space anywhere in it ends the link, and the
        # parentheses balance as one run. Repairing a fragment we are
        # already rewriting the line around beats re-emitting something
        # that is not a link.
        new_raw = new_path_part + ("#" + fragment if fragment else "")
        if pointy:
            # ``<…>`` holds a space and a parenthesis, so neither plain-form
            # repair applies and the author's spelling survives whole.
            return f"<{new_raw}>"
        if not was_encoded:
            # Both plain-form repairs, on the assembled destination: the
            # parenthesis test is a property of the whole run (#1516), and a
            # space anywhere in it ends the link (#1494). An encoded
            # original was re-encoded by ``quote`` above, which covers both.
            new_raw = encode_plain_destination(escape_unparsable_parentheses(new_raw))
        return new_raw


#: A bracket span (link text, reference label) is read by an escape-aware
#: scan rather than a negated character class, so a ``\\]`` does not close
#: it (CommonMark §6.3; #1517 for the inline text, #1519 for the two
#: reference forms) — :func:`find_bracket_span` below.
#:
#: It lives in this module, not in ``scanner.py``, because the rewrite
#: side needs the same answer: when the two disagreed about where a
#: link's text ends, a rename found a link the index held and silently
#: failed to rewrite it (#1521).
#:
#: The scan steps between the only characters that can matter rather than
#: over every one of them, so the engine keeps doing the scanning and a
#: note with no brackets costs one failed search rather than a Python loop
#: the length of the note.
#:
#: These three and no others: :func:`find_bracket_span` handles the
#: backslash and then treats what is left as the two brackets, so a
#: character added here needs a branch added there.
_RE_LINK_TEXT_MARK = re.compile(r"[\[\]\\]")


#: An optional link title after the destination, moved here from
#: ``scanner.py`` with the destination grammar it belongs to (#1526):
#: reading a link and rewriting one are the same grammar, and keeping
#: them apart is what let the two drift in #1521.
#: honoured, whitespace-separated, at the end of the parenthesised text.
#: Spaces and tabs separate it (§6.3; a NBSP is an ordinary destination
#: character, Ex. 507); a line ending never reaches here (#1334).
trailing_title_pattern = re.compile(
    r"""[ \t]+(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\((?:[^()\\]|\\.)*\))[ \t]*$"""
)


_PLAIN_CHAR = r"[^()\\\x00-\x08\x0a-\x1f\x7f]|\\."
_RE_PLAIN_DESTINATION = re.compile(
    rf"(?:{_PLAIN_CHAR}"
    rf"|\((?:{_PLAIN_CHAR}|\((?:{_PLAIN_CHAR}|\((?:{_PLAIN_CHAR})*\))*\))*\)"
    rf")*\)"
)


def _scan_plain_destination(region: str, pos: int) -> int | None:
    """Find the ``)`` that closes a plain destination (and title) at *pos*.

    See :data:`_RE_PLAIN_DESTINATION` for the grammar.

    Returns:
        The index of the closing ``)``, or ``None``.
    """
    m = _RE_PLAIN_DESTINATION.match(region, pos)
    return None if m is None else m.end() - 1


def _scan_pointy_destination(region: str, pos: int) -> int | None:
    """Find the end of a ``<…>`` destination opening at *pos*.

    No line ending and no unescaped ``<`` or ``>`` inside (§6.3, Ex. 491,
    493); a ``)`` or a space is fine (Ex. 489, 492).

    Returns:
        The index just past the closing ``>``, or ``None``.
    """
    i = pos + 1
    while i < len(region):
        char = region[i]
        if char == "\n" or char == "<":
            return None
        if char == "\\" and i + 1 < len(region) and region[i + 1] != "\n":
            i += 2
            continue
        if char == ">":
            return i + 1
        i += 1
    return None


def _parse_destination(region: str, pos: int) -> tuple[str, int, int] | None:
    """Read an inline link's destination starting just after its ``(``.

    Implements §6.3's two destination forms and the optional title (#1353).
    The returned destination is the text **as written**, title excluded —
    ``<my note.md>`` keeps its brackets, ``a\\(b\\).md`` its escapes — since
    that is what the rename path searches the file for; decoding is
    :func:`~markdown_vault_mcp.utils.links.decode_markdown_destination`'s job.

    Args:
        region: The paragraph region being scanned.
        pos: Index of the first character after ``(``.

    Returns:
        ``(raw_destination, start, end)`` with *start* the index of the
        destination's first character and *end* the index just past the
        closing ``)``, or ``None`` when no link is written here. The offset
        is returned rather than re-derived by the caller: a rewrite that
        searches the region for *raw_destination* finds the wrong
        occurrence whenever the title repeats it (#1526 review).
    """
    i = pos
    while i < len(region) and region[i] in " \t":
        i += 1
    if i < len(region) and region[i] == "<":
        return _parse_pointy_destination(region, i)
    close = _scan_plain_destination(region, i)
    if close is None:
        return None
    # Only spaces and tabs are trimmed: a NBSP is a destination character.
    # Only trailing text is removed above, so the destination still
    # begins at ``i`` and ``region[i : i + len(raw)] == raw``.
    raw = trailing_title_pattern.sub("", region[i:close]).strip(" \t")
    return (raw, i, close + 1) if raw else None


def _parse_pointy_destination(region: str, pos: int) -> tuple[str, int, int] | None:
    """The ``<…>`` half of :func:`_parse_destination`; *pos* is at the ``<``."""
    end = _scan_pointy_destination(region, pos)
    if end is None:
        return None
    raw = region[pos:end]
    close = _scan_plain_destination(region, end)
    if close is None or raw == "<>":
        return None
    rest = region[end:close]
    if rest.strip(" \t") and not trailing_title_pattern.fullmatch(rest):
        return None
    return raw, pos, close + 1


def find_bracket_span(region: str, pos: int) -> tuple[int, int, str] | None:
    """Find the next ``[…]`` at or after *pos*, honouring escapes.

    The primitive under every bracket span the scanner reads: an inline
    link's text, and a reference usage's two labels and a definition's one.
    Each used to be matched by a negated character class (``[^\\]]*``), which
    cannot see a backslash, so a ``\\]`` closed the span and the link it
    belonged to produced no row at all — for inline text (#1517) and for the
    reference family (#1519) alike.

    The scan, not a wider regex, is what makes that affordable. An
    escape-aware class has to read past every escaped ``]``, so the engine's
    retry from each ``[`` turns quadratic: on one 40 KB paragraph of
    ``[a\\]b`` the class costs seconds where this costs a single pass. The
    walk also improves the *existing* pathological case, a run of ``[`` that
    never closes (#1343).

    Matching follows the classes it replaces, so backslash-free input
    behaves exactly as before: the **first** ``[`` since the last unescaped
    ``]`` opens the span, and the span ends at the first unescaped ``]``. A
    caller that rejects the span it is handed resumes at ``close + 1``,
    which is where the engine's start-position retry would have landed.

    Args:
        region: The text to scan — one paragraph region for a usage, the
            whole body for a definition (#1334).
        pos: Index to resume from.

    Returns:
        ``(open_index, close_index, inner)`` with ``region[close_index]``
        the unescaped ``]`` that closed the span; ``None`` when no further
        span exists.
    """
    first_open: int | None = None
    index = pos
    while (mark := _RE_LINK_TEXT_MARK.search(region, index)) is not None:
        at = mark.start()
        char = region[at]
        if char == "\\":
            # An escaped character is literal, so neither a bracket that
            # opens the span nor one that closes it.
            index = at + 2
            continue
        if char == "[":
            if first_open is None:
                first_open = at
        else:
            # A ``]``: the mark class yields nothing else once the
            # backslash above is handled, so this needs no test of its own
            # — and stays an ``else`` rather than a third branch that could
            # never be taken.
            if first_open is not None:
                return first_open, at, region[first_open + 1 : at]
            # A ``]`` before any ``[`` closes nothing and is skipped, which
            # is where the engine's start-position retry used to land.
        index = at + 1
    return None


#: What :func:`iter_bracket_links` hands back for a link its caller accepted.
_Payload = TypeVar("_Payload")


def iter_bracket_links(
    region: str,
    follow: Callable[[str, int], tuple[_Payload, int] | None],
) -> Iterator[tuple[int, int, _Payload, int]]:
    r"""Walk *region*'s brackets under CommonMark's delimiter-stack rule.

    The one place the rule lives, for both link families. It replaces a scan
    that took the **first** ``[`` since the last unescaped ``]`` as the
    opener, where CommonMark takes the **nearest unmatched** one (§6.3): a
    ``]`` closes the innermost ``[`` still open, and an opener that closes
    nothing is discarded rather than swallowing what follows.

    What differs between the families is only what has to *follow* a closed
    span for a link to exist — a destination in ``(…)`` for the inline form
    (#1526), a second adjacent span for the reference form (#1528) — so
    that is the argument, and the walk itself is shared rather than written
    twice. Sharing it is the point: the two families drifted apart once
    already (#1521), and a rule that lives in one function cannot.

    Two rules travel with it. An opener preceded by an unescaped ``!`` is an
    image, so its own span yields nothing — but a link *inside* an image's
    description still does, since it closes first (§6.4, Ex. 575). And once
    a link is found, every opener still on the stack is deactivated, because
    links may not contain links (Ex. 518).

    The walk steps between ``[``, ``]`` and ``\`` rather than over every
    character, and pushes and pops each opener at most once, so it stays
    linear: a run of ``[`` costs one push apiece and no rescan.

    Args:
        region: One paragraph region, code already stripped (#1334).
        follow: Given *region* and the index of the ``]`` that closed a
            span, returns ``(payload, end)`` when a link is written there
            and ``None`` when one is not. *end* is the index just past the
            whole link.

    Yields:
        ``(open_index, close_index, payload, end)`` per link, in the order
        the links close. An image is consumed but never yielded.
    """
    stack: list[tuple[int, bool]] = []
    # "Links may not contain links" deactivates every opener on the stack at
    # once, so a watermark says it as well as a flag per entry would: the
    # opener at depth *i* is still active exactly when ``i >= active_from``.
    active_from = 0
    index = 0
    while (mark := _RE_LINK_TEXT_MARK.search(region, index)) is not None:
        at = mark.start()
        char = region[at]
        if char == "\\":
            # An escaped character is literal, so neither bracket nor image
            # marker.
            index = at + 2
            continue
        if char == "[":
            is_image = (
                at > 0 and region[at - 1] == "!" and not _is_escaped(region, at - 1)
            )
            stack.append((at, is_image))
            index = at + 1
            continue
        # A ``]``. With no opener it is literal; otherwise it consumes the
        # nearest one whether or not a link comes of it, which is what stops
        # a failed candidate from being retried and keeps the walk linear.
        if not stack:
            index = at + 1
            continue
        open_index, is_image = stack.pop()
        active = len(stack) >= active_from
        active_from = min(active_from, len(stack))
        matched = follow(region, at) if active else None
        if matched is None:
            index = at + 1
            continue
        payload, end = matched
        if not is_image:
            yield open_index, at, payload, end
            active_from = len(stack)
        # An image is consumed rather than retried: its own span yields no
        # link, but the text it spans has already been walked, so anything
        # linking inside it was yielded on the way.
        index = end


def _follow_inline_destination(
    region: str, close_index: int
) -> tuple[tuple[str, int], int] | None:
    """The inline family's shape test: a destination in ``(…)``."""
    if region[close_index + 1 : close_index + 2] != "(":
        return None
    parsed = _parse_destination(region, close_index + 2)
    if parsed is None:
        return None
    raw_target, target_start, end = parsed
    return (raw_target, target_start), end


class InlineLink(NamedTuple):
    """One inline link, as :func:`iter_inline_links` reads it.

    Named rather than a bare tuple because *target_start* is easy to
    confuse with *open_index*, and a rewrite that reaches for the wrong one
    corrupts a file silently.
    """

    #: Index of the ``[`` that opened the link.
    open_index: int
    #: The link text as written, escapes and all.
    link_text: str
    #: The destination as written, title excluded.
    raw_target: str
    #: Index of *raw_target*'s first character in the region.
    target_start: int
    #: Index just past the closing ``)``.
    end: int


def iter_inline_links(region: str) -> Iterator[InlineLink]:
    r"""Yield every inline ``[text](destination)`` link in *region*.

    The one place the inline-link grammar is decided, for the index and for
    the rewrite alike (#1521). It replaces a scan that took the **first**
    ``[`` since the last unescaped ``]`` as the opener, where CommonMark
    takes the **nearest unmatched** one (§6.3, "Process emphasis"): a ``]``
    closes the innermost ``[`` still open, and an opener that closes nothing
    is discarded rather than swallowing what follows.

    That one difference was three recorded departures (#1526). The old rule
    read ``[a[b](x)``'s text as ``a[b`` where it is ``b``; it stored no row
    at all for ``[a [b] c](x)``, a link whose text merely contains a
    balanced pair; and it read ``![a[b](x)`` as an image, because the
    ``!`` sits before the *outer* ``[`` while the link CommonMark finds
    opens at the inner one. All three follow from the nearest-unmatched
    rule; none of them needed its own repair.

    Two rules travel with it. An opener preceded by an unescaped ``!`` is an
    image, so its own span yields no link — but a link *inside* an image's
    description still does, since it closes first (§6.4, Ex. 575). And once
    a link is found, every opener still on the stack is deactivated, because
    links may not contain links (Ex. 518): in ``[a [b](y) c](x)`` only ``b``
    links.

    The walk steps between ``[``, ``]`` and ``\`` rather than over every
    character, and pushes and pops each opener at most once, so it stays
    linear: a run of ``[`` costs one push apiece and no rescan.

    Args:
        region: One paragraph region, code already stripped (#1334).

    Yields:
        An :class:`InlineLink` per link, in the order the links close.
    """
    for open_index, close_index, (raw_target, target_start), end in iter_bracket_links(
        region, _follow_inline_destination
    ):
        yield InlineLink(
            open_index,
            region[open_index + 1 : close_index],
            raw_target,
            target_start,
            end,
        )


def _replace_inline_destinations(content: str, old_raw: str, new_raw: str) -> str:
    r"""Rewrite every ``[text](old_raw)`` destination in *content*.

    The links are read by :func:`iter_inline_links`, the same scan the
    index is built with, because a rewrite has to see exactly the links the
    index holds. It did not: the class this replaces
    (``(?<!!)(\[[^\]]*?\])\(``) could not cross an escaped ``]``, so after
    #1517 made ``[Bra\]cket](old.md)`` an indexed link, a rename computed a
    replacement for it and then quietly matched nothing (#1521).

    The scan is also what keeps the rewrite linear. An escape-aware class
    is not affordable here even though a literal destination follows it:
    39 KB of ``[a\]b`` costs it about 5 s, quadrupling per doubling, and
    the class it replaces is already quadratic on a run of bare ``[``
    (285 ms at 8000). The scan is a single pass over both.

    Only the destination is matched literally, exactly as before: *old_raw*
    is the destination as the file spells it, which is what the index
    stored, so an equality test is the right one and the pointy, encoded
    and escaped spellings need no special case here.

    Args:
        content: Full file content.
        old_raw: The destination to replace, as written in the file.
        new_raw: The destination to write in its place.

    Returns:
        *content* with every matching destination replaced.

    The scan reports where each destination starts, and the splice uses
    that offset rather than searching the region for *old_raw*. A search
    finds the wrong occurrence whenever the text after the destination
    repeats it: given ``[a](x.md "the x.md note")`` it rewrote the title
    and left the destination stale, a broken link reported as a successful
    rewrite. Caught in review of #1526; the scan this replaced anchored at
    a fixed offset and so never had the ambiguity.

    Note:
        Operates on raw file content, so an occurrence inside a backtick
        code span is rewritten too. Pre-existing, and low risk in practice.
    """
    out: list[str] = []
    read = 0
    for link in iter_inline_links(content):
        if link.raw_target != old_raw:
            continue
        # Everything from the destination's end to ``link.end`` is the
        # author's: an optional title, whatever spacing they chose, and the
        # closing ``)``. It is carried across untouched.
        start = link.target_start
        out.append(content[read:start])
        out.append(new_raw)
        out.append(content[start + len(old_raw) : link.end])
        read = link.end
    out.append(content[read:])
    return "".join(out)


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
        return _replace_inline_destinations(content, old_raw, new_raw)
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
