"""Background-job records and their key-value store.

Ported from ``fastmcp_pvl_core._jobs.records`` and ``_jobs.store`` 10.2.0
(#2307). Only the shapes this project uses came along: the deferred-job
handle went with ``Jobs.defer``, which nothing here calls.

A ``JobStore`` persists :class:`JobRecord` entries over an ``AsyncKeyValue``
backend (``build_kv_store(config, namespace="jobs")``) with a
``working -> completed | failed | cancelled`` lifecycle:

- :meth:`JobStore.create` registers a ``working`` record whose lifetime is
  the key-value entry's TTL. There is no sweep loop: an expired record, or
  a job orphaned by a process crash, vanishes from the backend, and a later
  poll reports the id as unknown or expired.
- :meth:`JobStore.finish`, :meth:`JobStore.fail` and
  :meth:`JobStore.cancel` settle the record and keep the **remaining** TTL,
  so retention is pinned at creation and never slides.
- :meth:`JobStore.get` looks a record up within one subject scope; an id
  owned by another scope is indistinguishable from an unknown id.
- :meth:`JobStore.collect` marks a settled record as delivered once the
  poller has handed its outcome to the caller: the id leaves the scope's
  index and the record stays readable for :data:`COLLECTED_GRACE_S` at most,
  so a poll repeated after a lost response still answers.

**Per-subject cap.** Each scope keeps an index record of its live job ids:
those still working, and those settled but not yet collected. Only when the
index reaches the cap is it pruned against the backend (dropping ids whose
records have expired) before the create is rejected, so the common path
costs one index read and two writes. A collected job no longer counts
(#2530).

**Correctness boundary.** The key-value facade has no compare-and-set, so
one in-process :class:`asyncio.Lock` serialises every read-modify-write:
exact within the single serving process, and the records themselves survive
a restart when the backend persists.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypedDict
from urllib.parse import quote

if TYPE_CHECKING:
    from collections.abc import Callable

    from key_value.aio.protocols.key_value import AsyncKeyValue

JobStatus = Literal["working", "completed", "failed", "cancelled"]
"""SEP-2663's ``TaskStatus`` set minus ``input_required``: the fallback has
no elicitation channel."""

JOB_POLL_TOOL_NAME = "get_job_result"
"""Name of the polling tool, carried in every job handle's ``poll_with``."""

JOB_RETRY_AFTER_S = 5.0
"""Polling-interval hint carried in job handles and working polls."""

_RECORD_COLLECTION = "records"
"""Key-value collection holding job records, under the ``jobs`` namespace."""

_INDEX_COLLECTION = "index"
"""Key-value collection holding one live-id index record per subject scope."""


class JobHandle(TypedDict):
    """The payload a long-running tool returns when its work is promoted."""

    status: JobStatus
    job_id: str
    poll_with: str
    retry_after_s: float
    message: str


@dataclass(frozen=True)
class JobRecord:
    """One background job as stored and as returned by lookups.

    Attributes:
        job_id: Unguessable URL-safe identifier returned to the caller.
        status: ``"working"`` until the work finishes, then ``"completed"``,
            ``"failed"`` or ``"cancelled"``.
        result: The finished payload on success; ``None`` otherwise. A
            non-mapping return value is wrapped as ``{"value": ...}`` so the
            record stays a JSON object.
        error: The failure message on error; ``None`` otherwise.
        created_at: Wall-clock epoch when the record was created (promotion
            time for deadline-promoted work).
        started_at: Wall-clock epoch when execution began; for promoted work
            this precedes ``created_at`` by the inline window already spent.
        finished_at: Wall-clock epoch when it settled; ``None`` while working.
    """

    job_id: str
    status: JobStatus
    result: dict[str, Any] | None
    error: str | None
    created_at: float
    started_at: float
    finished_at: float | None


class JobNotFoundError(LookupError):
    """No job with this id is visible to the calling subject.

    Raised for ids that never existed, records that expired, and ids owned by
    a different subject. The three cases are deliberately indistinguishable,
    so a caller cannot probe other subjects' job ids.
    """


class JobLimitExceededError(RuntimeError):
    """The calling subject already has the maximum number of live jobs."""


COLLECTED_GRACE_S = 300.0
"""Seconds a collected record stays readable, so a repeated poll still answers."""


def _scope_key(scope: str, job_id: str = "") -> str:
    """Compose the key-value key for *scope*, plus an optional job id.

    The scope is URL-quoted so a subject containing the delimiter cannot
    collide with, or forge, another scope's keys.
    """
    encoded = quote(scope, safe="")
    return f"{encoded}:{job_id}" if job_id else encoded


def _record_to_mapping(record: JobRecord) -> dict[str, Any]:
    return {
        "job_id": record.job_id,
        "status": record.status,
        "result": record.result,
        "error": record.error,
        "created_at": record.created_at,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
    }


def _record_from_mapping(raw: dict[str, Any]) -> JobRecord:
    return JobRecord(
        job_id=str(raw["job_id"]),
        status=raw["status"],
        result=raw["result"],
        error=raw["error"],
        created_at=float(raw["created_at"]),
        started_at=float(raw["started_at"]),
        finished_at=(
            float(raw["finished_at"]) if raw["finished_at"] is not None else None
        ),
    )


class JobStore:
    """Subject-scoped, TTL-bounded store of background-job records."""

    def __init__(
        self,
        kv: AsyncKeyValue,
        *,
        result_ttl_s: float,
        max_per_subject: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Wire the store over a namespaced key-value backend.

        Args:
            kv: Namespaced ``AsyncKeyValue``, from
                ``build_kv_store(config, namespace="jobs")``.
            result_ttl_s: Record lifetime in seconds, from creation.
            max_per_subject: Live-record cap per subject scope.
            clock: Wall-clock source returning epoch seconds, injected only
                for deterministic tests. Wall clock, not monotonic, so
                timestamps persisted in the backend stay meaningful across a
                restart.
        """
        self._kv = kv
        self._result_ttl_s = float(result_ttl_s)
        self._max_per_subject = int(max_per_subject)
        self._clock = clock
        self._lock = asyncio.Lock()

    async def create(self, scope: str, *, started_at: float | None = None) -> JobRecord:
        """Register a new ``working`` record under *scope*.

        Args:
            scope: The owning subject scope.
            started_at: Epoch when execution actually began; defaults to now.
                Deadline-promoted work passes the earlier call start, so a
                poll's ``running_for_s`` covers the inline window.

        Returns:
            The stored record; its ``job_id`` is the caller's handle.

        Raises:
            JobLimitExceededError: If *scope* already holds
                ``max_per_subject`` live records after pruning expired ids.
        """
        now = self._clock()
        record = JobRecord(
            job_id=secrets.token_urlsafe(16),
            status="working",
            result=None,
            error=None,
            created_at=now,
            started_at=started_at if started_at is not None else now,
            finished_at=None,
        )
        async with self._lock:
            index_key = _scope_key(scope)
            raw_index = await self._kv.get(index_key, collection=_INDEX_COLLECTION)
            ids: list[str] = list(raw_index["ids"]) if raw_index else []
            if len(ids) >= self._max_per_subject:
                ids = await self._prune(scope, ids)
                if len(ids) >= self._max_per_subject:
                    raise JobLimitExceededError(
                        f"subject already has {len(ids)} live background "
                        f"jobs (cap {self._max_per_subject}); retry after "
                        "existing jobs are collected or expire."
                    )
            ids.append(record.job_id)
            await self._kv.put(
                _scope_key(scope, record.job_id),
                _record_to_mapping(record),
                collection=_RECORD_COLLECTION,
                ttl=self._result_ttl_s,
            )
            await self._kv.put(
                index_key,
                {"ids": ids},
                collection=_INDEX_COLLECTION,
                ttl=self._result_ttl_s,
            )
        return record

    async def _prune(self, scope: str, ids: list[str]) -> list[str]:
        """Drop ids whose records have expired; called under the store lock."""
        live: list[str] = []
        for job_id in ids:
            raw = await self._kv.get(
                _scope_key(scope, job_id), collection=_RECORD_COLLECTION
            )
            if raw is not None:
                live.append(job_id)
        return live

    async def finish(self, scope: str, job_id: str, result: dict[str, Any]) -> None:
        """Settle *job_id* as ``completed`` with its result payload.

        A record that expired mid-run is left alone: there is nothing to
        settle, and the poller already reports the id as unknown.
        """
        await self._settle(scope, job_id, status="completed", result=result, error=None)

    async def fail(self, scope: str, job_id: str, error: str) -> None:
        """Settle *job_id* as ``failed`` with the failure message."""
        await self._settle(scope, job_id, status="failed", result=None, error=error)

    async def cancel(self, scope: str, job_id: str) -> None:
        """Settle *job_id* as ``cancelled``; its task was cancelled."""
        await self._settle(scope, job_id, status="cancelled", result=None, error=None)

    async def _settle(
        self,
        scope: str,
        job_id: str,
        *,
        status: str,
        result: dict[str, Any] | None,
        error: str | None,
    ) -> None:
        key = _scope_key(scope, job_id)
        async with self._lock:
            raw, remaining = await self._kv.ttl(key, collection=_RECORD_COLLECTION)
            # Fail closed on a live record without a TTL as well: create()
            # always writes one, so such a record was not minted here, and
            # settling it would mint a full retention window, which the
            # remaining-TTL rule exists to prevent.
            if raw is None or remaining is None:
                return
            raw = dict(raw)
            raw["status"] = status
            raw["result"] = result
            raw["error"] = error
            raw["finished_at"] = self._clock()
            # Keep the REMAINING TTL: retention is pinned at creation and never
            # slides, so a record cannot live forever by being settled again.
            await self._kv.put(
                key, raw, collection=_RECORD_COLLECTION, ttl=float(remaining)
            )

    async def collect(self, scope: str, job_id: str) -> None:
        """Release a settled job's slot under the cap once its outcome was read.

        Drops *job_id* from *scope*'s index and shortens the record's lifetime
        to :data:`COLLECTED_GRACE_S`, never lengthening it. A working, expired
        or unknown record is left alone, so calling this again is harmless.

        Args:
            scope: The owning subject scope.
            job_id: The id whose terminal outcome the caller has received.
        """
        key = _scope_key(scope, job_id)
        async with self._lock:
            raw, remaining = await self._kv.ttl(key, collection=_RECORD_COLLECTION)
            if raw is None or remaining is None or raw["status"] == "working":
                return
            if remaining > COLLECTED_GRACE_S:
                await self._kv.put(
                    key, raw, collection=_RECORD_COLLECTION, ttl=COLLECTED_GRACE_S
                )
            await self._drop_from_index(scope, job_id)

    async def _drop_from_index(self, scope: str, job_id: str) -> None:
        """Remove *job_id* from *scope*'s index; called under the store lock."""
        index_key = _scope_key(scope)
        raw_index, remaining = await self._kv.ttl(
            index_key, collection=_INDEX_COLLECTION
        )
        if raw_index is None or job_id not in raw_index["ids"]:
            return
        ids = [i for i in raw_index["ids"] if i != job_id]
        if not ids:
            await self._kv.delete(index_key, collection=_INDEX_COLLECTION)
            return
        await self._kv.put(
            index_key,
            {"ids": ids},
            collection=_INDEX_COLLECTION,
            ttl=remaining if remaining is not None else self._result_ttl_s,
        )

    async def get(self, scope: str, job_id: str) -> JobRecord:
        """Return the record for *job_id* as visible to *scope*.

        Raises:
            JobNotFoundError: Unknown id, expired record, or an id owned by
                another scope; deliberately indistinguishable.
        """
        raw = await self._kv.get(
            _scope_key(scope, job_id), collection=_RECORD_COLLECTION
        )
        if raw is None:
            raise JobNotFoundError(
                f"no job {job_id!r} for this subject — the id is unknown, "
                "expired, or belongs to another caller."
            )
        return _record_from_mapping(dict(raw))
