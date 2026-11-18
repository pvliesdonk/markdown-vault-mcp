"""Check range images against Docker Hub; see docs/design/reference/docker-hub-range-tags.md.

Run without arguments, it prints what it found and the tracking-issue body it
would file (a dry run). With ``--sync-issue`` (the scheduled ``currency`` job),
it opens or refreshes the tracking issue while images are behind and closes it
once they are current. A failed Docker Hub read or ``gh`` call exits non-zero
and leaves the issue untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

import yaml

_PGVECTOR = re.compile(
    r"^pgvector/pgvector:(\d+)\.(\d+)\.(\d+)-pg(?P<postgres_major>\d+)$"
)
_VCHORD = re.compile(r"^tensorchord/vchord-suite:pg(?P<postgres_major>\d+)-(\d{8})$")
_ROOT = Path(__file__).resolve().parent.parent
_HUB_HOST = "hub.docker.com"

ISSUE_TITLE = "PostgreSQL range images are behind Docker Hub"
ISSUE_LABEL = "ci"
FOOTER = (
    "---\n"
    "_Agent-authored: written by the scheduled `currency` job of "
    "`.github/workflows/postgres-range.yml` (a script written with "
    "[Claude Code](https://claude.ai/code)) under the repository's Actions "
    "token. Observation, not a decision by the account holder._\n"
)

Gh = Callable[[list[str], str | None], str]


def _pg_versions(images: list[str]) -> dict[int, tuple[tuple[int, ...], str]]:
    versions: dict[int, tuple[tuple[int, ...], str]] = {}
    for image in images:
        if match := _PGVECTOR.fullmatch(image):
            version = tuple(map(int, match.groups()[:3]))
            major = int(match.group("postgres_major"))
            versions[major] = max(
                versions.get(major, (version, image)), (version, image)
            )
    return versions


def _vchord_versions(images: list[str]) -> dict[int, tuple[str, str]]:
    versions: dict[int, tuple[str, str]] = {}
    for image in images:
        if match := _VCHORD.fullmatch(image):
            major = int(match.group("postgres_major"))
            value = (match.group(2), image)
            versions[major] = max(versions.get(major, value), value)
    return versions


def lagging(
    images: list[str], pgvector_tags: list[str], vchord_tags: list[str]
) -> list[str]:
    """Return descriptions of newer strict tags for the tested image families.

    Args:
        images: The workflow's pinned ``range`` matrix images.
        pgvector_tags: Every ``pgvector/pgvector`` tag name on Docker Hub.
        vchord_tags: Every ``tensorchord/vchord-suite`` tag name on Docker Hub.

    Returns:
        One sentence per pinned image that has a newer upstream tag, and one per
        newer PostgreSQL major; empty when the range is current.

    Raises:
        KeyError: A pinned PostgreSQL major has no comparable upstream tag.
    """
    pinned_pg = _pg_versions(images)
    pinned_vc = _vchord_versions(images)
    latest_pg = _pg_versions([f"pgvector/pgvector:{tag}" for tag in pgvector_tags])
    latest_vc = _vchord_versions(
        [f"tensorchord/vchord-suite:{tag}" for tag in vchord_tags]
    )
    messages = []
    for major, (version, image) in sorted(pinned_pg.items()):
        newest, tag = latest_pg[major]
        if newest > version:
            messages.append(f"{image} is behind {tag.split(':', 1)[1]}")
    for major, (_, tag) in sorted(latest_pg.items()):
        if pinned_pg and major > max(pinned_pg):
            messages.append(
                f"PostgreSQL {major} has pgvector images ({tag.split(':', 1)[1]}); adding it is a support decision"
            )
    for major, (date, image) in sorted(pinned_vc.items()):
        newest_date, tag = latest_vc[major]
        if newest_date > date:
            messages.append(f"{image} is behind {tag.split(':', 1)[1]}")
    return messages


def _checked_next(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != _HUB_HOST:
        raise ValueError(f"Docker Hub pagination left {_HUB_HOST}: {url}")
    return url


def hub_tags(repository: str) -> list[str]:
    """Fetch all Docker Hub tag names, failing on malformed or incomplete pages.

    Args:
        repository: The Docker Hub repository, such as ``pgvector/pgvector``.

    Returns:
        Every tag name across all pages.

    Raises:
        KeyError: A page lacks ``results``, ``next`` or a result's ``name``.
        ValueError: A ``next`` URL leaves ``https://hub.docker.com``, or the
            20-page cap is reached.
    """
    url = f"https://{_HUB_HOST}/v2/repositories/{repository}/tags?page_size=100"
    tags: list[str] = []
    for _ in range(20):
        with urlopen(url, timeout=20) as response:  # https only, checked
            body = json.load(response)
        tags += [result["name"] for result in body["results"]]
        if body["next"] is None:
            return tags
        url = _checked_next(body["next"])
    raise ValueError("Docker Hub page limit exceeded")


def issue_body(messages: list[str]) -> str:
    """Render the tracking issue's body for the images found behind.

    Args:
        messages: The non-empty output of :func:`lagging`.

    Returns:
        The Markdown body, ending with the agent-authored footer.
    """
    items = "\n".join(f"- {message}" for message in messages)
    return (
        "The weekly image currency check in `.github/workflows/postgres-range.yml` "
        "found newer upstream images than the pinned `range` matrix:\n\n"
        f"{items}\n\n"
        "Bumping a pin or adding a PostgreSQL major is a manual support decision "
        "(`docs/design/design.md`, PostgreSQL range). The check refreshes this "
        "body while the list changes and closes this issue once the pinned "
        "images are current.\n\n" + FOOTER
    )


def _same_text(stored: str, rendered: str) -> bool:
    # GitHub may store a body with CRLF line ends and without the final newline.
    return stored.replace("\r\n", "\n").strip() == rendered.strip()


def _open_issue(gh: Gh) -> dict[str, object] | None:
    listed = json.loads(
        gh(
            [
                "issue",
                "list",
                "--state",
                "open",
                "--label",
                ISSUE_LABEL,
                "--limit",
                "200",
                "--json",
                "number,title,body",
            ],
            None,
        )
    )
    return next((issue for issue in listed if issue["title"] == ISSUE_TITLE), None)


def sync_issue(messages: list[str], gh: Gh, run_url: str) -> str:
    """Open, refresh or close the tracking issue to match ``messages``.

    Args:
        messages: The output of :func:`lagging`; empty means current.
        gh: Runs ``gh`` with the given arguments and optional stdin, returning
            stdout and raising on a non-zero exit.
        run_url: The workflow run to cite in comments.

    Returns:
        What it did: ``created``, ``updated``, ``unchanged``, ``closed`` or
        ``none``.
    """
    issue = _open_issue(gh)
    if not messages:
        if issue is None:
            return "none"
        comment = f"The check in {run_url} found every pinned image current.\n\n"
        gh(
            ["issue", "close", str(issue["number"]), "--comment", comment + FOOTER],
            None,
        )
        return "closed"
    body = issue_body(messages)
    if issue is None:
        args = ["issue", "create", "--title", ISSUE_TITLE, "--label", ISSUE_LABEL]
        gh([*args, "--body-file", "-"], body)
        return "created"
    if _same_text(str(issue["body"]), body):
        return "unchanged"
    number = str(issue["number"])
    gh(["issue", "edit", number, "--body-file", "-"], body)
    comment = (
        f"The check in {run_url} found a changed list; the body above is refreshed.\n\n"
    )
    gh(["issue", "comment", number, "--body-file", "-"], comment + FOOTER)
    return "updated"


def _run_gh(args: list[str], stdin: str | None) -> str:
    executable = shutil.which("gh")
    if executable is None:
        raise FileNotFoundError("gh is not on PATH")
    done = subprocess.run(  # fixed argv, no shell
        [executable, *args], input=stdin, capture_output=True, text=True, check=True
    )
    return done.stdout


def _run_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    return f"{server}/{repository}/actions/runs/{run_id}"


def main(argv: list[str] | None = None) -> int:
    """Check the workflow's range matrix and report or sync the tracking issue.

    Args:
        argv: Command-line arguments; ``--sync-issue`` writes to GitHub.

    Returns:
        Zero whether or not images are behind; a failed read raises instead.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sync-issue", action="store_true")
    args = parser.parse_args(argv)
    workflow = yaml.safe_load(
        (_ROOT / ".github/workflows/postgres-range.yml").read_text(encoding="utf-8")
    )
    images = workflow["jobs"]["range"]["strategy"]["matrix"]["image"]
    messages = lagging(
        images, hub_tags("pgvector/pgvector"), hub_tags("tensorchord/vchord-suite")
    )
    for message in messages:
        print(f"::warning::{message}")
    if args.sync_issue:
        print(f"Tracking issue: {sync_issue(messages, _run_gh, _run_url())}")
    elif messages:
        print(issue_body(messages))
    if not messages:
        newest = [value[1] for value in _pg_versions(images).values()]
        newest += [value[1] for value in _vchord_versions(images).values()]
        print("Current range images checked: " + ", ".join(newest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
