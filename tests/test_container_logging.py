"""Contract tests: the shipped deployments pin no log format.

Neither a container's stderr nor journald is a terminal. pvl-core (>= 8)
therefore renders one JSON object per record in both places with nothing
configured: ``MARKDOWN_VAULT_MCP_LOG_FORMAT`` unset means Rich on a terminal
and JSON everywhere else. Before that, the image and the packaged unit set
``FASTMCP_ENABLE_RICH_LOGGING=false`` to get one line per record (template
#608 / #609); the variable no longer exists, and a default that came back
through a ``copier update`` conflict would be dead weight at best and, if
it pinned ``MARKDOWN_VAULT_MCP_LOG_FORMAT`` instead, would take the choice
away from the deployment's own ``.env`` or ``/etc/markdown-vault-mcp/env``.

So the contract is an absence, asserted here because it is invisible until
someone reads the logs. The matching runtime proof — that a running
container's log really is one JSON object per line — lives in CI's
container job, which needs a Docker daemon this lane does not have.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE_PATH = REPO_ROOT / "Dockerfile"
UNIT_PATH = REPO_ROOT / "packaging" / "markdown-vault-mcp.service"

RETIRED = "FASTMCP_ENABLE_RICH_LOGGING"
FORMAT = "MARKDOWN_VAULT_MCP_LOG_FORMAT"


def _uncommented(text: str) -> list[str]:
    """Non-empty, non-comment lines, with ``\\``-continuations joined.

    Both files explain the choice in a comment that names the variables, so
    a plain substring search over the raw text would fail on the prose alone.
    """
    kept = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    joined = "\n".join(kept).replace("\\\n", " ")
    return [line.strip() for line in joined.splitlines() if line.strip()]


@pytest.fixture(scope="module")
def dockerfile_instructions() -> list[str]:
    return _uncommented(DOCKERFILE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def unit_directives() -> list[str]:
    return _uncommented(UNIT_PATH.read_text(encoding="utf-8"))


def test_image_pins_no_log_format(dockerfile_instructions: list[str]) -> None:
    """A container is never a terminal, so auto mode already means JSON there."""
    for line in dockerfile_instructions:
        assert RETIRED not in line, (
            f"{RETIRED} is gone in pvl-core 8; found it in {line!r} — a stale "
            "default that came back through a copier update conflict"
        )
        assert FORMAT not in line, (
            f"the image must not pin {FORMAT}; found it in {line!r}. Unset "
            "renders JSON in a container, and `.env` keeps the choice"
        )


def test_systemd_unit_pins_no_log_format(unit_directives: list[str]) -> None:
    """journald is not a terminal either, so the unit sets nothing."""
    for line in unit_directives:
        assert RETIRED not in line, (
            f"{RETIRED} is gone in pvl-core 8; found it in {line!r}"
        )
        assert FORMAT not in line, (
            f"the unit must not pin {FORMAT}; found it in {line!r}. "
            "/etc/markdown-vault-mcp/env is where an operator chooses"
        )


def test_systemd_unit_reads_the_operator_env_file(unit_directives: list[str]) -> None:
    """``EnvironmentFile=`` is the override path the package tells operators to use.

    Without it, ``MARKDOWN_VAULT_MCP_LOG_FORMAT=rich`` in
    ``/etc/markdown-vault-mcp/env`` would never reach the process.
    """
    assert any(line.startswith("EnvironmentFile=") for line in unit_directives), (
        "the unit reads no EnvironmentFile, so an operator cannot choose the "
        "log format from /etc/markdown-vault-mcp/env"
    )
