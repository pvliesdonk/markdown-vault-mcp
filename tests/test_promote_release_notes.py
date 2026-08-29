from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.promote_release_notes import (
    PromotionError,
    PromotionPlan,
    apply_plan,
    main,
    normalize_target,
    plan_promotion,
)

NEXT = """# Next release

<!-- notes-range-end: 0123456789abcdef0123456789abcdef01234567 -->

<!-- RELEASE-SUMMARY NEXT START -->
Operators can now rotate credentials without restarting the server.
<!-- RELEASE-SUMMARY NEXT END -->

## Credential rotation

The server reloads credentials after the configured interval ([#42](https://github.com/example/project/issues/42)).
"""

INDEX = """# Release Notes

<!-- RELEASE-PAGES-START: newest series first; one list entry per page.
     The first real entry replaces the placeholder line below. -->
No release pages yet. The first entry appears with the first stable
release cut after this project adopted release-notes pages.
<!-- RELEASE-PAGES-END -->
"""

INDEX_WITH_EXISTING = """# Release Notes

<!-- RELEASE-PAGES-START: newest series first; one list entry per page.
     The first real entry replaces the placeholder line below. -->
- [2.3](2.3.md)
<!-- RELEASE-PAGES-END -->
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def canonical(tag: str = "v2.4.0") -> str:
    return f"""# 2.4

<!-- notes-range-end: 1111111111111111111111111111111111111111 -->

<!-- RELEASE-SUMMARY {tag} START -->
Existing reviewed summary.
<!-- RELEASE-SUMMARY {tag} END -->

## Existing theme

Existing evidence.

<!-- PATCH-RELEASES-START -->
<!-- PATCH-RELEASES-END -->
"""


def with_patch(tag: str) -> str:
    section = f"""## {tag}

<!-- RELEASE-SUMMARY {tag} START -->
Earlier patch summary.
<!-- RELEASE-SUMMARY {tag} END -->

### Earlier patch theme

Earlier patch evidence.

"""
    return canonical().replace(
        "<!-- PATCH-RELEASES-END -->", section + "<!-- PATCH-RELEASES-END -->"
    )


def snapshot(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.parts
    }


def init_repository(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.com"], cwd=root, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Release Notes Tests"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixtures"], cwd=root, check=True)


def cached_status(root: Path, *, detect_renames: bool = True) -> list[str]:
    arguments = ["git", "diff", "--cached", "--name-status"]
    if not detect_renames:
        arguments.append("--no-renames")
    return subprocess.run(
        arguments,
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()


@pytest.mark.parametrize(
    ("version", "tag", "minor"),
    [
        ("2.4.0", "v2.4.0", "2.4"),
        ("v2.4.0", "v2.4.0", "2.4"),
        ("2.4.0-rc.3", "v2.4.0", "2.4"),
        ("2.4.0-rc.0", "v2.4.0", "2.4"),
    ],
)
def test_normalize_target(version: str, tag: str, minor: str) -> None:
    target = normalize_target(version)
    assert (target.tag, target.minor, target.page) == (
        tag,
        minor,
        Path("docs/releases/2.4.md"),
    )


def test_target_present_without_next_is_a_noop(tmp_path: Path) -> None:
    write(tmp_path / "docs/releases/2.4.md", canonical())
    plan = plan_promotion(tmp_path, "2.4.0-rc.2")
    assert plan.writes == {}
    assert plan.deletes == ()
    assert plan.stage_paths == ()


def test_missing_target_with_next_plans_promotion(tmp_path: Path) -> None:
    write(tmp_path / "docs/releases/next.md", NEXT)
    write(tmp_path / "docs/releases/index.md", INDEX)
    plan = plan_promotion(tmp_path, "2.4.0-rc.1")
    assert tmp_path / "docs/releases/2.4.md" in plan.writes
    assert plan.deletes == (tmp_path / "docs/releases/next.md",)


def test_missing_target_and_next_refuses(tmp_path: Path) -> None:
    with pytest.raises(PromotionError, match="no reviewed release notes"):
        plan_promotion(tmp_path, "2.4.0")


def test_target_and_next_together_refuse(tmp_path: Path) -> None:
    write(tmp_path / "docs/releases/2.4.md", canonical())
    write(tmp_path / "docs/releases/next.md", NEXT)
    with pytest.raises(PromotionError, match="ambiguous"):
        plan_promotion(tmp_path, "2.4.0-rc.2")


@pytest.mark.parametrize(
    "version",
    ["", "2.4", "02.4.0", "2.04.0", "2.4.00", "2.4.0-rc.01", "2.4.0-beta.1"],
)
def test_invalid_release_version_refuses(version: str) -> None:
    with pytest.raises(PromotionError, match="invalid release version"):
        normalize_target(version)


def test_new_minor_page_promotion(tmp_path: Path) -> None:
    write(tmp_path / "docs/releases/next.md", NEXT)
    write(tmp_path / "docs/releases/index.md", INDEX)

    plan = plan_promotion(tmp_path, "2.4.0-rc.1")

    assert (
        plan.writes[tmp_path / "docs/releases/2.4.md"]
        == """# 2.4

<!-- notes-range-end: 0123456789abcdef0123456789abcdef01234567 -->

<!-- RELEASE-SUMMARY v2.4.0 START -->
Operators can now rotate credentials without restarting the server.
<!-- RELEASE-SUMMARY v2.4.0 END -->

## Credential rotation

The server reloads credentials after the configured interval ([#42](https://github.com/example/project/issues/42)).

<!-- PATCH-RELEASES-START -->
<!-- PATCH-RELEASES-END -->
"""
    )
    assert (
        plan.writes[tmp_path / "docs/releases/index.md"]
        == """# Release Notes

<!-- RELEASE-PAGES-START: newest series first; one list entry per page.
     The first real entry replaces the placeholder line below. -->
- [2.4](2.4.md)
<!-- RELEASE-PAGES-END -->
"""
    )


def test_new_minor_index_entry_precedes_existing_series(tmp_path: Path) -> None:
    write(tmp_path / "docs/releases/next.md", NEXT)
    write(tmp_path / "docs/releases/index.md", INDEX_WITH_EXISTING)

    index = plan_promotion(tmp_path, "2.4.0").writes[
        tmp_path / "docs/releases/index.md"
    ]

    assert index.index("-->\n- [2.4](2.4.md)") < index.index("- [2.3](2.3.md)")


def test_existing_minor_index_link_refuses(tmp_path: Path) -> None:
    write(tmp_path / "docs/releases/next.md", NEXT)
    write(
        tmp_path / "docs/releases/index.md",
        INDEX_WITH_EXISTING.replace("2.3", "2.4"),
    )
    before = snapshot(tmp_path)

    with pytest.raises(PromotionError, match="already links"):
        plan_promotion(tmp_path, "2.4.0")

    assert snapshot(tmp_path) == before


def test_duplicate_target_summary_refuses(tmp_path: Path) -> None:
    write(tmp_path / "docs/releases/2.4.md", canonical() + canonical())
    before = snapshot(tmp_path)

    with pytest.raises(PromotionError, match="target summary"):
        plan_promotion(tmp_path, "2.4.0")

    assert snapshot(tmp_path) == before


def test_reversed_target_summary_refuses(tmp_path: Path) -> None:
    block = """<!-- RELEASE-SUMMARY v2.4.0 START -->
Existing reviewed summary.
<!-- RELEASE-SUMMARY v2.4.0 END -->"""
    reversed_block = """<!-- RELEASE-SUMMARY v2.4.0 END -->
Existing reviewed summary.
<!-- RELEASE-SUMMARY v2.4.0 START -->"""
    write(tmp_path / "docs/releases/2.4.md", canonical().replace(block, reversed_block))

    with pytest.raises(PromotionError, match="target summary"):
        plan_promotion(tmp_path, "2.4.0")


def test_empty_target_summary_refuses(tmp_path: Path) -> None:
    write(
        tmp_path / "docs/releases/2.4.md",
        canonical().replace("Existing reviewed summary.", ""),
    )

    with pytest.raises(PromotionError, match="target summary"):
        plan_promotion(tmp_path, "2.4.0")


@pytest.mark.parametrize(
    ("indent", "fence"), [("", "```"), ("   ", "```"), ("", "~~~"), ("   ", "~~~")]
)
def test_target_summary_ignores_marker_examples_in_fenced_code(
    tmp_path: Path,
    indent: str,
    fence: str,
) -> None:
    fenced_example = f"""
{indent}{fence}markdown
<!-- RELEASE-SUMMARY v2.4.0 START -->
Example text is not a second reviewed block.
<!-- RELEASE-SUMMARY v2.4.0 END -->
{indent}{fence}
"""
    write(tmp_path / "docs/releases/2.4.md", canonical() + fenced_example)

    plan = plan_promotion(tmp_path, "2.4.0")

    assert plan.stage_paths == ()


def test_patch_section_is_inserted_before_patch_end(tmp_path: Path) -> None:
    write(tmp_path / "docs/releases/2.4.md", canonical("v2.4.0"))
    write(tmp_path / "docs/releases/next.md", NEXT)

    page = plan_promotion(tmp_path, "2.4.1-rc.1").writes[
        tmp_path / "docs/releases/2.4.md"
    ]

    assert "## v2.4.1\n" in page
    assert "### Credential rotation\n" in page
    assert page.index("## v2.4.1") < page.index("<!-- PATCH-RELEASES-END -->")
    assert "notes-range-end: 0123456789abcdef0123456789abcdef01234567" in page
    assert "notes-range-end: 1111111111111111111111111111111111111111" not in page


def test_next_watermark_must_be_standalone_metadata(tmp_path: Path) -> None:
    watermark = "<!-- notes-range-end: 0123456789abcdef0123456789abcdef01234567 -->"
    write(tmp_path / "docs/releases/2.4.md", canonical())
    write(
        tmp_path / "docs/releases/next.md",
        NEXT.replace(watermark, f"Inline example: {watermark}"),
    )

    with pytest.raises(PromotionError, match="watermark"):
        plan_promotion(tmp_path, "2.4.1")


def test_extra_malformed_watermark_lookalike_refuses(tmp_path: Path) -> None:
    write(tmp_path / "docs/releases/2.4.md", canonical())
    write(
        tmp_path / "docs/releases/next.md",
        NEXT + "\n<!-- notes-range-end: not-a-commit -->\n",
    )

    with pytest.raises(PromotionError, match="watermark"):
        plan_promotion(tmp_path, "2.4.1")


def test_malformed_backtick_opener_cannot_hide_extra_watermark(
    tmp_path: Path,
) -> None:
    extra = "<!-- notes-range-end: 2222222222222222222222222222222222222222 -->"
    staged = NEXT + f"\n```markdown`invalid\n{extra}\n```\n"
    write(tmp_path / "docs/releases/2.4.md", canonical())
    write(tmp_path / "docs/releases/next.md", staged)

    with pytest.raises(PromotionError, match="watermark"):
        plan_promotion(tmp_path, "2.4.1")


def test_fenced_only_canonical_watermark_refuses(tmp_path: Path) -> None:
    watermark = "<!-- notes-range-end: 1111111111111111111111111111111111111111 -->"
    page = canonical().replace(watermark, "")
    page += f"\n```markdown\n{watermark}\n```\n"
    write(tmp_path / "docs/releases/2.4.md", page)
    write(tmp_path / "docs/releases/next.md", NEXT)

    with pytest.raises(PromotionError, match="watermark"):
        plan_promotion(tmp_path, "2.4.1")


def test_fenced_watermark_example_survives_metadata_promotion(tmp_path: Path) -> None:
    example = "<!-- notes-range-end: 2222222222222222222222222222222222222222 -->"
    staged = NEXT + f"\n```markdown\n{example}\n```\n"
    write(tmp_path / "docs/releases/2.4.md", canonical())
    write(tmp_path / "docs/releases/next.md", staged)

    page = plan_promotion(tmp_path, "2.4.1").writes[tmp_path / "docs/releases/2.4.md"]

    assert (
        page.count("<!-- notes-range-end: 0123456789abcdef0123456789abcdef01234567 -->")
        == 1
    )
    assert example in page
    assert "notes-range-end: 1111111111111111111111111111111111111111" not in page


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_heading_conversion_ignores_fenced_code(tmp_path: Path, fence: str) -> None:
    staged = NEXT + f"\n{fence}markdown\n## literal example\n{fence}\n"
    write(tmp_path / "docs/releases/2.4.md", canonical("v2.4.0"))
    write(tmp_path / "docs/releases/next.md", staged)

    page = plan_promotion(tmp_path, "2.4.1").writes[tmp_path / "docs/releases/2.4.md"]

    assert "### Credential rotation" in page
    assert "## literal example" in page
    assert "### literal example" not in page


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_heading_conversion_recognizes_three_space_indented_fences(
    tmp_path: Path,
    fence: str,
) -> None:
    staged = NEXT + f"\n   {fence}markdown\n## literal example\n   {fence}\n"
    write(tmp_path / "docs/releases/2.4.md", canonical("v2.4.0"))
    write(tmp_path / "docs/releases/next.md", staged)

    page = plan_promotion(tmp_path, "2.4.1").writes[tmp_path / "docs/releases/2.4.md"]

    assert "## literal example" in page
    assert "### literal example" not in page


@pytest.mark.parametrize(
    ("staged_heading", "promoted_heading"),
    [
        ("## Credential rotation", "### Credential rotation"),
        ("   ## Credential rotation", "   ### Credential rotation"),
        ("  ##\tCredential rotation", "  ###\tCredential rotation"),
        (" ## Credential rotation ##", " ### Credential rotation ##"),
    ],
)
def test_heading_conversion_preserves_commonmark_atx_indentation(
    tmp_path: Path,
    staged_heading: str,
    promoted_heading: str,
) -> None:
    staged = NEXT.replace("## Credential rotation", staged_heading)
    write(tmp_path / "docs/releases/2.4.md", canonical("v2.4.0"))
    write(tmp_path / "docs/releases/next.md", staged)

    page = plan_promotion(tmp_path, "2.4.1").writes[tmp_path / "docs/releases/2.4.md"]

    assert promoted_heading in page.splitlines()


def test_patch_sections_remain_ascending_and_undated(tmp_path: Path) -> None:
    write(tmp_path / "docs/releases/2.4.md", with_patch("v2.4.1"))
    write(tmp_path / "docs/releases/next.md", NEXT)

    page = plan_promotion(tmp_path, "2.4.2").writes[tmp_path / "docs/releases/2.4.md"]

    assert page.index("## v2.4.1") < page.index("## v2.4.2")
    assert "## v2.4.2 (" not in page


def test_indented_patch_sections_participate_in_order_validation(
    tmp_path: Path,
) -> None:
    page = with_patch("v2.4.2").replace("## v2.4.2", "   ## v2.4.2 ###")
    write(tmp_path / "docs/releases/2.4.md", page)
    write(tmp_path / "docs/releases/next.md", NEXT)

    with pytest.raises(PromotionError, match="ascending"):
        plan_promotion(tmp_path, "2.4.1")


def test_indented_patch_sections_participate_in_series_validation(
    tmp_path: Path,
) -> None:
    page = with_patch("v9.9.1").replace("## v9.9.1", "  ## v9.9.1")
    write(tmp_path / "docs/releases/2.4.md", page)
    write(tmp_path / "docs/releases/next.md", NEXT)

    with pytest.raises(PromotionError, match="canonical minor series"):
        plan_promotion(tmp_path, "2.4.2")


def test_out_of_order_patch_refuses(tmp_path: Path) -> None:
    write(tmp_path / "docs/releases/2.4.md", with_patch("v2.4.2"))
    write(tmp_path / "docs/releases/next.md", NEXT)
    before = snapshot(tmp_path)

    with pytest.raises(PromotionError, match="ascending"):
        plan_promotion(tmp_path, "2.4.1")

    assert snapshot(tmp_path) == before


def test_dated_patch_heading_refuses(tmp_path: Path) -> None:
    write(
        tmp_path / "docs/releases/2.4.md",
        with_patch("v2.4.1").replace("## v2.4.1", "## v2.4.1 (2026-08-24)"),
    )
    write(tmp_path / "docs/releases/next.md", NEXT)
    before = snapshot(tmp_path)

    with pytest.raises(PromotionError, match="undated"):
        plan_promotion(tmp_path, "2.4.2")

    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_patch_order_ignores_fenced_heading_examples(
    tmp_path: Path,
    fence: str,
) -> None:
    example = f"   {fence}markdown\n## v9.9.9\n   {fence}\n\n"
    page = with_patch("v2.4.1").replace(
        "<!-- PATCH-RELEASES-END -->",
        example + "<!-- PATCH-RELEASES-END -->",
    )
    write(tmp_path / "docs/releases/2.4.md", page)
    write(tmp_path / "docs/releases/next.md", NEXT)

    promoted = plan_promotion(tmp_path, "2.4.2").writes[
        tmp_path / "docs/releases/2.4.md"
    ]

    assert promoted.index("## v2.4.1") < promoted.index("## v2.4.2")
    assert "## v9.9.9" in promoted


def test_malformed_backtick_opener_cannot_hide_patch_heading(
    tmp_path: Path,
) -> None:
    example = "```markdown`invalid\n## v9.9.9\n```\n\n"
    page = with_patch("v2.4.1").replace(
        "<!-- PATCH-RELEASES-END -->",
        example + "<!-- PATCH-RELEASES-END -->",
    )
    write(tmp_path / "docs/releases/2.4.md", page)
    write(tmp_path / "docs/releases/next.md", NEXT)

    with pytest.raises(PromotionError, match="patch headings"):
        plan_promotion(tmp_path, "2.4.2")


def test_tilde_fence_info_string_may_contain_backticks(tmp_path: Path) -> None:
    example = "~~~markdown`valid\n## v9.9.9\n~~~\n\n"
    page = with_patch("v2.4.1").replace(
        "<!-- PATCH-RELEASES-END -->",
        example + "<!-- PATCH-RELEASES-END -->",
    )
    write(tmp_path / "docs/releases/2.4.md", page)
    write(tmp_path / "docs/releases/next.md", NEXT)

    promoted = plan_promotion(tmp_path, "2.4.2").writes[
        tmp_path / "docs/releases/2.4.md"
    ]

    assert "## v9.9.9" in promoted


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda text: text.replace("# Next release", "# Upcoming release", 1), "start"),
        (lambda text: text + "\n# Next release\n", "exactly once"),
        (
            lambda text: text.replace(
                "<!-- notes-range-end: 0123456789abcdef0123456789abcdef01234567 -->",
                "",
                1,
            ),
            "watermark",
        ),
        (
            lambda text: (
                text
                + "\n<!-- notes-range-end: 2222222222222222222222222222222222222222 -->\n"
            ),
            "watermark",
        ),
        (
            lambda text: text.replace("<!-- RELEASE-SUMMARY NEXT START -->", "", 1),
            "summary start",
        ),
        (
            lambda text: text + "\n<!-- RELEASE-SUMMARY NEXT START -->\n",
            "summary start",
        ),
        (
            lambda text: text.replace("<!-- RELEASE-SUMMARY NEXT END -->", "", 1),
            "summary end",
        ),
        (lambda text: text + "\n<!-- RELEASE-SUMMARY NEXT END -->\n", "summary end"),
        (
            lambda text: text.replace(
                "Operators can now rotate credentials without restarting the server.",
                "",
            ),
            "must not be empty",
        ),
    ],
)
def test_malformed_next_refuses_without_changes(
    tmp_path: Path,
    mutate: Callable[[str], str],
    message: str,
) -> None:
    write(tmp_path / "docs/releases/2.4.md", canonical())
    write(tmp_path / "docs/releases/next.md", mutate(NEXT))
    before = snapshot(tmp_path)

    with pytest.raises(PromotionError, match=message):
        plan_promotion(tmp_path, "2.4.1")

    assert snapshot(tmp_path) == before


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda text: text.replace("# 2.4", "# 2.4 release", 1), "canonical page"),
        (lambda text: text + "\n# 2.4\n", "canonical page"),
        (
            lambda text: text.replace(
                "<!-- notes-range-end: 1111111111111111111111111111111111111111 -->",
                "",
                1,
            ),
            "watermark",
        ),
        (
            lambda text: (
                text
                + "\n<!-- notes-range-end: 2222222222222222222222222222222222222222 -->\n"
            ),
            "watermark",
        ),
        (
            lambda text: text.replace("<!-- PATCH-RELEASES-START -->", "", 1),
            "patch start",
        ),
        (lambda text: text + "\n<!-- PATCH-RELEASES-START -->\n", "patch start"),
        (lambda text: text.replace("<!-- PATCH-RELEASES-END -->", "", 1), "patch end"),
        (lambda text: text + "\n<!-- PATCH-RELEASES-END -->\n", "patch end"),
    ],
)
def test_malformed_canonical_page_refuses_without_changes(
    tmp_path: Path,
    mutate: Callable[[str], str],
    message: str,
) -> None:
    write(tmp_path / "docs/releases/2.4.md", mutate(canonical()))
    write(tmp_path / "docs/releases/next.md", NEXT)
    before = snapshot(tmp_path)

    with pytest.raises(PromotionError, match=message):
        plan_promotion(tmp_path, "2.4.1")

    assert snapshot(tmp_path) == before


def test_apply_plan_writes_and_deletes_without_staging(tmp_path: Path) -> None:
    old = tmp_path / "old.md"
    new = tmp_path / "nested/new.md"
    write(old, "old\n")

    apply_plan(PromotionPlan(tmp_path, {new: "new\n"}, (old,), ()))

    assert new.read_bytes() == b"new\n"
    assert not old.exists()
    assert not (new.parent / ".new.md.tmp").exists()


def test_patch_cli_applies_and_stages_only_release_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write(tmp_path / "docs/releases/2.4.md", canonical())
    write(tmp_path / "docs/releases/next.md", NEXT)
    write(tmp_path / "unrelated.txt", "committed\n")
    init_repository(tmp_path)
    write(tmp_path / "unrelated.txt", "unstaged\n")
    write(tmp_path / "untracked.txt", "untracked\n")
    monkeypatch.chdir(tmp_path)

    assert main(["2.4.1-rc.1"]) == 0

    assert cached_status(tmp_path) == [
        "M\tdocs/releases/2.4.md",
        "D\tdocs/releases/next.md",
    ]
    assert (tmp_path / "unrelated.txt").read_text(encoding="utf-8") == "unstaged\n"
    assert (
        subprocess.run(
            ["git", "diff", "--quiet", "--", "unrelated.txt"],
            cwd=tmp_path,
            check=False,
        ).returncode
        == 1
    )
    assert (tmp_path / "untracked.txt").read_text(encoding="utf-8") == "untracked\n"


def test_new_series_cli_applies_and_stages_exact_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write(tmp_path / "docs/releases/next.md", NEXT)
    write(tmp_path / "docs/releases/index.md", INDEX)
    init_repository(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert main(["2.4.0"]) == 0

    assert cached_status(tmp_path, detect_renames=False) == [
        "A\tdocs/releases/2.4.md",
        "M\tdocs/releases/index.md",
        "D\tdocs/releases/next.md",
    ]


def test_existing_target_cli_is_a_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write(tmp_path / "docs/releases/2.4.md", canonical())
    init_repository(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert main(["2.4.0-rc.2"]) == 0

    assert cached_status(tmp_path) == []
    assert "nothing to do" in capsys.readouterr().out


@pytest.mark.parametrize(
    "staged",
    [
        NEXT.replace("# Next release", "# Upcoming release", 1),
        NEXT + "\n# Next release\n",
        NEXT.replace(
            "<!-- notes-range-end: 0123456789abcdef0123456789abcdef01234567 -->",
            "",
            1,
        ),
        NEXT + "\n<!-- notes-range-end: 2222222222222222222222222222222222222222 -->\n",
        NEXT.replace("<!-- RELEASE-SUMMARY NEXT START -->", "", 1),
        NEXT + "\n<!-- RELEASE-SUMMARY NEXT START -->\n",
        NEXT.replace("<!-- RELEASE-SUMMARY NEXT END -->", "", 1),
        NEXT + "\n<!-- RELEASE-SUMMARY NEXT END -->\n",
        NEXT.replace(
            "Operators can now rotate credentials without restarting the server.", ""
        ),
    ],
)
def test_cli_next_refusals_preserve_every_source_byte(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    staged: str,
) -> None:
    write(tmp_path / "docs/releases/2.4.md", canonical())
    write(tmp_path / "docs/releases/next.md", staged)
    init_repository(tmp_path)
    before = snapshot(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert main(["2.4.1"]) == 1

    assert snapshot(tmp_path) == before
    assert cached_status(tmp_path) == []


@pytest.mark.parametrize(
    "page",
    [
        canonical().replace("# 2.4", "# 2.4 release", 1),
        canonical() + "\n# 2.4\n",
        canonical().replace(
            "<!-- notes-range-end: 1111111111111111111111111111111111111111 -->",
            "",
            1,
        ),
        canonical()
        + "\n<!-- notes-range-end: 2222222222222222222222222222222222222222 -->\n",
        canonical().replace("<!-- PATCH-RELEASES-START -->", "", 1),
        canonical() + "\n<!-- PATCH-RELEASES-START -->\n",
        canonical().replace("<!-- PATCH-RELEASES-END -->", "", 1),
        canonical() + "\n<!-- PATCH-RELEASES-END -->\n",
    ],
)
def test_cli_canonical_refusals_preserve_every_source_byte(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    page: str,
) -> None:
    write(tmp_path / "docs/releases/2.4.md", page)
    write(tmp_path / "docs/releases/next.md", NEXT)
    init_repository(tmp_path)
    before = snapshot(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert main(["2.4.1"]) == 1

    assert snapshot(tmp_path) == before
    assert cached_status(tmp_path) == []
