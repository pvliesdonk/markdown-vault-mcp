"""The documented ``FASTMCP_DOCKET_*`` defaults are values the task backend accepts (#2531).

``config-presentation.yml`` is the source of the configuration reference and
the ``.env`` examples, so an operator copies these values verbatim. Each must
parse as fastmcp-tasks reads it from the environment, and must be the value
the server actually runs with when the variable is unset.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastmcp_tasks.settings import DocketSettings

from markdown_vault_mcp._tasks import DEFAULT_TASK_RETENTION

_PRESENTATION = Path(__file__).resolve().parents[1] / "config-presentation.yml"

_SERVER_DEFAULTS: dict[str, Any] = {"execution_ttl": DEFAULT_TASK_RETENTION}
"""Fields where this server, not fastmcp-tasks, sets the default (``_tasks.py``)."""


def _documented_docket_vars() -> list[tuple[str, str]]:
    entries = yaml.safe_load(_PRESENTATION.read_text(encoding="utf-8"))["vars"]
    return [
        (entry["name"], str(entry["default"]))
        for entry in entries
        if entry["name"].startswith("FASTMCP_DOCKET_")
        and entry.get("default") is not None
    ]


def test_every_documented_docket_variable_is_checked() -> None:
    assert len(_documented_docket_vars()) >= 4


@pytest.mark.parametrize(("name", "value"), _documented_docket_vars())
def test_documented_default_parses_to_the_effective_default(
    name: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    field = name.removeprefix("FASTMCP_DOCKET_").lower()
    monkeypatch.setenv(name, value)
    parsed = getattr(DocketSettings(), field)
    monkeypatch.delenv(name)
    expected = _SERVER_DEFAULTS.get(field, getattr(DocketSettings(), field))
    assert parsed == expected
