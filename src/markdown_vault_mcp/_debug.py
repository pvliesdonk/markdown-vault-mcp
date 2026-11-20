"""Optional remote-debugger entrypoint helper.

Ported from ``fastmcp_pvl_core._debug`` 10.2.0 (#2335).

Call :func:`maybe_start_debugpy` early in ``main()`` (after logging is
configured, before argument parsing) with the same ``env_prefix`` the rest of
the server uses for its config: it is a no-op unless ``{PREFIX}_DEBUG_PORT``
is set, so it is safe to call unconditionally.

Environment contract:

* ``{PREFIX}_DEBUG_PORT`` — TCP port to listen on. Unset, blank, or any
  value that parses to ``0`` disables the helper silently. Non-numeric
  or out-of-``1..65535`` values log a ``WARNING`` and the helper
  returns without raising. Surrounding whitespace is ignored.
* ``{PREFIX}_DEBUG_WAIT`` — when truthy (``1``/``true``/``yes``/``on``,
  case-insensitive — see ``parse_bool`` in ``_env.py``), block startup
  until the IDE attaches. Default is non-blocking so missing-attach
  doesn't deadlock production containers that were accidentally built
  with ``DEBUG=true``.

The ``debugpy`` package is imported lazily — install it via
``pip install 'markdown-vault-mcp[debug]'`` (quote the brackets in zsh)
or ``uv add debugpy`` only on images that actually need the listener.
A failed import logs a ``WARNING`` and continues, so the helper is safe
to call unconditionally in the default scaffold.

.. warning::

   The listener binds ``0.0.0.0`` so the debugger is reachable from
   outside the container — this is intentional for the developer
   workflow, but **debugpy's DAP protocol is unauthenticated**: any
   peer that can reach the port has arbitrary code execution as the
   server process. Only enable ``{PREFIX}_DEBUG_PORT`` in environments
   where the port is reachable solely from a trusted developer
   workstation (e.g. ``kubectl port-forward``,
   ``docker run -p 127.0.0.1:5678:5678``, an SSH tunnel). Never publish
   the debug port on a public network.
"""

from __future__ import annotations

import logging

from markdown_vault_mcp._env import env, env_int, parse_bool

logger = logging.getLogger(__name__)

# Module-level latch — debugpy.listen() raises if called twice on the
# same process, so the helper is idempotent across repeated invocations
# (e.g. typer subcommands sharing a root callback). Not thread-safe;
# the helper is meant to be called from main() before any worker
# threads spawn. A racing second caller would hit debugpy's own
# "already listening" error, which the broad except below absorbs into
# a WARNING — benign, but worth knowing.
_started = False


def maybe_start_debugpy(env_prefix: str) -> None:
    """Start a debugpy listener if ``{env_prefix}_DEBUG_PORT`` is set.

    Reads ``{env_prefix}_DEBUG_PORT`` from the environment. Unset,
    blank, or any value that parses to ``0`` is a silent no-op.
    Non-numeric or out-of-``1..65535`` values log a ``WARNING`` so
    misconfiguration is visible. Otherwise imports :mod:`debugpy`
    lazily and binds ``("0.0.0.0", port)``.

    A failed import logs a ``WARNING`` pointing at the ``debug`` extra
    and returns. A failure inside :func:`debugpy.listen` (port in use,
    permission denied, debugpy-internal error) likewise logs a
    ``WARNING`` and returns — a debug-port problem must never crash
    the server.

    When ``{env_prefix}_DEBUG_WAIT`` is truthy the helper blocks until
    the IDE attaches (``debugpy.wait_for_client()``); otherwise startup
    continues immediately. A failure inside ``wait_for_client`` (e.g.
    debugpy-internal error, transport hiccup) likewise logs a
    ``WARNING`` and returns — the listener is still up, so the IDE can
    still attach manually. ``KeyboardInterrupt`` propagates so an
    operator-initiated Ctrl-C still aborts the process.

    Idempotent across repeated calls in the same process. Not
    thread-safe — call from ``main()`` before spawning workers. After
    a ``wait_for_client`` failure a re-call short-circuits silently:
    the listener is up, there is nothing more to do.

    Args:
        env_prefix: The same per-app prefix the server uses for its
            other env vars (e.g. ``"MY_APP"`` produces
            ``MY_APP_DEBUG_PORT`` / ``MY_APP_DEBUG_WAIT``). Trailing
            underscore optional, matching ``_env.env``'s convention.

    Security note: the listener binds ``0.0.0.0`` and debugpy's DAP
    protocol is unauthenticated. Only expose the port to a trusted
    developer workstation (port-forward, loopback bind, SSH tunnel).
    See the module docstring for details.
    """
    global _started
    if _started:
        return

    port_var = f"{env_prefix.rstrip('_')}_DEBUG_PORT"
    # Soft-mode parse + validate: a non-numeric, negative, or >65535 value
    # logs a WARNING naming the var and returns None; unset/blank also returns
    # None. ``minimum=0`` keeps every documented "parses to 0" disable form
    # (``0``, ``00``, ``-0``, ``+0``, whitespace-wrapped) valid and unwarned,
    # so it falls through to the silent no-op below alongside the None cases.
    port = env_int(env_prefix, "DEBUG_PORT", minimum=0, maximum=65535)
    if port is None or port == 0:
        return

    try:
        # ``import-not-found`` covers environments without the optional
        # ``[debug]`` extra; ``unused-ignore`` covers CI / dev installs
        # that *do* have debugpy and would otherwise flag the comment.
        import debugpy  # type: ignore[import-not-found, unused-ignore]
    except ImportError:
        logger.warning(
            "debugpy_not_installed env_var=%s port=%d action=%s",
            port_var,
            port,
            "pip install 'markdown-vault-mcp[debug]' or uv add debugpy",
        )
        return

    try:
        # Deliberate debugpy bind, see the docstring; S104 is gate-only.
        debugpy.listen(("0.0.0.0", port))  # noqa: S104, RUF100
    except Exception as exc:  # broad: listener bring-up must not crash startup
        logger.warning(
            "debugpy_listen_failed port=%d error=%s consequence=%s",
            port,
            exc,
            "continuing without remote debugger",
        )
        return

    _started = True
    logger.info("debugpy_listening address=0.0.0.0 port=%d", port)

    if parse_bool(env(env_prefix, "DEBUG_WAIT", "")):
        wait_var = f"{env_prefix.rstrip('_')}_DEBUG_WAIT"
        logger.info("debugpy_wait_enabled env_var=%s blocking=true", wait_var)
        try:
            debugpy.wait_for_client()
        except KeyboardInterrupt:
            # Operator hit Ctrl-C while waiting — propagate so the
            # process exits as the user expects. The latch stays set
            # because listen() already succeeded; a re-caller would
            # short-circuit silently (listener is up, nothing to do).
            raise
        except Exception as exc:  # broad: debugger bring-up must not crash startup
            logger.warning(
                "debugpy_wait_for_client_failed port=%d error=%s consequence=%s",
                port,
                exc,
                "listener still up on 0.0.0.0; IDE can attach manually",
            )
            return
        logger.info("debugpy_attached")
