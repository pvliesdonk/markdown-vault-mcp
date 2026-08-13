"""Guards for the background-task backend wiring in `make_server`.

`MARKDOWN_VAULT_MCP_TASKS_URL` is documented automatically — the config-surface
generator reads it from `fastmcp_pvl_core.server_config_surface()`, whether or
not anything wires it.  Documentation without wiring is exactly how a feature
ships inert, so these tests assert the wiring itself: that `make_server` calls
`configure_task_backend`, and that the config it passes is this project's, not
a default-constructed stand-in.

The assertions are on `fastmcp.settings.docket`, which is where
`configure_task_backend` writes.  Resolution order (pvl-core ADR 0002 §4.2)
is core's to own; what belongs here is proof the template reaches it.
"""

from __future__ import annotations

import fastmcp
import pytest
from fastmcp_pvl_core import ServerConfig

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.server import make_server

_DERIVED_QUEUE_NAME = "markdown-vault-mcp"
"""What pvl-core derives from `MARKDOWN_VAULT_MCP` — lower-cased, `_` to `-`.

Hard-coded rather than imported: `_derive_queue_name` is core-private, and the
point of the assertion is that the rendered project gets *this* name instead of
fastmcp's global `"fastmcp"` default.
"""


@pytest.fixture(autouse=True)
def _isolate_docket_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo the process-global writes `configure_task_backend` makes.

    fastmcp offers no constructor-level injection point for Docket, so the
    helper mutates `fastmcp.settings.docket` in place.  Setting each attribute
    to its current value registers monkeypatch's restore hook without changing
    anything, so these tests cannot leak a Redis URL or a queue name into
    whatever runs after them.  The native escape-hatch vars are cleared too:
    an operator value in the ambient environment outranks parts of the
    derivation and would make the assertions below depend on the machine.
    """
    monkeypatch.setattr(fastmcp.settings.docket, "url", fastmcp.settings.docket.url)
    monkeypatch.setattr(fastmcp.settings.docket, "name", fastmcp.settings.docket.name)
    monkeypatch.delenv("FASTMCP_DOCKET_URL", raising=False)
    monkeypatch.delenv("FASTMCP_DOCKET_NAME", raising=False)


def test_make_server_configures_the_task_backend() -> None:
    """The queue name is this server's, not fastmcp's global default.

    This is the whole wiring assertion: nothing but `configure_task_backend`
    renames the queue, so a `make_server` that stopped calling it leaves the
    default `"fastmcp"` here and fails this test.  Two servers sharing one
    Redis sharing one queue is the failure being prevented.
    """
    make_server()
    assert fastmcp.settings.docket.name == _DERIVED_QUEUE_NAME


def test_tasks_url_reaches_the_backend() -> None:
    """An explicit `MARKDOWN_VAULT_MCP_TASKS_URL` selects the Docket backend."""
    config = ProjectConfig(server=ServerConfig(tasks_url="redis://tasks.test:6379/1"))
    make_server(config=config)
    assert fastmcp.settings.docket.url == "redis://tasks.test:6379/1"


def test_redis_kv_store_url_is_reused_for_tasks() -> None:
    """One `redis://` KV URL configures the task queue as well.

    Proves `make_server` hands over the project's real `ServerConfig` rather
    than a default-constructed one: the derivation can only see `kv_store_url`
    if the config passed through.
    """
    config = ProjectConfig(server=ServerConfig(kv_store_url="redis://kv.test:6379/0"))
    make_server(config=config)
    assert fastmcp.settings.docket.url == "redis://kv.test:6379/0"
