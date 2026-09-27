"""No operator or developer vocabulary reaches the model-facing surface (#1599).

The ``writing-model-facing-text`` skill's first gate: every sentence a client
sends the model must be something the model can act on while it chooses or
fills in a call.  An environment variable, a Sphinx role, an issue number, a
git command-line flag, a reST literal or an exception class fails it on sight:
the model cannot set, open, pass or catch any of them.  Those are the leaks a
pattern can find, so this test finds them everywhere the client lists text:
instructions, tool descriptions, every input-schema description, resources,
resource templates, prompts and prompt arguments.

The template-owned ``test_model_facing_text.py`` checks shape (sections,
length, the prompt schema note) on a default deployment.  This file builds the
largest surface instead, so text registered only when git, OKF writes or
summarization is configured is checked too.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client, FastMCP

from tests.server_factory import make_server

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Iterator
    from pathlib import Path

# Each pattern names one vocabulary the model cannot act on.
_LEAKS = {
    "environment variable or setting name": re.compile(
        r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b"
    ),
    "Sphinx role": re.compile(r":(?:meth|func|class|mod|attr|exc|data):"),
    "reST literal": re.compile(r"``"),
    "issue or PR reference": re.compile(r"(?<![\w&])#\d+\b"),
    "command-line flag": re.compile(r"(?<![\w-])--[a-z][\w-]*"),
    "exception class": re.compile(r"\b[A-Z]\w*(?:Error|Exception)\b"),
}


@pytest.fixture
def maximal_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    """Every optional feature on, so every conditional description is listed."""
    from markdown_vault_mcp import server as server_module

    @asynccontextmanager
    async def _skip_lifespan(_: FastMCP) -> AsyncGenerator[dict[str, Any]]:
        yield {}

    monkeypatch.setattr(server_module, "server_lifespan", _skip_lifespan)
    (tmp_path / "index.md").write_text(
        "---\nokf_version: 0.2\n---\n# Index\n", encoding="utf-8"
    )
    env = {
        "SOURCE_DIR": str(tmp_path),
        "READ_ONLY": "false",
        "BASE_URL": "https://vault.example",
        "SUMMARIZE_OPENAI_API_KEY": "sk-test",
        "OKF_MODE": "on",
        "OKF_WRITE": "true",
        "OKF_VERIFY": "elicit",
        "GIT_REPO_URL": "https://github.com/example/vault.git",
        "KV_STORE_URL": "memory://",
    }
    for key, value in env.items():
        monkeypatch.setenv(f"MARKDOWN_VAULT_MCP_{key}", value)
    return make_server(transport="http")


def _schema_descriptions(schema: Any, where: str) -> Iterator[tuple[str, str]]:
    """Every ``description`` in a JSON schema, with the path that holds it."""
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "description" and isinstance(value, str):
                yield where, value
            else:
                yield from _schema_descriptions(value, f"{where}.{key}")
    elif isinstance(schema, list):
        for index, item in enumerate(schema):
            yield from _schema_descriptions(item, f"{where}[{index}]")


def _app_only(tool: Any) -> bool:
    """An MCP Apps backend the host routes and never shows the model."""
    ui = (tool.meta or {}).get("ui")
    return isinstance(ui, dict) and ui.get("visibility") == ["app"]


async def _surface(server: FastMCP) -> list[tuple[str, str]]:
    """``(where, text)`` for every model-facing text the client lists."""
    found = [("instructions", server.instructions or "")]
    async with Client(server) as client:
        for tool in await client.list_tools():
            if _app_only(tool):
                continue
            found.append((f"tool {tool.name}", tool.description or ""))
            found += _schema_descriptions(tool.input_schema, f"tool {tool.name}")
        for resource in await client.list_resources():
            found.append((f"resource {resource.uri}", resource.description or ""))
        for template in await client.list_resource_templates():
            found.append(
                (f"template {template.uri_template}", template.description or "")
            )
        for prompt in await client.list_prompts():
            found.append((f"prompt {prompt.name}", prompt.description or ""))
            found += [
                (f"prompt {prompt.name}.{arg.name}", arg.description or "")
                for arg in prompt.arguments or []
            ]
    return found


@pytest.mark.asyncio
async def test_surface_carries_no_operator_or_developer_vocabulary(
    maximal_server: FastMCP,
) -> None:
    """Nothing the model cannot set, open, pass or catch reaches it."""
    leaks = sorted(
        {
            f"{where}: {kind} {match.group(0)!r}"
            for where, text in await _surface(maximal_server)
            for kind, pattern in _LEAKS.items()
            for match in pattern.finditer(text)
        }
    )
    assert not leaks, (
        "model-facing text names something the model cannot act on; move it "
        "to the operator docs, the error text or a # comment "
        "(writing-model-facing-text skill):\n" + "\n".join(leaks)
    )
