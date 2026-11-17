"""Independent OKF authority and the legacy transition."""

import logging
from dataclasses import replace

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
        caps.legacy_stamping,
    ) == (False, "off", False, "off", (".",), False)


def test_alias_and_explicit_overrides(caplog, monkeypatch):
    monkeypatch.setattr(_assembly, "_okf_warning_emitted", False)
    with caplog.at_level(logging.WARNING):
        config = ProjectConfig(
            okf_write=True,
            okf_author=True,
            okf_verify="off",
            okf_index=False,
            okf_log="events",
            okf_folders=("projects/*",),
        )
    caps = config.content.okf_capabilities
    assert (
        caps.author,
        caps.verify,
        caps.index,
        caps.log,
        caps.folders,
        caps.legacy_stamping,
    ) == (True, "off", False, "events", ("projects/*",), True)
    notices = [r for r in caplog.records if r.msg.startswith("okf_write_deprecated")]
    assert len(notices) == 1
    assert "Keep OKF_WRITE enabled" in notices[0].getMessage()
    assert all(
        name in notices[0].getMessage()
        for name in ("OKF_AUTHOR", "OKF_VERIFY", "OKF_INDEX", "OKF_LOG", "OKF_FOLDERS")
    )
    legacy = ContentConfig(okf_write=True).okf_capabilities
    assert (
        legacy.author,
        legacy.verify,
        legacy.index,
        legacy.log,
        legacy.folders,
        legacy.legacy_stamping,
    ) == (False, "elicit", True, "writes", ("**",), True)


@pytest.mark.parametrize(
    "setting",
    [
        {"okf_author": True},
        {"okf_verify": "elicit"},
        {"okf_index": True},
        {"okf_log": "events"},
        {"okf_write": True},
    ],
)
def test_off_mode_rejects_write_authority(setting):
    with pytest.raises(ConfigurationError):
        ContentConfig(okf_mode="off", **setting)
    with pytest.raises(ConfigurationError):
        ProjectConfig(okf_mode="off", **setting)


def test_environment_overrides_alias(monkeypatch):
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


def test_explicit_elicit_and_folder_immutability():
    folders = ["projects/*"]
    content = ContentConfig(okf_verify="elicit", okf_folders=folders)
    folders.append("other")
    assert content.okf_capabilities.verify == "elicit"
    assert content.okf_capabilities.folders == ("projects/*",)


def test_deprecation_warns_once_across_env_and_copies(monkeypatch, caplog):
    monkeypatch.setattr(_assembly, "_okf_warning_emitted", False)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_OKF_WRITE", "true")
    with caplog.at_level(logging.WARNING):
        config = ProjectConfig.from_env()
        replace(config)
        replace(config, okf_index=False)
    assert sum(r.msg.startswith("okf_write_deprecated") for r in caplog.records) == 1


def test_project_config_snapshots_folders():
    folders = ["projects/*"]
    config = ProjectConfig(okf_folders=folders)
    folders.append("secret/*")
    assert config.okf_folders == ("projects/*",)
    assert replace(config).content.okf_capabilities.folders == ("projects/*",)
    with pytest.raises(ConfigurationError, match="okf_folders"):
        ProjectConfig(okf_folders=["valid", ""])
    with pytest.raises(ConfigurationError, match="okf_folders"):
        ProjectConfig(okf_folders="invalid")


def test_generated_examples_leave_verification_unset():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for name in ("examples/bearer-auth.env", "examples/oidc.env"):
        text = (root / name).read_text()
        assert "MARKDOWN_VAULT_MCP_OKF_VERIFY=\n" in text

    import json

    spec = json.loads(
        (root / "docs/javascripts/config-wizard/wizard-spec.json").read_text()
    )
    question = next(item for item in spec["questions"] if item["id"] == "okf_verify")
    assert not question.get("default")
