"""Domain-local quieting of the httpx/httpcore per-request loggers (#792).

``httpx``/``httpcore`` emit a per-request ``INFO`` line ("HTTP Request: POST …
200 OK") that floods normal embedding builds. FastMCP's ``configure_logging_from_env``
only governs ``uvicorn.access`` / ``mcp.server.lowlevel.server`` (per the logging
standard); these HTTP-client deps are domain-owned, so MVM quiets them itself.

Kept out of the template-owned ``cli.py`` ``_root`` callback and ``server.py``
``make_server`` scaffold (both must stay byte-identical to the template skeleton);
called instead from the MVM-owned command bodies and the ``make_server``
``DOMAIN-WIRING`` sentinel — every path that may construct an embedding provider.
"""

from __future__ import annotations

import logging


def quiet_http_loggers() -> None:
    """Pin httpx/httpcore to WARNING unless the root level is DEBUG.

    Reads the root level that the CLI ``_root`` callback / ``configure_logging_from_env``
    already set: DEBUG (``-v``) → ``NOTSET`` (follow root, visible), otherwise
    ``WARNING`` (quiet). Idempotent — safe to call from multiple entry points.
    """
    http_level = (
        logging.NOTSET
        if logging.getLogger().getEffectiveLevel() <= logging.DEBUG
        else logging.WARNING
    )
    logging.getLogger("httpx").setLevel(http_level)
    logging.getLogger("httpcore").setLevel(http_level)
