"""Unexpected resource errors reach the client without their text (template-owned).

``make_server`` sets ``mask_error_details=True``: an exception a resource
raises, other than a deliberate ``ResourceError``, can carry upstream URLs,
paths or credentials, so the client gets FastMCP's generic message and the
server log keeps the traceback (pvliesdonk/fastmcp-server-template#646).
Prompts are not checked here: FastMCP does not yet mask them
(PrefectHQ/fastmcp#6273).
"""

from __future__ import annotations

import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ResourceError

from markdown_vault_mcp.server import make_server

_LEAKED_DETAIL = "postgres://svc:hunter2@db/internal"


@pytest.fixture(autouse=True)
def _preset_contract_env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Set every variable the project's ``config_contract_env`` returns.

    The fixture in the project's ``tests/conftest.py`` supplies values for
    the variables its ``from_env`` reads with ``env(..., required=True)``.
    Resolved via ``getfixturevalue`` so a ``conftest.py`` that predates the
    fixture keeps passing with nothing preset.  Repeated verbatim in every
    template-owned test that builds from the environment rather than
    imported: a sibling import only resolves when ``tests/`` is not a package.
    """
    try:
        env = request.getfixturevalue("config_contract_env")
    except pytest.FixtureLookupError:
        return
    for key, value in dict(env).items():
        monkeypatch.setenv(key, value)


def _read_error(uri: str) -> str:
    server = make_server()

    @server.resource("masking-test://fault")
    def fault() -> str:
        raise RuntimeError(f"connect to {_LEAKED_DETAIL} failed")

    @server.resource("masking-test://outcome")
    def outcome() -> str:
        raise ResourceError("Note not found: a.md")

    async def _read() -> str:
        async with Client(server) as client:
            with pytest.raises(Exception) as excinfo:
                await client.read_resource(uri)
        return str(excinfo.value)

    return asyncio.run(_read())


def test_unexpected_resource_error_is_masked() -> None:
    assert _LEAKED_DETAIL not in _read_error("masking-test://fault")


def test_resource_error_text_reaches_the_client() -> None:
    assert "Note not found: a.md" in _read_error("masking-test://outcome")
