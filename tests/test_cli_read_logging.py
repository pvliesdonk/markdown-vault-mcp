"""Local read logging is quiet by default and restores process policy."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from logging.handlers import BufferingHandler
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from markdown_vault_mcp._cli_client import ClientOptions, ClientSettings
from markdown_vault_mcp._cli_remote import RemoteReply, run_read
from markdown_vault_mcp.exceptions import ConfigurationError

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("backend", "verbosity", "output"),
    [
        ("memory", "default", "text"),
        ("memory", "INFO", "json"),
        ("memory", "DEBUG", "agent"),
        ("memory", "legacy", "text"),
        ("memory", "verbose", "json"),
        ("memory", "WARNING", "agent"),
        ("okf-disk", "default", "json"),
        ("okf-disk", "DEBUG", "text"),
        ("unknown", "default", "agent"),
        ("unknown", "DEBUG", "json"),
        ("unknown", "WARNING", "text"),
        ("plain-disk", "default", "text"),
    ],
)
def test_natural_read_logs(
    tmp_path: Path, backend: str, output: str, verbosity: str
) -> None:
    """Real memory/OKF INFO is quiet; DEBUG, warnings and admin logs survive."""
    source = tmp_path / "vault"
    source.mkdir()
    (source / "note.md").write_text("# Quartz\nA synthetic note.\n")
    version = {"okf-disk": "0.2", "unknown": "999.0"}.get(backend)
    if version:
        (source / "index.md").write_text(
            f'---\nokf_version: "{version}"\n---\n# Index\n'
        )
    env = dict(
        os.environ,
        HOME=str(tmp_path / "home"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        MARKDOWN_VAULT_MCP_SOURCE_DIR=str(source),
        MARKDOWN_VAULT_MCP_LOG_FORMAT="json",
    )
    env.pop("FASTMCP_LOG_LEVEL", None)
    if "disk" in backend:
        env["MARKDOWN_VAULT_MCP_INDEX_PATH"] = str(tmp_path / "index.db")
        setup = subprocess.run(
            [sys.executable, "-m", "markdown_vault_mcp.cli", "admin", "index"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert setup.returncode == 0, setup.stderr
        if backend == "okf-disk" and verbosity == "default":
            assert '"level": "INFO"' in setup.stderr
    if verbosity in {"INFO", "DEBUG", "legacy", "WARNING"}:
        env[
            "FASTMCP_LOG_LEVEL"
            if verbosity == "legacy"
            else "MARKDOWN_VAULT_MCP_LOG_LEVEL"
        ] = "DEBUG" if verbosity == "legacy" else verbosity
    command = [
        sys.executable,
        "-m",
        "markdown_vault_mcp.cli",
        *(["-v"] if verbosity == "verbose" else []),
        "stats",
        "--local",
        *([] if output == "text" else ["--" + output]),
    ]
    result = subprocess.run(
        command, env=env, capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr
    assert "document_count" in result.stdout
    records = [json.loads(line) for line in result.stderr.splitlines()]
    verbose = verbosity in {"DEBUG", "legacy", "verbose"}
    if not verbose:
        assert not any(record["level"] == "INFO" for record in records)
    elif backend != "plain-disk":
        assert any(record["level"] == "INFO" for record in records)
    if backend == "unknown":
        assert any(record.get("event") == "okf_unknown_version" for record in records)
    if verbosity == "legacy":
        assert "log_level_env_deprecated" in result.stderr


@given(
    st.sampled_from([10, 20, 30, 40, 50]),
    st.integers(min_value=0, max_value=60),
    st.sampled_from([None, ConfigurationError, RuntimeError, KeyboardInterrupt]),
)
@example(20, 0, None)
@settings(max_examples=80)
def test_local_read_restores_logging(level: int, previous: int, fault: Any) -> None:
    """Every callback outcome retains stricter policy and restores the prior state."""
    root = logging.getLogger()
    saved = root.level, root.manager.disable
    observed: list[bool] = []
    error = fault("synthetic callback fault") if fault else None
    emitted = [
        (20, "synthetic_info"),
        (30, "synthetic_warning"),
        (40, "synthetic_error"),
    ]
    capture = BufferingHandler(10)

    def local() -> str:
        observed.extend(root.isEnabledFor(value) for value in [10, 20, 30, 40, 50])
        for value, message in emitted:
            root.log(value, message)
        if error:
            raise error
        return "payload"

    try:
        root.setLevel(level)
        logging.disable(previous)
        root.addHandler(capture)
        if error:
            with pytest.raises(type(error)) as raised:
                run_read("stats", {}, ClientOptions(local=True), local)
            assert raised.value is error
        else:
            assert (
                run_read("stats", {}, ClientOptions(local=True), local).data
                == "payload"
            )
        threshold = previous if level <= logging.DEBUG else max(previous, logging.INFO)
        assert observed == [
            value >= level and value > threshold for value in [10, 20, 30, 40, 50]
        ]
        assert root.manager.disable == previous
        assert root.level == level
        root.info("outside_info")
        assert [record.getMessage() for record in capture.buffer] == [
            message
            for value, message in emitted
            if value >= level and value > threshold
        ] + (
            ["outside_info"]
            if level <= logging.INFO and previous < logging.INFO
            else []
        )
    finally:
        root.removeHandler(capture)
        capture.close()
        root.setLevel(saved[0])
        logging.disable(saved[1])


def test_remote_keeps_security_suppression(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Remote DEBUG still hides credential-bearing transport logs and skips local."""

    async def remote(*_args: Any) -> RemoteReply:
        logging.getLogger(__name__).error("synthetic_secret")
        return RemoteReply("remote")

    def forbidden() -> None:
        raise AssertionError("local callback reached")

    monkeypatch.setattr("markdown_vault_mcp._cli_remote._call", remote)
    previous = logging.root.manager.disable
    with caplog.at_level(logging.DEBUG):
        result = run_read(
            "stats",
            {},
            ClientOptions(),
            forbidden,
            settings=ClientSettings("https://example.invalid/mcp"),
        )
    assert result.data == "remote"
    assert not caplog.records
    assert logging.root.manager.disable == previous
