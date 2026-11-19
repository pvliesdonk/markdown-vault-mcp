"""Offline checks for the PostgreSQL image currency job."""

import io
import json
import subprocess
from unittest.mock import patch

import pytest
from scripts.check_postgres_images import (
    FOOTER,
    ISSUE_LABEL,
    ISSUE_TITLE,
    _run_gh,
    _run_url,
    hub_tags,
    issue_body,
    lagging,
    main,
    sync_issue,
)

IMAGES = [
    "pgvector/pgvector:0.7.0-pg15",
    "pgvector/pgvector:0.7.4-pg17",
    *[f"pgvector/pgvector:0.8.7-pg{major}" for major in range(15, 19)],
    "tensorchord/vchord-suite:pg17-20260501",
]
PG_TAGS = [f"0.8.7-pg{major}" for major in range(15, 19)]
VC_TAGS = ["pg17-20260501"]


def test_new_pgvector_patch_and_minor_are_reported() -> None:
    assert lagging(IMAGES, [*PG_TAGS, "0.8.8-pg17"], VC_TAGS) == [
        "pgvector/pgvector:0.8.7-pg17 is behind 0.8.8-pg17"
    ]

    tags = [f"0.9.0-pg{major}" for major in range(15, 19)]
    assert lagging(IMAGES, tags, VC_TAGS) == [
        f"pgvector/pgvector:0.8.7-pg{major} is behind 0.9.0-pg{major}"
        for major in range(15, 19)
    ]


def test_floors_equal_tags_and_variants_are_ignored() -> None:
    tags = [*PG_TAGS, "0.7.9-pg15", "0.7.5-pg17", "0.8.8-pg17-bookworm"]
    variants = [*VC_TAGS, "pg17-20260601-arm64", "pg17-v0.4.3", "pg18-20260601"]
    assert lagging(IMAGES, tags, variants) == []


def test_new_major_is_a_support_decision() -> None:
    assert lagging(IMAGES, [*PG_TAGS, "0.8.7-pg19"], VC_TAGS) == [
        "PostgreSQL 19 has pgvector images (0.8.7-pg19); adding it is a support decision"
    ]


def test_new_vchord_date_is_reported() -> None:
    assert lagging(IMAGES, PG_TAGS, [*VC_TAGS, "pg17-20260601"]) == [
        "tensorchord/vchord-suite:pg17-20260501 is behind pg17-20260601"
    ]


@pytest.mark.parametrize(
    ("pg_tags", "vc_tags", "missing"),
    [
        ([], VC_TAGS, "15"),
        (PG_TAGS, [], "17"),
        (["0.8.8-pg15-bookworm"], VC_TAGS, "15"),
        (PG_TAGS, ["pg17-v0.4.3"], "17"),
        (
            [tag for tag in PG_TAGS if not tag.endswith("pg16")],
            VC_TAGS,
            "16",
        ),
    ],
)
def test_missing_comparable_tags_fail(
    pg_tags: list[str], vc_tags: list[str], missing: str
) -> None:
    with pytest.raises(KeyError, match=missing):
        lagging(IMAGES, pg_tags, vc_tags)


def test_hub_follows_next_page() -> None:
    pages = [
        io.BytesIO(
            json.dumps(
                {
                    "results": [{"name": "first"}],
                    "next": "https://hub.docker.com/v2/page2",
                }
            ).encode()
        ),
        io.BytesIO(
            json.dumps({"results": [{"name": "second"}], "next": None}).encode()
        ),
    ]
    with patch("scripts.check_postgres_images.urlopen", side_effect=pages) as request:
        assert hub_tags("pgvector/pgvector") == ["first", "second"]
    assert request.call_args_list[1].args[0] == "https://hub.docker.com/v2/page2"


@pytest.mark.parametrize("body", [{}, {"results": [{}], "next": None}])
def test_malformed_hub_response_fails(body: dict) -> None:
    with (
        patch(
            "scripts.check_postgres_images.urlopen",
            return_value=io.BytesIO(json.dumps(body).encode()),
        ),
        pytest.raises(KeyError),
    ):
        hub_tags("pgvector/pgvector")


def test_page_cap_fails_instead_of_reporting_current() -> None:
    body = json.dumps(
        {"results": [], "next": "https://hub.docker.com/v2/next"}
    ).encode()
    with (
        patch(
            "scripts.check_postgres_images.urlopen",
            side_effect=lambda *_args, **_kwargs: io.BytesIO(body),
        ),
        pytest.raises(ValueError, match="page"),
    ):
        hub_tags("pgvector/pgvector")


@pytest.mark.parametrize(
    "next_url",
    [
        "https://example.test/page2",
        "https://hub.docker.com.example.test/page2",
        "http://hub.docker.com/v2/page2",
    ],
)
def test_hub_refuses_next_page_off_docker_hub(next_url: str) -> None:
    page = json.dumps({"results": [{"name": "first"}], "next": next_url}).encode()
    with (
        patch(
            "scripts.check_postgres_images.urlopen",
            return_value=io.BytesIO(page),
        ) as request,
        pytest.raises(ValueError, match=r"hub\.docker\.com"),
    ):
        hub_tags("pgvector/pgvector")
    assert request.call_count == 1


class FakeGh:
    """Records gh calls; serves one canned ``issue list`` response."""

    def __init__(self, open_issues: list[dict[str, object]]) -> None:
        self.open_issues = open_issues
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, args: list[str], stdin: str | None) -> str:
        self.calls.append((args, stdin))
        return json.dumps(self.open_issues) if args[:2] == ["issue", "list"] else ""

    def writes(self) -> list[list[str]]:
        return [args[:2] for args, _ in self.calls if args[:2] != ["issue", "list"]]


LAG = ["pgvector/pgvector:0.8.7-pg17 is behind 0.8.8-pg17"]
RUN = "https://github.com/o/r/actions/runs/1"


def test_issue_body_lists_findings_and_ends_with_footer() -> None:
    body = issue_body(LAG)
    assert f"- {LAG[0]}" in body
    assert body.endswith(FOOTER)
    assert FOOTER.startswith("---\n_Agent-authored: written by ")


def test_lag_without_open_issue_creates_one() -> None:
    gh = FakeGh([{"number": 9, "title": "unrelated", "body": ""}])
    assert sync_issue(LAG, gh, RUN) == "created"
    args, stdin = gh.calls[-1]
    assert args[:2] == ["issue", "create"]
    assert ISSUE_TITLE in args and ISSUE_LABEL in args
    assert stdin == issue_body(LAG)
    assert gh.calls[0][0][4:6] == ["--label", ISSUE_LABEL]


def test_lag_with_same_body_leaves_issue_alone() -> None:
    stored = issue_body(LAG).strip().replace("\n", "\r\n")
    gh = FakeGh([{"number": 7, "title": ISSUE_TITLE, "body": stored}])
    assert sync_issue(LAG, gh, RUN) == "unchanged"
    assert gh.writes() == []


def test_changed_lag_refreshes_body_and_comments() -> None:
    gh = FakeGh([{"number": 7, "title": ISSUE_TITLE, "body": "old"}])
    assert sync_issue(LAG, gh, RUN) == "updated"
    assert gh.writes() == [["issue", "edit"], ["issue", "comment"]]
    assert gh.calls[1] == (["issue", "edit", "7", "--body-file", "-"], issue_body(LAG))
    comment = gh.calls[2][1]
    assert comment is not None and RUN in comment and comment.endswith(FOOTER)


def test_current_images_close_the_open_issue() -> None:
    gh = FakeGh([{"number": 7, "title": ISSUE_TITLE, "body": "old"}])
    assert sync_issue([], gh, RUN) == "closed"
    args, _ = gh.calls[-1]
    assert args[:3] == ["issue", "close", "7"]
    assert RUN in args[-1] and args[-1].endswith(FOOTER)


def test_current_images_without_issue_do_nothing() -> None:
    gh = FakeGh([])
    assert sync_issue([], gh, RUN) == "none"
    assert gh.writes() == []


def _fake_hub(repository: str) -> list[str]:
    if repository == "pgvector/pgvector":
        return ["0.8.7-pg15", "0.8.7-pg16", "0.8.7-pg17", "0.8.7-pg18", "0.8.8-pg17"]
    return ["pg17-20260501"]


def test_dry_run_prints_the_issue_body_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch("scripts.check_postgres_images.hub_tags", side_effect=_fake_hub),
        patch("scripts.check_postgres_images._run_gh") as gh,
    ):
        assert main([]) == 0
    gh.assert_not_called()
    out = capsys.readouterr().out
    assert "::warning::pgvector/pgvector:0.8.7-pg17 is behind 0.8.8-pg17" in out
    assert out.rstrip().endswith(FOOTER.rstrip())


def test_sync_issue_flag_reaches_github(capsys: pytest.CaptureFixture[str]) -> None:
    with (
        patch("scripts.check_postgres_images.hub_tags", side_effect=_fake_hub),
        patch("scripts.check_postgres_images._run_gh", FakeGh([])),
    ):
        assert main(["--sync-issue"]) == 0
    assert "Tracking issue: created" in capsys.readouterr().out


def test_hub_failure_fails_before_touching_the_issue() -> None:
    with (
        patch(
            "scripts.check_postgres_images.hub_tags",
            side_effect=ValueError("Docker Hub page limit exceeded"),
        ),
        patch("scripts.check_postgres_images._run_gh") as gh,
        pytest.raises(ValueError),
    ):
        main(["--sync-issue"])
    gh.assert_not_called()


def test_run_gh_passes_stdin_and_fails_without_gh() -> None:
    done = subprocess.CompletedProcess([], 0, stdout="out")
    with (
        patch("scripts.check_postgres_images.shutil.which", return_value="/bin/gh"),
        patch("scripts.check_postgres_images.subprocess.run", return_value=done) as run,
    ):
        assert _run_gh(["issue", "list"], "body") == "out"
    assert run.call_args.args[0] == ["/bin/gh", "issue", "list"]
    assert run.call_args.kwargs["input"] == "body"
    assert run.call_args.kwargs["check"] is True
    with (
        patch("scripts.check_postgres_images.shutil.which", return_value=None),
        pytest.raises(FileNotFoundError),
    ):
        _run_gh(["issue", "list"], None)


def test_run_url_names_the_workflow_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    assert _run_url() == "https://github.com/o/r/actions/runs/42"
