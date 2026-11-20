"""Date filters refuse malformed text instead of trusting git's permissive parser."""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.managers.git_query import GitQueryManager

if TYPE_CHECKING:
    from pathlib import Path


def test_malformed_date_does_not_return_plausible_history(tmp_path: Path) -> None:
    """The reporter's invalid month/day must not silently select git's date."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    git = ["git", "-C", str(tmp_path), "-c", "user.name=Test", "-c", "user.email=t@t"]
    for date in ("2020-01-01T00:00:00Z", "2026-11-15T00:00:00Z"):
        (tmp_path / "note.md").write_text(date)
        subprocess.run([*git, "add", "note.md"], check=True)
        subprocess.run(
            [*git, "commit", "-q", "-m", date],
            env={**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date},
            check=True,
        )
    manager = GitQueryManager(GitWriteStrategy(), tmp_path)
    assert len(manager.get_history()) == 2
    with pytest.raises(InvalidRequestError, match="since"):
        manager.get_history(since="2026-13-45")


@pytest.mark.parametrize("argument", ["since", "until", "since_timestamp"])
@pytest.mark.parametrize(
    "value",
    [
        "zzz",
        "not a date at all",
        "2026-13-45",
        "2026-02-29",
        "2026-01-01T25:00:00",
        "2026-01-01T00:00:00+25:00",
        "",
        " ",
        "1 weke ago",
        "1 week ago garbage",
        "1 \u017fecond ago",
        "2026-01-01T00:00:00+01:99",
        "last Friday",
        "last friday",
        "June 1 2020",
        "@1600000000",
        "1960-01-01T00:00:00Z",
        "9999-01-01T00:00:00Z",
        "1970-01-01T00:00:00+01:00",
        "2099-12-31T23:59:59-01:00",
        "4294967296 days ago",
        "2147483647 seconds ago",
        "99999999 days ago",
        "100 years ago",
        "0001 day ago",
        "2020\x00",
    ],
)
def test_invalid_date_never_reaches_backend(
    tmp_path: Path, argument: str, value: str
) -> None:
    backend = MagicMock()
    manager = GitQueryManager(backend, tmp_path)
    with pytest.raises(InvalidRequestError, match=argument):
        if argument == "since_timestamp":
            manager.get_diff("note.md", since_timestamp=value)
        else:
            manager.get_history(**{argument: value})
    backend.get_file_history.assert_not_called()
    backend.get_file_diff.assert_not_called()


@pytest.mark.parametrize("argument", ["since", "until", "since_timestamp"])
@pytest.mark.parametrize(
    "value",
    [
        "2026-01-01",
        "2024-02-29",
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:00:00+0000",
        "2026-01-01T00:00:00+05:30",
        "2026-01-01 12:30:00",
        "1 week ago",
        "2 days 3 hours ago",
        "now",
        "yesterday",
    ],
)
def test_supported_dates_are_forwarded_unchanged(
    tmp_path: Path, argument: str, value: str
) -> None:
    backend = MagicMock()
    manager = GitQueryManager(backend, tmp_path)
    if argument == "since_timestamp":
        manager.get_diff("note.md", since_timestamp=value)
        assert backend.get_file_diff.call_args.kwargs["since_timestamp"] == value
    else:
        manager.get_history(**{argument: value})
        call = backend.get_file_history.call_args
        assert (call.args[2] if argument == "since" else call.kwargs["until"]) == value


@pytest.mark.parametrize(
    "value",
    [
        "1970-01-01T00:00:00Z",
        "2099-12-31T23:59:59Z",
        "2026-01-01T00:00:00+23:59",
        "2026-01-01T12:30:00.123456Z",
        "2026-01-01T12:30Z",
        "2026-01-01t12:30:00Z",
    ],
)
def test_accepted_instants_mean_the_same_to_git(git_repo: Path, value: str) -> None:
    """Validation must not approve a date git reinterprets as another instant."""
    from datetime import datetime

    from markdown_vault_mcp.managers.git_query import _validate_date

    _validate_date("since", value)
    parsed = subprocess.check_output(
        ["git", "-C", str(git_repo), "rev-parse", f"--since={value}"], text=True
    )
    assert int(parsed.removeprefix("--max-age=")) == int(
        datetime.fromisoformat(value).timestamp()
    )


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("1 week ago", 604800),
        ("2 days 3 hours ago", 183600),
        ("99999999 seconds ago", 99999999),
        ("0 seconds ago", 0),
        ("1 month ago", 31 * 86400),
        ("1 year ago", 365 * 86400),
    ],
)
def test_accepted_relative_quantities_mean_the_same_to_git(
    git_repo: Path, value: str, seconds: int
) -> None:
    from markdown_vault_mcp.managers.git_query import _validate_date

    _validate_date("since", value)
    now = 1794832496  # 2026-11-16T12:34:56Z; prior month has 31 days.
    parsed = subprocess.check_output(
        ["git", "-C", str(git_repo), "rev-parse", f"--since={value}"],
        env={**os.environ, "TZ": "UTC", "GIT_TEST_DATE_NOW": str(now)},
        text=True,
    )
    assert int(parsed.removeprefix("--max-age=")) == now - seconds


@pytest.mark.parametrize("argument", ["since", "until", "since_timestamp"])
@pytest.mark.parametrize("value", ["today", "Today", "TODAY"])
def test_today_is_sent_as_local_midnight(
    tmp_path: Path, argument: str, value: str
) -> None:
    """Git before 2.56 reads an unknown ``today`` as now, so it never reaches git."""
    from datetime import datetime

    backend = MagicMock()
    manager = GitQueryManager(backend, tmp_path)
    midnight = datetime.now().strftime("%Y-%m-%dT00:00:00")
    if argument == "since_timestamp":
        manager.get_diff("note.md", since_timestamp=value)
        sent = backend.get_file_diff.call_args.kwargs["since_timestamp"]
    else:
        manager.get_history(**{argument: value})
        call = backend.get_file_history.call_args
        sent = call.args[2] if argument == "since" else call.kwargs["until"]
    assert sent == midnight


def test_today_rewrite_means_midnight_to_git(git_repo: Path) -> None:
    from datetime import datetime

    from markdown_vault_mcp.managers.git_query import _validate_date

    now = datetime(2026, 11, 16, 12, 34, 56)
    sent = _validate_date("since", "today", now=now)
    assert sent == "2026-11-16T00:00:00"
    parsed = subprocess.check_output(
        ["git", "-C", str(git_repo), "rev-parse", f"--since={sent}"], text=True
    )
    assert int(parsed.removeprefix("--max-age=")) == int(
        datetime(2026, 11, 16).timestamp()
    )
