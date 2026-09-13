"""Contract tests for one-line log output in the shipped deployments.

Neither a container's stderr nor journald is a terminal. Rich therefore falls
back to 80 columns, and a structured request-log record is longer than what
its own time, level and source columns leave, so every record wraps across
three space-padded lines — unreadable in ``docker logs`` / ``journalctl``, and
unparseable by a collector (template #608). Both shipped deployments turn Rich
off instead, which restores one line per record.

That fix is a single environment default in each of two files, easy to lose in
a ``copier update`` conflict and invisible until someone reads the logs, so it
is asserted here rather than only in the pipeline that builds the image. The
matching runtime proof — that the running container's log really is one line
per record — lives in CI's container job, which needs a Docker daemon this
lane does not have.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE_PATH = REPO_ROOT / "Dockerfile"
UNIT_PATH = REPO_ROOT / "packaging" / "markdown-vault-mcp.service"

SETTING = "FASTMCP_ENABLE_RICH_LOGGING=false"


def _uncommented(text: str) -> list[str]:
    """Non-empty, non-comment lines, with ``\\``-continuations joined.

    Both files explain the setting in a comment that quotes it verbatim, so a
    plain substring search over the raw text would pass on the prose alone.
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


def test_image_defaults_rich_logging_off(dockerfile_instructions: list[str]) -> None:
    """The image bakes the default in, so a bare ``docker run`` gets it too."""
    env_instructions = [
        line for line in dockerfile_instructions if line.startswith("ENV ")
    ]
    assert any(SETTING in line for line in env_instructions), (
        f"no Dockerfile ENV sets {SETTING} — without it every structured log "
        "record wraps across three lines in `docker logs`"
    )


def test_image_default_stays_overridable(dockerfile_instructions: list[str]) -> None:
    """``ENV`` is a default; ``.env`` and compose ``environment:`` outrank it.

    Guards the choice of instruction, not just the value: baking the setting
    into ``CMD`` or the entrypoint would make it unconditional, and an
    operator who wants color back could no longer ask for it.
    """
    for line in dockerfile_instructions:
        if line.startswith("ENV "):
            continue
        assert SETTING.split("=")[0] not in line, (
            "the rich-logging default belongs in an ENV instruction, where an "
            f"operator can override it; found it in {line!r}"
        )


def test_systemd_unit_defaults_rich_logging_off(unit_directives: list[str]) -> None:
    """journald is not a terminal either, so the unit makes the same trade."""
    assert f"Environment={SETTING}" in unit_directives, (
        f"the systemd unit sets no Environment={SETTING} — without it every "
        "structured log record wraps across three lines in `journalctl`"
    )


def test_systemd_unit_default_stays_overridable(unit_directives: list[str]) -> None:
    """``EnvironmentFile=`` overrides ``Environment=``, whatever the order.

    That is what keeps ``/etc/markdown-vault-mcp/env`` — the file the package
    tells an operator to edit — able to turn Rich back on.
    """
    assert any(line.startswith("EnvironmentFile=") for line in unit_directives), (
        "the unit reads no EnvironmentFile, so the rich-logging default above "
        "cannot be overridden by the operator's env file"
    )
