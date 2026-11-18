"""Independent OKF authority and the legacy transition."""

import json
import logging
from dataclasses import replace
from pathlib import Path

import pytest

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.config_sections import _assembly
from markdown_vault_mcp.config_sections.content import ContentConfig
from markdown_vault_mcp.exceptions import ConfigurationError


def test_defaults_preserve_legacy_field_but_grant_no_writes():
    content = ContentConfig()
    assert content.okf_verify == "elicit"
    caps = content.okf_capabilities
    assert (
        caps.author,
        caps.verify,
        caps.index,
        caps.log,
        caps.folders,
    ) == (False, "off", False, "off", (".",))


def test_alias_and_explicit_overrides():
    caps = ContentConfig(
        okf_write=True,
        okf_author=True,
        okf_verify="off",
        okf_index=False,
        okf_log="events",
        okf_folders=("projects/*",),
    ).okf_capabilities
    assert (
        caps.author,
        caps.verify,
        caps.index,
        caps.log,
        caps.folders,
    ) == (True, "off", False, "events", ("projects/*",))
    assert ContentConfig(okf_verify="elicit").okf_capabilities.verify == "elicit"
    assert (
        ContentConfig(okf_verify="trust-auth").okf_capabilities.verify == "trust-auth"
    )
    legacy = ContentConfig(okf_write=True).okf_capabilities
    assert (
        legacy.author,
        legacy.verify,
        legacy.index,
        legacy.log,
        legacy.folders,
    ) == (True, "elicit", True, "writes", ("**",))


@pytest.mark.parametrize(
    "setting",
    [
        {"okf_author": True},
        {"okf_verify": "elicit"},
        {"okf_index": True},
        {"okf_log": "events"},
    ],
)
def test_off_mode_rejects_write_authority(setting):
    with pytest.raises(ConfigurationError, match="write capabilities require okf_mode"):
        ContentConfig(okf_mode="off", **setting)


def test_environment_overrides_alias(monkeypatch):
    with pytest.raises(ConfigurationError, match="write capabilities require okf_mode"):
        ProjectConfig(okf_mode="off", okf_author=True)
    for key, value in {
        "OKF_WRITE": "true",
        "OKF_AUTHOR": "true",
        "OKF_VERIFY": "off",
        "OKF_INDEX": "false",
        "OKF_LOG": "off",
        "OKF_FOLDERS": ".,projects/*",
    }.items():
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_" + key, value)
    caps = ProjectConfig.from_env().content.okf_capabilities
    assert (caps.author, caps.verify, caps.index, caps.log, caps.folders) == (
        True,
        "off",
        False,
        "off",
        (".", "projects/*"),
    )


def test_validation():
    with pytest.raises(ConfigurationError, match="okf_log"):
        ContentConfig(okf_log="unknown")
    with pytest.raises(ConfigurationError, match="okf_folders"):
        ContentConfig(okf_folders="bad")
    with pytest.raises(ConfigurationError, match="okf_folders"):
        ContentConfig(okf_folders=["valid", ""])


def test_explicit_elicit_and_folder_immutability():
    folders = ["projects/*"]
    content = ContentConfig(okf_folders=folders)
    folders.append("other")
    assert content.okf_capabilities.folders == ("projects/*",)


def test_deprecation_warns_once_across_env_and_copies(monkeypatch, caplog):
    monkeypatch.setattr(_assembly, "_okf_warning_emitted", False)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", "true")
    with caplog.at_level(logging.WARNING):
        config = ProjectConfig.from_env()
        replace(config)
        replace(config, okf_index=False)
    notices = [r for r in caplog.records if r.msg.startswith("okf_write_deprecated")]
    assert len(notices) == 1
    assert "remove OKF_WRITE" in notices[0].getMessage()
    assert all(
        name in notices[0].getMessage()
        for name in ("OKF_AUTHOR", "OKF_VERIFY", "OKF_INDEX", "OKF_LOG", "OKF_FOLDERS")
    )


def test_project_config_snapshots_folders():
    folders = ["projects/*"]
    config = ProjectConfig(okf_folders=folders)
    folders.append("secret/*")
    assert config.okf_folders == ("projects/*",)


def test_generated_examples_leave_verification_unset():
    root = Path(__file__).resolve().parents[1]
    for name in ("examples/bearer-auth.env", "examples/oidc.env"):
        text = (root / name).read_text()
        assert "MARKDOWN_VAULT_MCP_OKF_VERIFY=\n" in text

    spec = json.loads(
        (root / "docs/javascripts/config-wizard/wizard-spec.json").read_text()
    )
    question = next(item for item in spec["questions"] if item["id"] == "okf_verify")
    assert not question.get("default")
