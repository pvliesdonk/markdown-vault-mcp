#!/usr/bin/env python3
"""Warn about or finalize a release package using documented GitHub APIs.

GitHub behavior: docs/design/reference/github-planning-objects.md.
Packages are open milestones named NNN content-name. Listing always
collects every page before choosing the lowest ordinal. A versioned title
reserves the selected milestone for that release before membership changes:
workflow retries and explicit --resume calls resume only that reservation,
so even a cut originally made without a package cannot consume the next one.
Failures are advisory, but an unsuccessful removal never closes a package.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

PACKAGE = re.compile(r"^([0-9]{3}) (\S.*)$")
RELEASED = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+ ")


def api(path: str, *, fields: dict[str, Any] | None = None) -> Any:
    """Read all pages, or PATCH typed JSON and return the server response."""
    command = ["gh", "api", path]
    if fields is None:
        command += ["--paginate", "--slurp"]
    else:
        command += ["--method", "PATCH", "--input", "-"]
    result = subprocess.run(
        command,
        input=None if fields is None else json.dumps(fields),
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    data = json.loads(result.stdout)
    return [item for page in data for item in page] if fields is None else data


def report(message: str, *, warning: bool = False) -> None:
    """Write safe workflow annotations and a durable job summary."""
    # Tracker titles are data: prevent newlines from becoming workflow commands.
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{'warning' if warning else 'notice'}::{escaped}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write(f"- {message.replace(chr(10), ' ').replace(chr(13), ' ')}\n")


def current(milestones: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Select the earliest package; reject ambiguous duplicate ordinals."""
    candidates = sorted(
        (
            m
            for m in milestones
            if m["state"] == "open" and PACKAGE.fullmatch(m["title"])
        ),
        key=lambda m: m["title"],
    )
    if not candidates:
        return None
    if len(candidates) > 1 and candidates[0]["title"][:3] == candidates[1]["title"][:3]:
        raise ValueError(
            "Duplicate current package ordinal; give packages unique ordinals"
        )
    return candidates[0]


def warn(milestones: list[dict[str, Any]]) -> None:
    """Surface unfinished finalizations and open work in the current package."""
    pending = [
        m for m in milestones if m["state"] == "open" and RELEASED.match(m["title"])
    ]
    for milestone in pending:
        report(
            f"Package finalization unfinished: {milestone['title']}; "
            "resume its package bookkeeping before the next cut",
            warning=True,
        )
    package = current(milestones)
    if package and package["open_issues"]:
        report(
            f"Current package {package['title']} — {package['open_issues']} "
            "open item(s); not yet safe to cut from a quiescent trunk",
            warning=True,
        )


def reserve(
    milestones: list[dict[str, Any]], repo: str, version: str, *, resume_only: bool
) -> dict[str, Any] | None:
    """Bind this cut to one milestone, resuming an earlier attempt if present."""
    prefix = f"v{version} "
    matches = [m for m in milestones if m["title"].startswith(prefix)]
    if len(matches) > 1:
        raise ValueError(
            f"Multiple packages recorded for v{version}; reconcile manually"
        )
    if matches:
        package = matches[0]
        if package["state"] == "closed":
            report(f"Package for v{version} already closed; no changes")
            return None
    else:
        if resume_only:
            report(
                f"No package reserved for v{version}; recovery selects nothing. "
                "Verify the original cut before starting any new finalization",
                warning=True,
            )
            return None
        if any(m["state"] == "open" and RELEASED.match(m["title"]) for m in milestones):
            raise ValueError(
                "An earlier package finalization is unfinished; retry it first"
            )
        selected = current(milestones)
        if selected is None:
            report("No current package milestone; no changes")
            return None
        title = prefix + selected["title"].split(" ", 1)[1]
        package = api(
            f"repos/{repo}/milestones/{selected['number']}", fields={"title": title}
        )
        if package["title"] != title or package["state"] != "open":
            raise ValueError("Package reservation was not applied; reconcile manually")
    return package


def finalize(package: dict[str, Any], repo: str, version: str) -> None:
    """Return remaining work to backlog, then close the reserved milestone."""
    endpoint = f"repos/{repo}/milestones/{package['number']}"
    items_path = (
        f"repos/{repo}/issues?milestone={package['number']}&state=open&per_page=100"
    )
    # Snapshot all pages BEFORE removing members: mutating while paginating
    # would shift later pages and skip items.
    for item in api(items_path):
        updated = api(
            f"repos/{repo}/issues/{item['number']}", fields={"milestone": None}
        )
        # GitHub can silently ignore milestone writes without push access.
        if "milestone" not in updated or updated["milestone"] is not None:
            raise ValueError(f"Milestone removal for #{item['number']} was not applied")
        kind = "PR" if "pull_request" in item else "issue"
        report(
            f"v{version}: returned {kind} #{item['number']} to backlog; "
            "assign a future package deliberately"
        )
    # Recheck for concurrent membership changes; never hide visible open work.
    if api(items_path):
        raise ValueError("Open items remain in the package; retry before closing it")
    closed = api(endpoint, fields={"state": "closed"})
    if closed["state"] != "closed":
        raise ValueError("Package closure was not applied; resume its bookkeeping")
    report(f"Closed package {package['title']} (milestone #{package['number']})")


def run(mode: str, repo: str, version: str = "", *, resume_only: bool = False) -> None:
    """Apply the package convention; callers restrict close to stable trunk."""
    if mode == "close" and not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Package closure requires a stable X.Y.Z version")
    milestones = api(f"repos/{repo}/milestones?state=all&per_page=100")
    if mode == "warn":
        warn(milestones)
        return
    # Actions sets this to >1 on every rerun, including rerun-all-jobs.
    # Before a reservation exists we cannot know whether the prior attempt
    # saw no package or failed before selecting one. Recovery must not guess.
    resume_only = resume_only or int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")) > 1
    package = reserve(milestones, repo, version, resume_only=resume_only)
    if package is not None:
        finalize(package, repo, version)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("warn", "close"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--version", default="")
    parser.add_argument(
        "--resume", action="store_true", help="Only resume this version's reservation"
    )
    args = parser.parse_args()
    try:
        run(args.mode, args.repo, args.version, resume_only=args.resume)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as exc:
        report(f"Package bookkeeping incomplete: {exc}", warning=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
