#!/usr/bin/env python3
"""Promote reviewed staging notes into canonical release-note pages."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

VERSION_RE = re.compile(
    r"^v?(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?:-rc\.(?P<rc>[1-9][0-9]*))?$"
)
WATERMARK_RE = re.compile(r"^<!-- notes-range-end: ([0-9a-f]{40}) -->$", re.MULTILINE)
INDEX_START_RE = re.compile(r"<!-- RELEASE-PAGES-START:[\s\S]*?-->")
PATCH_HEADING_RE = re.compile(
    r"^ {0,3}##[ \t]+v([0-9]+)\.([0-9]+)\.([0-9]+)"
    r"(?:[ \t]+#+)?[ \t]*$",
    re.MULTILINE,
)
PATCH_HEADING_CANDIDATE_RE = re.compile(
    r"^ {0,3}##[ \t]+v[0-9]+\.[0-9]+\.[0-9]+.*$", re.MULTILINE
)
ATX_HEADING_RE = re.compile(
    r"^(?P<indent> {0,3})(?P<marks>#{1,6})(?P<suffix>(?:[ \t].*)?)$"
)


class PromotionError(ValueError):
    """Release notes cannot be promoted without human correction."""


@dataclass(frozen=True)
class Target:
    version: str
    tag: str
    minor: str
    page: Path


@dataclass(frozen=True)
class PromotionPlan:
    root: Path
    writes: dict[Path, str]
    deletes: tuple[Path, ...]
    stage_paths: tuple[Path, ...]


def normalize_target(version: str) -> Target:
    match = VERSION_RE.fullmatch(version)
    if match is None:
        raise PromotionError(
            f"invalid release version {version!r}; expected X.Y.Z or X.Y.Z-rc.N"
        )
    stable = f"{match['major']}.{match['minor']}.{match['patch']}"
    minor = f"{match['major']}.{match['minor']}"
    return Target(stable, f"v{stable}", minor, Path(f"docs/releases/{minor}.md"))


def require_once(text: str, token: str, label: str) -> None:
    count = text.count(token)
    if count != 1:
        raise PromotionError(f"{label} must occur exactly once; found {count}")


def _masked_line(line: str) -> str:
    return "".join(character if character in "\r\n" else " " for character in line)


def _opening_fence(line: str) -> tuple[str, int] | None:
    match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
    if match is None:
        return None
    marker = match.group(1)
    if marker[0] == "`" and "`" in match.group(2):
        return None
    return marker[0], len(marker)


def _closes_fence(line: str, character: str, length: int) -> bool:
    return (
        re.fullmatch(rf" {{0,3}}{re.escape(character)}{{{length},}}[ \t]*", line)
        is not None
    )


def _mask_fenced_code(text: str) -> str:
    masked: list[str] = []
    fence_character = ""
    fence_length = 0
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if fence_character:
            masked.append(_masked_line(line))
            if _closes_fence(stripped, fence_character, fence_length):
                fence_character = ""
                fence_length = 0
            continue

        fence = _opening_fence(stripped)
        if fence is not None:
            fence_character, fence_length = fence
            masked.append(_masked_line(line))
        else:
            masked.append(line)
    return "".join(masked)


def _watermark_match(text: str, label: str) -> re.Match[str]:
    visible = _mask_fenced_code(text)
    matches = list(WATERMARK_RE.finditer(visible))
    lookalikes = sum("notes-range-end" in line for line in visible.splitlines())
    if len(matches) != 1 or lookalikes != 1:
        raise PromotionError(
            f"{label} must contain exactly one standalone 40-hex watermark"
        )
    return matches[0]


def validate_next(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0] != "# Next release":
        raise PromotionError("next.md must start with '# Next release'")
    title_count = sum(line == "# Next release" for line in lines)
    if title_count != 1:
        raise PromotionError(
            f"next.md title must occur exactly once; found {title_count}"
        )
    _watermark_match(text, "next.md")
    start = "<!-- RELEASE-SUMMARY NEXT START -->"
    end = "<!-- RELEASE-SUMMARY NEXT END -->"
    require_once(text, start, "NEXT summary start")
    require_once(text, end, "NEXT summary end")
    if text.index(start) > text.index(end):
        raise PromotionError("NEXT summary START must precede END")
    summary = text.split(start, 1)[1].split(end, 1)[0].strip()
    if not summary:
        raise PromotionError("NEXT summary must not be empty")
    return text


def _target_summary_count(text: str, tag: str) -> int:
    start = f"<!-- RELEASE-SUMMARY {tag} START -->"
    end = f"<!-- RELEASE-SUMMARY {tag} END -->"
    visible = _mask_fenced_code(text)
    starts = visible.count(start)
    ends = visible.count(end)
    if starts == 0 and ends == 0:
        return 0
    if starts != 1 or ends != 1:
        raise PromotionError(
            "target summary must contain exactly one complete START/END pair"
        )
    start_at = visible.index(start)
    end_at = visible.index(end)
    if start_at > end_at:
        raise PromotionError("target summary START must precede END")
    summary = visible[start_at + len(start) : end_at].strip()
    if not summary:
        raise PromotionError("target summary must not be empty")
    return 1


def new_page_text(next_text: str, target: Target) -> str:
    body = next_text.replace("# Next release", f"# {target.minor}", 1)
    body = body.replace("RELEASE-SUMMARY NEXT", f"RELEASE-SUMMARY {target.tag}")
    return (
        body.rstrip()
        + "\n\n<!-- PATCH-RELEASES-START -->\n<!-- PATCH-RELEASES-END -->\n"
    )


def _new_index_text(index_text: str, target: Target) -> str:
    starts = list(INDEX_START_RE.finditer(index_text))
    if len(starts) != 1:
        raise PromotionError(
            f"release pages start must occur exactly once; found {len(starts)}"
        )
    end = "<!-- RELEASE-PAGES-END -->"
    require_once(index_text, end, "release pages end")
    if starts[0].end() > index_text.index(end):
        raise PromotionError("release pages START must precede END")
    if f"({target.minor}.md)" in index_text:
        raise PromotionError(f"release index already links to {target.minor}.md")

    placeholder = (
        "No release pages yet. The first entry appears with the first stable\n"
        "release cut after this project adopted release-notes pages.\n"
    )
    if index_text.count(placeholder) > 1:
        raise PromotionError("release pages placeholder must occur at most once")
    without_placeholder = index_text.replace(placeholder, "", 1)
    start = INDEX_START_RE.search(without_placeholder)
    if start is None:
        raise PromotionError("release pages START comment is malformed")
    suffix = without_placeholder[start.end() :]
    suffix = suffix.removeprefix("\n")
    entry = f"- [{target.minor}]({target.minor}.md)\n"
    return without_placeholder[: start.end()] + "\n" + entry + suffix


def promote_new_page(
    root: Path,
    target: Target,
    next_text: str,
    index_text: str,
) -> PromotionPlan:
    validate_next(next_text)
    promoted_index = _new_index_text(index_text, target)
    page_text = new_page_text(next_text, target)
    page_path = root / target.page
    index_relative = Path("docs/releases/index.md")
    next_relative = Path("docs/releases/next.md")
    return PromotionPlan(
        root,
        {page_path: page_text, root / index_relative: promoted_index},
        (root / next_relative,),
        (target.page, index_relative, next_relative),
    )


def shift_headings(text: str, levels: int = 1) -> str:
    shifted: list[str] = []
    fence_character = ""
    fence_length = 0
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if fence_character:
            shifted.append(line)
            if _closes_fence(stripped, fence_character, fence_length):
                fence_character = ""
                fence_length = 0
            continue

        fence = _opening_fence(stripped)
        if fence is not None:
            fence_character, fence_length = fence
            shifted.append(line)
            continue

        heading = ATX_HEADING_RE.fullmatch(stripped)
        if heading is None or len(heading["marks"]) + levels > 6:
            shifted.append(line)
            continue
        newline = line[len(stripped) :]
        shifted.append(
            heading["indent"]
            + "#" * levels
            + heading["marks"]
            + heading["suffix"]
            + newline
        )
    return "".join(shifted)


def _validate_canonical_page(
    page_text: str, target: Target
) -> list[tuple[int, int, int]]:
    lines = page_text.splitlines()
    title = f"# {target.minor}"
    title_count = sum(line == title for line in lines)
    if not lines or lines[0] != title or title_count != 1:
        raise PromotionError(
            f"canonical page title must be {title!r} exactly once; found {title_count}"
        )
    _watermark_match(page_text, "canonical page")

    start = "<!-- PATCH-RELEASES-START -->"
    end = "<!-- PATCH-RELEASES-END -->"
    require_once(page_text, start, "patch start sentinel")
    require_once(page_text, end, "patch end sentinel")
    start_at = page_text.index(start) + len(start)
    end_at = page_text.index(end)
    if start_at > end_at:
        raise PromotionError("patch START sentinel must precede END sentinel")

    patch_text = _mask_fenced_code(page_text[start_at:end_at])
    patch_matches = list(PATCH_HEADING_RE.finditer(patch_text))
    if len(patch_matches) != len(PATCH_HEADING_CANDIDATE_RE.findall(patch_text)):
        raise PromotionError("patch headings must be ascending and undated")
    patches = [(int(match[1]), int(match[2]), int(match[3])) for match in patch_matches]
    if any(left >= right for left, right in pairwise(patches)):
        raise PromotionError(
            "existing patch sections must be in ascending version order"
        )
    target_minor = tuple(int(part) for part in target.minor.split("."))
    if any(patch[:2] != target_minor for patch in patches):
        raise PromotionError("patch headings must belong to the canonical minor series")
    return patches


def promote_patch_page(
    root: Path,
    target: Target,
    next_text: str,
    page_text: str,
) -> PromotionPlan:
    validate_next(next_text)
    patches = _validate_canonical_page(page_text, target)
    target_version = tuple(int(part) for part in target.version.split("."))
    if target_version[2] == 0:
        raise PromotionError(
            "an existing canonical page cannot accept a new .0 release"
        )
    if patches and target_version <= patches[-1]:
        raise PromotionError("target patch would violate ascending version order")

    staging_watermark = _watermark_match(next_text, "next.md")
    canonical_watermark = _watermark_match(page_text, "canonical page")

    body_start = next_text.index("\n") + 1
    body = next_text[body_start:]
    watermark_start = staging_watermark.start() - body_start
    watermark_end = staging_watermark.end() - body_start
    body = body[:watermark_start] + body[watermark_end:]
    body = body.replace("RELEASE-SUMMARY NEXT", f"RELEASE-SUMMARY {target.tag}")
    body = shift_headings(body).strip()
    section = f"## {target.tag}\n\n{body}"

    promoted = (
        page_text[: canonical_watermark.start()]
        + staging_watermark.group(0)
        + page_text[canonical_watermark.end() :]
    )
    end = "<!-- PATCH-RELEASES-END -->"
    insert_at = promoted.index(end)
    promoted = (
        promoted[:insert_at].rstrip() + "\n\n" + section + "\n\n" + promoted[insert_at:]
    )
    page_path = root / target.page
    next_relative = Path("docs/releases/next.md")
    return PromotionPlan(
        root,
        {page_path: promoted},
        (root / next_relative,),
        (target.page, next_relative),
    )


def plan_promotion(root: Path, version: str) -> PromotionPlan:
    root = root.resolve()
    target = normalize_target(version)
    page_path = root / target.page
    next_relative = Path("docs/releases/next.md")
    next_path = root / next_relative
    index_relative = Path("docs/releases/index.md")
    index_path = root / index_relative

    page_text = page_path.read_text(encoding="utf-8") if page_path.is_file() else ""
    target_present = _target_summary_count(page_text, target.tag) == 1
    next_present = next_path.is_file()

    if target_present and next_present:
        raise PromotionError("ambiguous release notes: target and next.md both exist")
    if target_present:
        return PromotionPlan(root, {}, (), ())
    if not next_present:
        raise PromotionError("no reviewed release notes found for target")

    next_text = validate_next(next_path.read_text(encoding="utf-8"))
    if page_path.is_file():
        return promote_patch_page(root, target, next_text, page_text)

    if not index_path.is_file():
        raise PromotionError(
            "docs/releases/index.md is required for a new release series"
        )
    index_text = index_path.read_text(encoding="utf-8")
    return promote_new_page(root, target, next_text, index_text)


def apply_plan(plan: PromotionPlan) -> None:
    temporary: dict[Path, Path] = {}
    try:
        for path, text in plan.writes.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(f".{path.name}.tmp")
            temp.write_text(text, encoding="utf-8", newline="\n")
            temporary[path] = temp
        for path, temp in temporary.items():
            temp.replace(path)
        for path in plan.deletes:
            path.unlink()
    finally:
        for temp in temporary.values():
            temp.unlink(missing_ok=True)

    if plan.stage_paths:
        subprocess.run(
            ["git", "add", "-A", "--", *[str(path) for path in plan.stage_paths]],
            cwd=plan.root,
            check=True,
        )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("usage: promote_release_notes.py VERSION", file=sys.stderr)
        return 2

    try:
        plan = plan_promotion(Path.cwd(), arguments[0])
        if not plan.stage_paths:
            print(
                f"promote_release_notes: {arguments[0]} already promoted; nothing to do"
            )
            return 0
        apply_plan(plan)
    except PromotionError as error:
        print(f"promote_release_notes: REFUSING: {error}", file=sys.stderr)
        return 1

    print(f"promote_release_notes: promoted {arguments[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
