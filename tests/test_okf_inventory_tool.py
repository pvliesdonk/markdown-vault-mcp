"""Read-only bundle inventory across its public adapters."""

from pathlib import Path

import pytest

from markdown_vault_mcp.bundle_inventory import BundleTreeRequest
from markdown_vault_mcp.vault import Vault


@pytest.mark.parametrize("sparse", [False, True])
def test_filtered_paging(tmp_path, sparse):
    (tmp_path / "index.md").write_text("# index")
    (tmp_path / "log.md").write_text("# log")
    for number in range(150 if sparse else 1000):
        (tmp_path / f"typed-{number:04}.md").write_text("---\ntype: concept\n---\ntext")
    names = ("z-a.md", "z-b.md", "z-c.md") if sparse else ("z-note.md",)
    for name in names:
        (tmp_path / name).write_text("text")
    vault = Vault(source_dir=tmp_path)
    try:
        limit = 2 if sparse else 1
        first = vault.reader.okf_inventory(
            BundleTreeRequest("", entry_limit=limit), type_state="absent"
        )
        if sparse:
            assert [row.path for row in first.entries] == ["z-a.md", "z-b.md"]
            assert first.next_cursor == "z-b.md"
            assert first.enumeration == "entry_limit"
            assert first.request.entry_limit == 2
        else:
            assert first.entries == ()
            assert first.next_cursor is not None
        second = vault.reader.okf_inventory(
            BundleTreeRequest("", entry_limit=limit, cursor=first.next_cursor),
            type_state="absent",
        )
        assert [row.path for row in second.entries] == (
            ["z-c.md"] if sparse else ["z-note.md"]
        )
        assert second.next_cursor is None
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("arguments", "flags"),
    [
        ({"type_state": "absent"}, ["--type-state", "absent"]),
        (
            {"parse_state": "malformed_metadata"},
            ["--parse-state", "malformed_metadata"],
        ),
        ({"search_state": "non_admitted"}, ["--search-state", "non_admitted"]),
        ({"entry_limit": 1}, ["--entry-limit", "1"]),
        ({"cursor": "note.md"}, ["--cursor", "note.md"]),
    ],
)
@pytest.mark.parametrize("explicit_source", [False, True])
async def test_tool_and_cli_read_only_parity(
    tmp_path, monkeypatch, arguments, flags, explicit_source
):
    import json

    from fastmcp import Client
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app
    from tests.conftest import wait_for_mcp_writer_drain
    from tests.server_factory import make_server

    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "note.md").write_text("# note")
    (vault_dir / "typed.md").write_text("---\ntype: concept\ntitle: Typed\n---\ntext")
    (vault_dir / "broken.md").write_text("---\ntype: [\n---\ntext")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_dir))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(tmp_path / "index.db"))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_REQUIRED_FIELDS", "title")
    async with Client(make_server()) as client:
        await wait_for_mcp_writer_drain(client)
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert tools["okf_inventory"].icons
        payload = (
            await client.call_tool("okf_inventory", {"root": "", **arguments})
        ).structured_content
    source_flags = ["--source-dir", str(vault_dir / ".")] if explicit_source else []
    cli = CliRunner().invoke(
        app, ["okf-inventory", "", *flags, "--local", "--json", *source_flags]
    )
    assert cli.exit_code == 0, cli.output
    assert json.loads(cli.stdout) == payload
    if "parse_state" in arguments:
        assert [row["path"] for row in payload["entries"]] == ["broken.md"]
        assert payload["entries"][0]["parse_state"] == "malformed_metadata"
    if "search_state" in arguments:
        assert [row["path"] for row in payload["entries"]] == ["note.md"]
        assert payload["entries"][0]["search_state"] == "non_admitted"


@pytest.mark.parametrize(
    "arguments",
    [
        {"root": "../outside"},
        {"root": "", "cursor": "../x"},
        {"root": "", "entry_limit": 0},
    ],
)
async def test_invalid_scope_is_actionable(tmp_path, monkeypatch, arguments):
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    from tests.server_factory import make_server

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    async with Client(make_server()) as client:
        with pytest.raises(ToolError, match="relative root"):
            await client.call_tool("okf_inventory", arguments)


def test_inventory_filters_malformed_and_non_admitted(tmp_path):
    from markdown_vault_mcp.config_sections.vault_settings import VaultSettings

    (tmp_path / "broken.md").write_text("---\ntype: [\n---\ntext")
    (tmp_path / "missing.md").write_text("text")
    (tmp_path / "typed.md").write_text("---\ntype: concept\ntitle: Typed\n---\ntext")
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(required_frontmatter=["title"])
    )
    try:
        malformed = vault.reader.okf_inventory(
            BundleTreeRequest(""), parse_state="malformed_metadata"
        )
        assert [entry.path for entry in malformed.entries] == ["broken.md"]
        vault.index.build_index()
        rejected = vault.reader.okf_inventory(
            BundleTreeRequest(""), search_state="non_admitted"
        )
        assert [entry.path for entry in rejected.entries] == ["missing.md"]
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--json", "--agent"], "--json and --agent"),
        (
            ["--source-dir", ".", "--server", "http://localhost:8000/mcp"],
            "--source-dir conflicts",
        ),
    ],
)
def test_cli_rejects_conflicting_options(
    tmp_path, monkeypatch, _clear_env, args, message
):
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    result = CliRunner().invoke(app, ["okf-inventory", "", *args])
    assert result.exit_code == 2
    assert message in result.output


def test_unknown_observations_survive_type_filter_except_reserved(
    tmp_path, monkeypatch
):
    from markdown_vault_mcp import bundle_inventory
    from markdown_vault_mcp.config_sections.vault_settings import VaultSettings

    original_read = bundle_inventory.read_regular_nofollow

    def fail_support(parent, name, limit):
        if name == "support.md":
            raise OSError("unavailable")
        return original_read(parent, name, limit)

    monkeypatch.setattr(bundle_inventory, "read_regular_nofollow", fail_support)
    (tmp_path / "support.md").write_text("support")
    (tmp_path / "ok.md").write_text("text")
    (tmp_path / "big.md").write_text("x" * 70000)
    (tmp_path / "link.md").symlink_to(tmp_path / "ok.md")
    (tmp_path / "index.md").symlink_to(tmp_path / "ok.md")
    (tmp_path / "image.png").symlink_to(tmp_path / "ok.md")
    (tmp_path / "linkdir").symlink_to(tmp_path, target_is_directory=True)
    vault = Vault(
        source_dir=tmp_path, settings=VaultSettings(conventions_file="support.md")
    )
    try:
        observed = vault.reader.inspect_bundle_tree(BundleTreeRequest(""))
        support = next(row for row in observed.entries if row.path == "support.md")
        assert (support.role, support.state) == ("support", "unavailable")
        for state in (None, "absent"):
            report = vault.reader.okf_inventory(BundleTreeRequest(""), type_state=state)
            rows = {row.path: row for row in report.entries}
            assert set(rows) == {"ok.md", "big.md", "link.md"}
            assert rows["big.md"].reason == "note_limit"
            assert rows["link.md"].reason == "symlink"
        report = vault.reader.okf_inventory(
            BundleTreeRequest(""),
            parse_state="malformed_metadata",
            search_state="searchable",
        )
        assert report.entries == ()
        report = vault.reader.okf_inventory(
            BundleTreeRequest(""), parse_state="malformed_metadata"
        )
        assert {row.path for row in report.entries} == {"big.md", "link.md"}
    finally:
        vault.close()


@pytest.mark.parametrize(
    ("root", "options", "message"),
    [("../x", [], "Scope must"), ("", ["--cursor", "../x"], "Scope must")],
)
def test_remote_invalid_scope_is_rejected_before_dispatch(
    tmp_path, monkeypatch, _clear_env, root, options, message
):
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    result = CliRunner().invoke(
        app,
        [
            "okf-inventory",
            root,
            "--server",
            "http://localhost:8000/mcp",
            "--json",
            *options,
        ],
    )
    assert result.exit_code == 2
    assert message in result.output


def test_injected_inspection_policy_replaces_default(tmp_path):
    from markdown_vault_mcp.bundle_inventory import InspectionPolicy

    (tmp_path / "private.md").write_text("private")
    default = Vault(source_dir=tmp_path)
    injected = Vault(
        source_dir=tmp_path,
        inspection_policy=InspectionPolicy(index_path=tmp_path / "private.md"),
    )
    try:
        assert [
            e.path for e in default.reader.okf_inventory(BundleTreeRequest("")).entries
        ] == ["private.md"]
        assert injected.reader.okf_inventory(BundleTreeRequest("")).entries == ()
    finally:
        default.close()
        injected.close()


def _index_policy_fixture(tmp_path, case):
    import sqlite3

    from markdown_vault_mcp.config_sections.vault_settings import VaultSettings

    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("note")
    index = root / "private.md"
    tracker = root / "tracker.md"
    tracker.write_text("private")
    vector = root / "vectors.npy"
    vector.write_bytes(b"vector")
    if case == "state_dir":
        tracker.unlink()
        tracker.mkdir()
        (tracker / "secret.md").write_text("private")
    if case == "zero":
        index.write_bytes(b"")
    elif case == "unbuilt":
        vault = Vault(source_dir=root, settings=VaultSettings(index_path=index))
        vault.close()
    elif case == "corrupt":
        index.write_bytes(b"bad index")
    elif case == "newer":
        with sqlite3.connect(index) as conn:
            conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO meta VALUES ('schema_version', '99')")
    elif case in {
        "same",
        "same_override",
        "alias",
        "override",
        "unrelated",
        "internal_value",
    }:
        vault = Vault(source_dir=root, settings=VaultSettings(index_path=index))
        try:
            vault.index.build_index()
        finally:
            vault.close()
    return root, index, tracker, vector


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "state_dir",
        "override",
        "same_override",
        "alias",
        "internal_value",
        "same",
        "corrupt",
        "newer",
        "zero",
        "unbuilt",
        "unrelated",
    ],
)
def test_cli_index_selection_preserves_policy(tmp_path, monkeypatch, case):
    # One matrix checks every index fallback against the same policy contract.
    import json

    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root, index, tracker, vector = _index_policy_fixture(tmp_path, case)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(root))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(index))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tracker))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", str(root / "vectors"))
    if case == "override":
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path / "other"))
    options = (
        ["--source-dir", str(root)] if case in {"override", "same_override"} else []
    )
    if case == "alias":
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        options = ["--source-dir", str(alias)]
    calls = []
    if case in {"unrelated", "internal_value"}:
        from markdown_vault_mcp import _cli_bundle
        from markdown_vault_mcp.exceptions import StoreUnavailableError

        def fail_other_store(*_args, **_kwargs):
            calls.append(1)
            if case == "internal_value":
                raise ValueError("internal value failure")
            raise StoreUnavailableError(
                "other store unavailable", backend="sqlite", location="other.db"
            )

        monkeypatch.setattr(_cli_bundle, "_build_reader_vault", fail_other_store)
    original_index = (
        index.read_bytes() if case in {"zero", "newer", "unbuilt"} else None
    )
    result = CliRunner().invoke(
        app, ["okf-inventory", "", "--local", "--json", *options]
    )
    if case in {"unrelated", "internal_value"}:
        expected = (
            "other store unavailable"
            if case == "unrelated"
            else "internal value failure"
        )
        assert result.exit_code == 1 and expected in result.output
        assert calls == [1]
    else:
        assert result.exit_code == 0, result.output
        rows = json.loads(result.stdout)["entries"]
        assert [row["path"] for row in rows] == ["note.md"]
        assert rows[0]["search_state"] == (
            "searchable"
            if case in {"same", "same_override", "alias"}
            else "not_evaluated"
        )
    assert (
        (tracker / "secret.md").read_text() == "private"
        if case == "state_dir"
        else tracker.read_text() == "private"
    )
    assert vector.read_bytes() == b"vector"
    if original_index is not None:
        assert index.read_bytes() == original_index
    if case == "missing":
        assert not index.exists()
    if case == "corrupt":
        assert index.read_bytes() == b"bad index"


@pytest.mark.parametrize("name", ["index.md", "log.md", "ordinary"])
def test_reserved_directory_boundary_can_be_resumed(tmp_path, name):
    (tmp_path / "a.md").write_text("note")
    (tmp_path / "b.md").write_text("note")
    path = tmp_path
    for _ in range(31):
        path = path / "d"
        path.mkdir()
    boundary = path / name
    boundary.mkdir()
    (boundary / "note.md").write_text("note")
    vault = Vault(source_dir=tmp_path)
    try:
        report = vault.reader.okf_inventory(
            BundleTreeRequest(""), search_state="searchable"
        )
        assert len(report.entries) == 1
        assert report.entries[0].reason == "depth_limit"
        first = vault.reader.okf_inventory(BundleTreeRequest("", entry_limit=1))
        assert [row.path for row in first.entries] == ["a.md"]
        second = vault.reader.okf_inventory(
            BundleTreeRequest("", entry_limit=1, cursor=first.next_cursor)
        )
        assert second.enumeration == "unavailable"
        assert [row.path for row in second.entries] == ["b.md"]
        third = vault.reader.okf_inventory(
            BundleTreeRequest("", cursor=second.next_cursor)
        )
        assert third.entries == report.entries
        resumed = vault.reader.okf_inventory(BundleTreeRequest(report.entries[0].path))
        assert [row.path for row in resumed.entries] == [
            f"{report.entries[0].path}/note.md"
        ]
    finally:
        vault.close()


def test_cli_interrupt_exits_130(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from markdown_vault_mcp import _cli_bundle
    from markdown_vault_mcp.cli import app

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setattr(_cli_bundle, "run_read", interrupt)
    result = CliRunner().invoke(app, ["okf-inventory", "", "--local"])
    assert result.exit_code == 130


@pytest.mark.parametrize("case", ["missing", "empty", "unrelated", "override"])
def test_local_inventory_does_not_bootstrap_managed_source(tmp_path, monkeypatch, case):
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app
    from markdown_vault_mcp.git.bootstrap import RepoBootstrap

    configured = tmp_path / "configured"
    if case != "missing":
        configured.mkdir()
    if case == "unrelated":
        (configured / ".git").mkdir()
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "note.md").write_text("note")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(configured))
    monkeypatch.setenv(
        "MARKDOWN_VAULT_MCP_GIT_REPO_URL", "https://example.test/repo.git"
    )
    attempts = []

    def forbid_clone(_self, path):
        attempts.append(path)
        raise AssertionError("unexpected clone")

    monkeypatch.setattr(RepoBootstrap, "_clone_into", forbid_clone)
    options = ["--source-dir", str(selected)] if case == "override" else []
    result = CliRunner().invoke(
        app, ["okf-inventory", "", "--local", "--json", *options]
    )
    assert not attempts
    assert result.exit_code == (1 if case == "missing" else 0), result.output


async def test_unavailable_ledger_is_visible_through_tool_and_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from fastmcp import Client
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app
    from tests.conftest import wait_for_mcp_writer_drain
    from tests.server_factory import make_server

    source = tmp_path / "vault"
    source.mkdir()
    (source / "live.md").write_text("Live")
    state = tmp_path / "state"
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(source))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(state))
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_INDEX_PATH", raising=False)
    async with Client(make_server()) as client:
        await wait_for_mcp_writer_drain(client)
        (tmp_path / "state.observations.json").write_text('{"version": 99, "rows": {}}')
        payload = (
            await client.call_tool("okf_inventory", {"root": ""})
        ).structured_content
        cli = CliRunner().invoke(app, ["okf-inventory", "", "--local", "--json"])
        assert cli.exit_code == 0, cli.output
        for result in (payload, json.loads(cli.stdout)):
            assert result["enumeration"] == "complete"
            assert result["removal_history"] == "unavailable"
            assert any(entry["path"] == "live.md" for entry in result["entries"])
