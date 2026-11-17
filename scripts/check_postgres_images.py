"""Check range images against Docker Hub; see docs/design/reference/docker-hub-range-tags.md."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.request import urlopen

import yaml

_PGVECTOR = re.compile(r"^pgvector/pgvector:(\d+)\.(\d+)\.(\d+)-pg(\d+)$")
_VCHORD = re.compile(r"^tensorchord/vchord-suite:pg(\d+)-(\d{8})$")
_ROOT = Path(__file__).resolve().parent.parent


def _pg_versions(images: list[str]) -> dict[int, tuple[tuple[int, ...], str]]:
    versions: dict[int, tuple[tuple[int, ...], str]] = {}
    for image in images:
        if match := _PGVECTOR.fullmatch(image):
            version = tuple(map(int, match.groups()[:3]))
            major = int(match.group(4))
            versions[major] = max(
                versions.get(major, (version, image)), (version, image)
            )
    return versions


def _vchord_versions(images: list[str]) -> dict[int, tuple[str, str]]:
    versions: dict[int, tuple[str, str]] = {}
    for image in images:
        if match := _VCHORD.fullmatch(image):
            major = int(match.group(1))
            value = (match.group(2), image)
            versions[major] = max(versions.get(major, value), value)
    return versions


def lagging(
    images: list[str], pgvector_tags: list[str], vchord_tags: list[str]
) -> list[str]:
    """Return descriptions of newer strict tags for the tested image families."""
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


def hub_tags(repository: str) -> list[str]:
    """Fetch all Docker Hub tag names, failing on malformed or incomplete pages."""
    url: str = f"https://hub.docker.com/v2/repositories/{repository}/tags?page_size=100"
    tags: list[str] = []
    for _ in range(20):
        with urlopen(url, timeout=20) as response:
            body = json.load(response)
        tags += [result["name"] for result in body["results"]]
        if body["next"] is None:
            return tags
        url = body["next"]
    raise ValueError("Docker Hub page limit exceeded")


def main() -> int:
    """Check the workflow's range matrix and return a failing status if behind."""
    workflow = yaml.safe_load(
        (_ROOT / ".github/workflows/postgres-range.yml").read_text(encoding="utf-8")
    )
    images = workflow["jobs"]["range"]["strategy"]["matrix"]["image"]
    messages = lagging(
        images, hub_tags("pgvector/pgvector"), hub_tags("tensorchord/vchord-suite")
    )
    for message in messages:
        print(f"::error::{message}")
    if messages:
        return 1
    newest = [value[1] for value in _pg_versions(images).values()]
    newest += [value[1] for value in _vchord_versions(images).values()]
    print("Current range images checked: " + ", ".join(newest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
