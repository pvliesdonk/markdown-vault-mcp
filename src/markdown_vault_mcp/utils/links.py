"""Link-update helpers for file rename operations.

These functions compute replacement link targets and apply substitutions
in file content when a note is renamed within the vault.
"""

from __future__ import annotations

import os.path as osp
import re
from pathlib import Path


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
        The replacement raw_target string to write into the source file.
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
        # markdown and reference links.
        # Detect whether the link was written as vault-root-relative (raw_target
        # matches old_path) or as a path relative to the source file's directory
        # (raw_target != old_path, e.g. "../archive/target.md" from docs/).
        raw_path_part = raw_target.split("#")[0]
        if source_path and old_path and raw_path_part != old_path:
            # Relative-to-source link: compute the correct new relative path so
            # cross-directory links continue to resolve after the rename.
            source_dir = str(Path(source_path).parent)
            new_rel = osp.relpath(new_path, source_dir)
            # os.path.relpath uses OS separators on Windows; normalise to /.
            new_path_part = new_rel.replace("\\", "/")
        else:
            new_path_part = new_path
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
