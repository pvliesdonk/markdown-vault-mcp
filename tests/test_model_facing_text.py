"""Model-facing text ships clean and fits the client that cuts it (template-owned).

Every docstring and ``description=`` this server registers reaches a model as
a hint at the moment it chooses a call.  Three things go wrong silently:
FastMCP ships a tool or prompt docstring verbatim, ``Returns:`` and
``Raises:`` included, whenever it finds no ``Args:`` entry (parameterless
tools and argumentless prompts; FastMCP issue 4952) and never parses a
resource docstring at all; under postponed annotations it appends a
JSON-schema sentence to every ``str`` prompt argument; and Claude Code
truncates each tool description and each server's instructions at 2,048
characters and keeps the start.  ``docs/design/reference/mcp-model-facing-text.md``
records all three; the ``writing-model-facing-text`` skill says what to
write instead.

Every test takes the module's own ``server`` fixture: it scrubs the
``MARKDOWN_VAULT_MCP_*`` environment and builds the server synchronously, per
test, so the asserted text is the wire text of a default deployment and not
of whatever the shell carries, and the file does not depend on a fixture
from the project-owned ``conftest.py``.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from fastmcp import Client
from fastmcp_pvl_core import utf16_code_units

from markdown_vault_mcp.server import make_server

if TYPE_CHECKING:
    from fastmcp import FastMCP

# Claude Code's default per-description and per-instructions cut, in
# JavaScript string units (UTF-16 code units).
CLAUDE_CODE_CUT = 2_048

# A Google-style docstring section heading on its own line, from the set
# FastMCP strips when it parses a docstring.  One of these in a wire
# description means the docstring was not parsed and developer sections
# reached the model.
_SECTION_RE = re.compile(
    r"^\s*(Args|Arguments|Parameters|Returns|Yields|Raises):\s*$", re.MULTILINE
)

# FastMCP's schema sentence for a ``{"type":"string"}`` argument: noise for a
# plain ``str``, and only ever emitted for one under postponed annotations.
_STRING_SCHEMA_NOTE = 'following JSON schema: {"type":"string"}'


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    """A default deployment's server, built before any event loop starts.

    ``make_server`` finalises the instructions synchronously and refuses to
    run inside an event loop, so the client probes below run under
    ``asyncio.run`` and the construction does not.
    """
    for key in list(os.environ):
        if key.startswith("MARKDOWN_VAULT_MCP_"):
            monkeypatch.delenv(key, raising=False)
    return make_server()


@dataclass(frozen=True)
class Described:
    kind: str
    name: str
    description: str


def _surface(server: FastMCP) -> list[Described]:
    """Every listed component with the description a client receives."""

    async def _list() -> list[Described]:
        async with Client(server) as client:
            found = [
                Described("tool", t.name, t.description or "")
                for t in await client.list_tools()
            ]
            found += [
                Described("resource", str(r.uri), r.description or "")
                for r in await client.list_resources()
            ]
            found += [
                Described("template", str(r.uri_template), r.description or "")
                for r in await client.list_resource_templates()
            ]
            found += [
                Described("prompt", p.name, p.description or "")
                for p in await client.list_prompts()
            ]
            return found

    return asyncio.run(_list())


def _prompt_arguments(server: FastMCP) -> list[tuple[str, str, str]]:
    """``(prompt, argument, description)`` for every listed prompt argument."""

    async def _list() -> list[tuple[str, str, str]]:
        async with Client(server) as client:
            return [
                (p.name, a.name, a.description or "")
                for p in await client.list_prompts()
                for a in p.arguments or []
            ]

    return asyncio.run(_list())


def test_descriptions_carry_no_docstring_sections(server: FastMCP) -> None:
    """No ``Args:``/``Returns:``/``Raises:`` heading reaches the wire."""
    leaked = [
        f"{d.kind} {d.name}: {m.group(1)}:"
        for d in _surface(server)
        for m in _SECTION_RE.finditer(d.description)
    ]
    assert not leaked, (
        "docstring sections shipped as model-facing text (a tool or prompt "
        "docstring is parsed only when it has an Args: entry, a resource "
        "docstring never; pass description= or keep the docstring to one "
        f"line): {leaked}"
    )


def test_every_tool_has_a_description(server: FastMCP) -> None:
    """A tool the model cannot tell apart from its siblings is not registered."""
    missing = [
        d.name
        for d in _surface(server)
        if d.kind == "tool" and not d.description.strip()
    ]
    assert not missing, f"tools without a description: {missing}"


def test_prompt_arguments_carry_no_string_schema_note(server: FastMCP) -> None:
    """A plain ``str`` prompt argument ships its own description, nothing more."""
    noisy = [
        f"{prompt}.{arg}"
        for prompt, arg, description in _prompt_arguments(server)
        if _STRING_SCHEMA_NOTE in description
    ]
    assert not noisy, (
        "string prompt arguments carry FastMCP's JSON-schema sentence; drop "
        f"`from __future__ import annotations` from the prompts module: {noisy}"
    )


def test_descriptions_fit_the_claude_code_cut(server: FastMCP) -> None:
    """Each tool description and the instructions stay under 2,048 units."""
    over = [
        f"{d.kind} {d.name}: {utf16_code_units(d.description)} units"
        for d in _surface(server)
        if d.kind == "tool" and utf16_code_units(d.description) > CLAUDE_CODE_CUT
    ]
    instructions = server.instructions or ""
    if utf16_code_units(instructions) > CLAUDE_CODE_CUT:
        over.append(f"instructions: {utf16_code_units(instructions)} units")
    assert not over, (
        f"Claude Code truncates these at {CLAUDE_CODE_CUT} characters and keeps "
        f"the start; move what the model does not need at call time: {over}"
    )
