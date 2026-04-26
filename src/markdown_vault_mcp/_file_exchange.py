"""Module-level FileExchange singleton and periodic sweep timer.

The MCP File Exchange v0.3 protocol (spec lives in
``fastmcp-pvl-core``) lets servers share files through a shared
volume rather than the LLM context. This module owns one
:class:`fastmcp_pvl_core.FileExchange` instance per process so tool
bodies and the lifespan teardown can reach the same configured
runtime.

The producer side also needs a periodic sweep so files written to
``$MCP_EXCHANGE_DIR/{namespace}/`` don't pile up after their TTL
expires. :func:`start_sweep_timer` runs a daemon
:class:`threading.Timer` loop until :func:`stop_sweep_timer` is
called from the lifespan's finally block.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp_pvl_core import FileExchange

logger = logging.getLogger(__name__)


#: Default sweep cadence — matches spec §7's "TTL is the producer's promise".
#: 5 min keeps the worst-case orphan window short without measurable
#: disk-IO cost; operators can override per server.
DEFAULT_SWEEP_INTERVAL_S = 300.0


_fx: FileExchange | None = None
_sweep_timer: threading.Timer | None = None
_sweep_lock = threading.Lock()
#: Set by :func:`stop_sweep_timer` so a tick already in flight skips
#: re-arming. Cancelling the ``threading.Timer`` only stops a tick that
#: hasn't fired yet — without this flag, ``_tick`` running concurrently
#: with ``stop_sweep_timer`` would re-install a fresh timer the cancel
#: never sees (gemini-code-assist flagged this race).
_sweep_stopped = threading.Event()
_sweep_stopped.set()  # default: not running


def set_file_exchange(fx: FileExchange | None) -> None:
    """Register *fx* as the process-wide singleton (or clear with ``None``).

    Called once from :func:`markdown_vault_mcp.server.make_server` per
    server construction.  Tests pass ``None`` to exercise the
    "not configured" path.
    """
    global _fx
    _fx = fx


def get_file_exchange() -> FileExchange | None:
    """Return the configured :class:`FileExchange`, or ``None`` if disabled.

    Tools that produce ``file_ref`` blocks gate their augmentation on a
    non-``None`` (and ``is_configured``) return; consumers can call
    :meth:`FileExchange.read_exchange_uri` on a configured instance.
    """
    return _fx


def start_sweep_timer(
    fx: FileExchange,
    interval_s: float = DEFAULT_SWEEP_INTERVAL_S,
) -> None:
    """Start (or restart) the periodic sweep timer.

    No-op when ``fx.is_configured`` is ``False`` — there is nothing to
    sweep without a base directory.  Always cancels any previously
    started timer first so repeated calls don't stack.

    Args:
        fx: The configured :class:`FileExchange` instance to sweep.
        interval_s: Seconds between sweeps.  Defaults to
            :data:`DEFAULT_SWEEP_INTERVAL_S`.
    """
    if not fx.is_configured:
        return
    stop_sweep_timer()
    _sweep_stopped.clear()
    _arm(fx, interval_s)


def stop_sweep_timer() -> None:
    """Cancel the periodic sweep timer if one is running.

    Safe to call multiple times; safe to call when no timer was ever
    started.  Called from the lifespan's ``finally`` block to release
    the daemon thread on shutdown.

    Sets the stop event *before* cancelling so a tick currently inside
    ``fx.sweep()`` reads ``_sweep_stopped.is_set() == True`` when it
    returns and skips re-arming.  Cancelling alone is racy because a
    timer that has already fired can't be cancelled — the only handle
    on an in-flight tick is the shared event.
    """
    global _sweep_timer
    _sweep_stopped.set()
    with _sweep_lock:
        if _sweep_timer is not None:
            _sweep_timer.cancel()
            _sweep_timer = None


def _arm(fx: FileExchange, interval_s: float) -> None:
    """Install one daemon timer that re-arms after each tick.

    The closure rebuilds itself each tick so cancellation between
    ticks always wins — :func:`stop_sweep_timer` clearing the
    singleton prevents the next ``Timer`` from starting.
    """

    def _tick() -> None:
        # Bail before doing any work if a stop arrived while this tick
        # was waiting on the timer queue.
        if _sweep_stopped.is_set():
            return
        try:
            evicted = fx.sweep()
            if evicted:
                logger.debug("file_exchange_sweep_tick evicted=%d", evicted)
        except Exception:
            # Sweep failures must not break the loop — the next tick
            # will retry, and spec §7 producer cleanup is best-effort.
            logger.exception("file_exchange_sweep failed")
        # Re-check the flag after the (potentially long) sweep call;
        # otherwise a stop that arrived mid-sweep would be ignored and
        # we'd re-install a fresh timer the cancel never sees.
        if _sweep_stopped.is_set():
            return
        _arm(fx, interval_s)

    global _sweep_timer
    timer = threading.Timer(interval_s, _tick)
    timer.daemon = True
    with _sweep_lock:
        # Final stop check INSIDE the lock so a `stop_sweep_timer` that
        # interleaves with this re-arm wins deterministically.
        # `stop_sweep_timer` sets the event and clears `_sweep_timer`
        # under the same lock, so if it ran first we observe
        # ``_sweep_stopped.is_set()`` here and drop the timer instead
        # of installing it (would otherwise leak an orphan daemon).
        if _sweep_stopped.is_set():
            return
        _sweep_timer = timer
    timer.start()
