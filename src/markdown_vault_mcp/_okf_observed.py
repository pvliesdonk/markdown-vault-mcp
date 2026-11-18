"""Compare supplied review claims with instance-local file observations."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.okf import (
    _parse_stale_after,
    _review_recorded,
    verified_entries,
)

if TYPE_CHECKING:
    from markdown_vault_mcp.indexing.observations import Observation


def review_observation(
    metadata: dict[str, Any],
    row: Observation | None,
    etag: str | None,
    snapshot: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Compare local bytes with the latest supplied review and snapshot claim.

    Args:
        metadata: Supplied note metadata.
        row: Locally observed state for the path, if any.
        etag: Digest of the returned whole-note bytes.
        snapshot: Digest of the returned concept, excluding review metadata.

    Returns:
        An available comparison or an unavailable observation.

    Raises:
        ValueError: If the current concept snapshot has an invalid shape.
    """
    if snapshot is not None and (
        snapshot.get("algo") != "mvmcp-concept-v1"
        or not isinstance(snapshot.get("digest"), str)
        or re.fullmatch(r"[0-9a-f]{64}", snapshot["digest"]) is None
    ):
        raise ValueError("Invalid concept snapshot")
    entries = [entry for entry in verified_entries(metadata) if _review_recorded(entry)]
    if (
        not entries
        or row is None
        or row.presence != "present"
        or row.content_digest != etag
    ):
        return {"state": "unavailable"}
    dates = [_parse_stale_after(entry["at"]) for entry in entries]
    latest = max(value for value in dates if isinstance(value, datetime))
    first = datetime.fromisoformat(row.first_seen_at)
    changed = datetime.fromisoformat(row.last_change_observed_at)
    if latest > datetime.now(UTC) or first > latest:
        return {"state": "unavailable"}
    if changed <= latest:
        return {"state": "available", "later_change_observed": False}
    extension = metadata.get("x-mvmcp")
    claims = extension.get("reviews") if isinstance(extension, dict) else None
    if not isinstance(claims, list) or snapshot is None:
        return {"state": "unavailable"}
    matching = [
        claim
        for claim in claims
        if isinstance(claim, dict)
        and any(
            claim.get("by") == entry.get("by")
            and _parse_stale_after(claim.get("at")) == latest
            and _parse_stale_after(entry.get("at")) == latest
            for entry in entries
        )
    ]
    bindings = [claim.get("snapshot") for claim in matching]
    if not bindings or any(
        not isinstance(binding, dict)
        or binding.get("algo") != snapshot["algo"]
        or not isinstance(binding.get("digest"), str)
        or re.fullmatch(r"[0-9a-f]{64}", binding["digest"]) is None
        or binding == snapshot
        for binding in bindings
    ):
        return {"state": "unavailable"}
    return {
        "state": "available",
        "later_change_observed": True,
        "basis": "local_byte_change_and_supplied_snapshot",
    }
