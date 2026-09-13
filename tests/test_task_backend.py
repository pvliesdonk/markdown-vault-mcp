"""Guards for the background-task backend wiring in `make_server`.

`MARKDOWN_VAULT_MCP_TASKS_URL` is documented automatically — the config-surface
generator reads it from `fastmcp_pvl_core.server_config_surface()`, whether or
not anything wires it.  Documentation without wiring is exactly how a feature
ships inert, so these tests assert the wiring itself: that `make_server` calls
`configure_task_backend` on the very server it returns, and that the config
it passes is this project's, not a default-constructed stand-in.

The assertions are on the `TasksExtension` the helper registers and returns —
`configure_task_backend(mcp, ...)` constructs the SEP-2663 tasks extension
with the resolved backend and registers it on `mcp`.  FastMCP exposes no
public read accessor for registered extensions, so a spy wraps the server
module's `configure_task_backend` binding to capture the return value; the
spy calls the real pvl-core helper, so resolution and registration still
run unchanged.  Resolution order (pvl-core ADR 0002 §4.2, as revised for
FastMCP 4) is core's to own; what belongs here is proof the template
reaches it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from fastmcp_pvl_core import ServerConfig, configure_task_backend

import markdown_vault_mcp.server as server_module
from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.server import make_server

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp_tasks import TasksExtension
    from fastmcp_tasks.settings import DocketSettings

_DERIVED_QUEUE_NAME = "markdown-vault-mcp"
"""What pvl-core derives from `MARKDOWN_VAULT_MCP` — lower-cased, `_` to `-`.

Hard-coded rather than imported: `_derive_queue_name` is core-private, and the
point of the assertion is that the rendered project gets *this* name instead of
fastmcp's `"fastmcp"` default.
"""


@pytest.fixture(autouse=True)
def _neutral_docket_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the native escape-hatch vars.

    An operator value in the ambient environment outranks parts of the
    derivation and would make the assertions below depend on the machine.
    Nothing process-global needs restoring any more: the extension is
    per-server state on the `FastMCP` instance each test builds and drops.
    """
    monkeypatch.delenv("FASTMCP_DOCKET_URL", raising=False)
    monkeypatch.delenv("FASTMCP_DOCKET_NAME", raising=False)


@dataclass
class _TaskBackendCapture:
    """What the spy observed: the server passed in, the extension returned."""

    server: FastMCP | None = None
    extension: TasksExtension | None = None

    def settings(self) -> DocketSettings:
        assert self.extension is not None, (
            "make_server never called configure_task_backend (or the tasks "
            "machinery reported unavailable)"
        )
        return self.extension.docket_settings


@pytest.fixture
def task_backend(monkeypatch: pytest.MonkeyPatch) -> _TaskBackendCapture:
    """Spy on `configure_task_backend`, capturing server and extension.

    Calls through to the real helper, so resolution and registration are the
    genuine pvl-core behaviour; the capture only adds visibility (FastMCP
    exposes no public read accessor for registered extensions).
    """
    capture = _TaskBackendCapture()

    def spy(
        mcp: FastMCP, env_prefix: str, config: ServerConfig
    ) -> TasksExtension | None:
        # ``configure_task_backend`` here is the same object server.py
        # imported; calling it directly keeps the spy a pure pass-through.
        extension = configure_task_backend(mcp, env_prefix, config)
        capture.server = mcp
        capture.extension = extension
        return extension

    monkeypatch.setattr(server_module, "configure_task_backend", spy)
    return capture


def test_make_server_configures_the_task_backend(
    task_backend: _TaskBackendCapture,
) -> None:
    """The backend is wired on the server `make_server` returns, with this
    server's queue name — not fastmcp's default.

    The identity assertion matters under FastMCP 4: the extension is
    per-server state, and one registered on any other `FastMCP` instance
    leaves the returned server's task tools without a running extension.
    Two servers sharing one Redis sharing one queue is the other failure
    being prevented.
    """
    server = make_server()
    assert task_backend.server is server
    assert task_backend.settings().name == _DERIVED_QUEUE_NAME


def test_tasks_url_reaches_the_backend(task_backend: _TaskBackendCapture) -> None:
    """An explicit `MARKDOWN_VAULT_MCP_TASKS_URL` selects the Docket backend."""
    config = ProjectConfig(server=ServerConfig(tasks_url="redis://tasks.test:6379/1"))
    make_server(config=config)
    assert task_backend.settings().url == "redis://tasks.test:6379/1"


def test_redis_kv_store_url_is_reused_for_tasks(
    task_backend: _TaskBackendCapture,
) -> None:
    """One `redis://` KV URL configures the task queue as well.

    Proves `make_server` hands over the project's real `ServerConfig` rather
    than a default-constructed one: the derivation can only see `kv_store_url`
    if the config passed through.
    """
    config = ProjectConfig(server=ServerConfig(kv_store_url="redis://kv.test:6379/0"))
    make_server(config=config)
    assert task_backend.settings().url == "redis://kv.test:6379/0"
