"""Review assurance applies to the bytes in the exported artifact."""

import io
import zipfile

import pytest

from markdown_vault_mcp._okf_review import bind_review, concept_snapshot
from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.okf_bundle import build_okf_bundle
from markdown_vault_mcp.okf_export_report import PublicationRefused
from markdown_vault_mcp.vault import Vault, VaultSettings


@pytest.fixture(autouse=True)
def isolate_git(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))


def reviewed(text, check="human-review"):
    entry = {"by": "human:alice", "at": "2020-01-01T00:00:00Z"}
    return bind_review(
        text, entry, {**entry, "check": check, "snapshot": concept_snapshot(text)}
    )


def test_export_requires_matching_review_and_preserves_bytes(tmp_path):
    text = reviewed("---\ntype: concept\nstale_after: 2000-01-01\n---\nBody\n")
    (tmp_path / "note.md").write_text(text)
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        result = build_okf_bundle(
            vault, ProjectConfig(source_dir=tmp_path), require_review=True
        )
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            assert archive.read("note.md").decode() == text
        assert result.report.review_assurance.failures == []
        with pytest.raises(PublicationRefused):
            build_okf_bundle(
                vault,
                ProjectConfig(source_dir=tmp_path),
                require_review=True,
                review_evidence="local_receipt",
            )
    finally:
        vault.close()


def test_check_kind_is_explicit(tmp_path):
    (tmp_path / "note.md").write_text(reviewed("Body\n", "schema-only"))
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        with pytest.raises(PublicationRefused):
            build_okf_bundle(
                vault, ProjectConfig(source_dir=tmp_path), require_review=True
            )
        build_okf_bundle(
            vault,
            ProjectConfig(source_dir=tmp_path),
            require_review=True,
            require_check="schema-only",
        )
    finally:
        vault.close()


def test_subtree_path_rewrite_invalidates_binding(tmp_path):
    folder = tmp_path / "sub"
    folder.mkdir()
    text = reviewed(
        "---\ntype: concept\nresource: /sub/other.md\n---\n[other](/sub/other.md)\n"
    )
    (folder / "note.md").write_text(text)
    (folder / "other.md").write_text(reviewed("Other\n"))
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        with pytest.raises(PublicationRefused) as exc:
            build_okf_bundle(
                vault,
                ProjectConfig(source_dir=tmp_path),
                folder="sub",
                require_review=True,
            )
        failure = next(
            f
            for f in exc.value.report.review_assurance.failures
            if f["path"] == "note.md"
        )
        assert failure["transformed"] is True
        assert failure["assessments"][0]["snapshot"] == "differs"
    finally:
        vault.close()


def test_receipt_and_wrong_or_unbound_review(tmp_path):
    source = tmp_path / "vault"
    source.mkdir()
    original = "---\ntype: concept\n---\nBody\n"
    (source / "note.md").write_text(original)
    state = tmp_path / "state"
    vault = Vault(
        source_dir=source, settings=VaultSettings(read_only=False, state_path=state)
    )
    try:
        vault.writer.okf_verify("note.md", "alice", concept_snapshot(original))
        before = (source / "note.md").read_bytes()
        receipt = state.with_name(state.name + ".receipts.json")
        before_receipt = receipt.read_bytes()
        result = build_okf_bundle(
            vault,
            ProjectConfig(source_dir=source),
            require_review=True,
            review_evidence="local_receipt",
        )
        assert result.report.review_assurance.failures == []
        assert (source / "note.md").read_bytes() == before
        assert receipt.read_bytes() == before_receipt
        (source / "note.md").write_text(before.decode().replace("Body", "Changed"))
        with pytest.raises(PublicationRefused) as exc:
            build_okf_bundle(
                vault, ProjectConfig(source_dir=source), require_review=True
            )
        assert (
            exc.value.report.review_assurance.failures[0]["assessments"][0]["snapshot"]
            == "differs"
        )
        (source / "note.md").write_text(
            '---\nverified: {by: "human:alice", at: "2020-01-01T00:00:00Z"}\n---\nBody\n'
        )
        with pytest.raises(PublicationRefused) as exc:
            build_okf_bundle(
                vault, ProjectConfig(source_dir=source), require_review=True
            )
        assert (
            exc.value.report.review_assurance.failures[0]["assessments"][0]["snapshot"]
            == "unbound"
        )
    finally:
        vault.close()


def test_committed_snapshot_cannot_borrow_working_review(tmp_path):
    import subprocess

    def git(*args):
        subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, capture_output=True
        )

    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    (tmp_path / "note.md").write_text("Body\n")
    git("add", "note.md")
    git("commit", "-qm", "old")
    (tmp_path / "note.md").write_text(reviewed("Body\n"))
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        with pytest.raises(PublicationRefused) as exc:
            build_okf_bundle(
                vault, ProjectConfig(source_dir=tmp_path), require_review=True
            )
        assert exc.value.report.review_assurance.failures[0]["path"] == "note.md"
        assert "note.md" in exc.value.report.snapshot.uncommitted_paths
    finally:
        vault.close()


def test_cli_uses_only_configured_source_receipts(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    source = tmp_path / "vault"
    source.mkdir()
    state = tmp_path / "state"
    original = "Body\n"
    (source / "note.md").write_text(original)
    vault = Vault(
        source_dir=source, settings=VaultSettings(read_only=False, state_path=state)
    )
    try:
        vault.writer.okf_verify("note.md", "alice", concept_snapshot(original))
    finally:
        vault.close()
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(source))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(state))
    out = tmp_path / "output.zip"
    result = CliRunner().invoke(
        app,
        ["export", str(out), "--require-review", "--review-evidence", "local_receipt"],
    )
    assert result.exit_code == 0, result.output
    assert out.is_file()
    other = tmp_path / "other"
    other.mkdir()
    (other / "note.md").write_bytes((source / "note.md").read_bytes())
    result = CliRunner().invoke(
        app,
        [
            "export",
            str(out),
            "--source-dir",
            str(other),
            "--require-review",
            "--review-evidence",
            "local_receipt",
        ],
    )
    assert result.exit_code == 1


def test_binding_does_not_load_receipts(tmp_path, monkeypatch):
    (tmp_path / "note.md").write_text(reviewed("Body\n"))
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())

    def unavailable(_path):
        raise AssertionError("binding mode must not load receipts")

    monkeypatch.setattr(vault.reader._receipt_store, "receipts", unavailable)
    try:
        build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path), require_review=True)
    finally:
        vault.close()


def test_transfer_review_options():
    from fastmcp.exceptions import ToolError

    from markdown_vault_mcp._transfer_sink import _bundle_options

    assert _bundle_options(
        "okf-bundle?require_review&require_check=schema-only&review_evidence=local_receipt:sub"
    ) == (
        "sub",
        {
            "require_review": True,
            "require_check": "schema-only",
            "review_evidence": "local_receipt",
        },
    )
    with pytest.raises(ToolError):
        _bundle_options("okf-bundle?review_evidence=other")


def test_malformed_frontmatter_is_withheld(tmp_path):
    (tmp_path / "note.md").write_text("---\ntype: [broken\n---\nBody\n")
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        with pytest.raises(PublicationRefused) as exc:
            build_okf_bundle(
                vault, ProjectConfig(source_dir=tmp_path), require_review=True
            )
        assert exc.value.report.review_assurance.failures[0]["path"] == "note.md"
    finally:
        vault.close()


@pytest.mark.parametrize(
    "ref",
    [
        "okf-bundle?require_check",
        "okf-bundle?require_check=%XX",
        "okf-bundle?require_check=%FF",
    ],
)
def test_transfer_rejects_invalid_review_value(ref):
    from fastmcp.exceptions import ToolError

    from markdown_vault_mcp._transfer_sink import _bundle_options

    with pytest.raises(ToolError):
        _bundle_options(ref)


def test_transfer_external_base_with_review():
    from markdown_vault_mcp._transfer_sink import _bundle_options

    assert _bundle_options(
        "okf-bundle?external_base=https%3A%2F%2Fexample.org&require_review"
    ) == ("", {"external_base": "https://example.org", "require_review": True})


@pytest.mark.parametrize(
    "policy",
    [
        {"require_check": ""},
        {"review_evidence": "unknown"},
    ],
)
def test_invalid_library_review_policy_is_rejected(tmp_path, policy):
    vault = Vault(source_dir=tmp_path, settings=VaultSettings())
    try:
        with pytest.raises(ValueError, match="Invalid review assurance policy"):
            build_okf_bundle(vault, ProjectConfig(source_dir=tmp_path), **policy)
    finally:
        vault.close()
