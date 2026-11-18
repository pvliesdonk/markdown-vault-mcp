"""Read-only bundle inventory across its public adapters."""

import pytest

from markdown_vault_mcp.bundle_inventory import BundleTreeRequest
from markdown_vault_mcp.vault import Vault


def test_untyped_inventory_excludes_reserved_and_keeps_cursor(tmp_path):
    (tmp_path / "index.md").write_text("# index")
    (tmp_path / "log.md").write_text("# log")
    for number in range(1000):
        (tmp_path / f"typed-{number:04}.md").write_text("---\ntype: concept\n---\ntext")
    (tmp_path / "z-note.md").write_text("# note")
    vault = Vault(source_dir=tmp_path)
    try:
        first = vault.reader.okf_inventory(
            BundleTreeRequest("", entry_limit=1), type_state="absent"
        )
        assert first.entries == ()
        assert first.next_cursor is not None
        second = vault.reader.okf_inventory(
            BundleTreeRequest("", cursor=first.next_cursor), type_state="absent"
        )
        assert [row.path for row in second.entries] == ["z-note.md"]
        assert second.entries[0].search_state == "not_evaluated"
    finally:
        vault.close()


def test_sparse_matches_and_matching_cursor(tmp_path):
    for number in range(150):
        (tmp_path / f"typed-{number:03}.md").write_text("---\ntype: concept\n---\ntext")
    for name in ("z-a.md", "z-b.md", "z-c.md"):
        (tmp_path / name).write_text("text")
    vault = Vault(source_dir=tmp_path)
    try:
        first = vault.reader.okf_inventory(
            BundleTreeRequest("", entry_limit=2), type_state="absent"
        )
        assert [row.path for row in first.entries] == ["z-a.md", "z-b.md"]
        assert first.next_cursor == "z-b.md"
        assert first.enumeration == "entry_limit"
        assert first.request.entry_limit == 2
        second = vault.reader.okf_inventory(
            BundleTreeRequest("", entry_limit=2, cursor=first.next_cursor),
            type_state="absent",
        )
        assert [row.path for row in second.entries] == ["z-c.md"]
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
async def test_tool_and_cli_read_only_parity(tmp_path, monkeypatch, arguments, flags):
    import json

    from fastmcp import Client
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app
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
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert tools["okf_inventory"].icons
        payload = (
            await client.call_tool("okf_inventory", {"root": "", **arguments})
        ).structured_content
    cli = CliRunner().invoke(app, ["okf-inventory", "", *flags, "--local", "--json"])
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


def test_local_keeps_configured_state_directory_private(
    tmp_path, monkeypatch, _clear_env
):
    import json

    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    state = tmp_path / "vectors"
    state.mkdir()
    (state / "secret.md").write_text("private")
    (tmp_path / "note.md").write_text("visible")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(state))
    result = CliRunner().invoke(app, ["okf-inventory", "", "--local", "--json"])
    assert result.exit_code == 0, result.output
    assert [entry["path"] for entry in json.loads(result.stdout)["entries"]] == [
        "note.md"
    ]


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


def test_truncation_preserves_unavailable_outcome(tmp_path):
    (tmp_path / "a.md").write_text("one")
    (tmp_path / "b.md").write_text("two")
    path = tmp_path
    for _ in range(32):
        path = path / "z"
        path.mkdir()
    vault = Vault(source_dir=tmp_path)
    try:
        first = vault.reader.okf_inventory(
            BundleTreeRequest("", entry_limit=1), type_state="absent"
        )
        assert first.enumeration == "unavailable"
        assert [entry.path for entry in first.entries] == ["a.md"]
        assert first.next_cursor == "a.md"
        next_page = vault.reader.okf_inventory(
            BundleTreeRequest("", entry_limit=10, cursor=first.next_cursor),
            type_state="absent",
        )
        assert [entry.reason for entry in next_page.entries] == [None, "depth_limit"]
    finally:
        vault.close()


def test_unknown_observations_survive_type_filter_except_reserved(tmp_path):
    (tmp_path / "ok.md").write_text("text")
    (tmp_path / "big.md").write_text("x" * 70000)
    (tmp_path / "link.md").symlink_to(tmp_path / "ok.md")
    (tmp_path / "index.md").symlink_to(tmp_path / "ok.md")
    (tmp_path / "image.png").symlink_to(tmp_path / "ok.md")
    (tmp_path / "linkdir").symlink_to(tmp_path, target_is_directory=True)
    vault = Vault(source_dir=tmp_path)
    try:
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
        from markdown_vault_mcp.bundle_inventory import BundleEntry
        from markdown_vault_mcp.facets.reader import _selected

        for role in ("support", "supporting"):
            assert not _selected(
                BundleEntry("support.md", role, "unavailable"), None, None, None
            )
    finally:
        vault.close()


def test_remote_invalid_scope_is_rejected_before_dispatch(
    tmp_path, monkeypatch, _clear_env
):
    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    result = CliRunner().invoke(
        app,
        ["okf-inventory", "../x", "--server", "http://localhost:8000/mcp", "--json"],
    )
    assert result.exit_code == 1
    assert "Scope must" in result.output


def test_injected_inspection_policy_is_independent_of_open_index(tmp_path):
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
    wal = root / "private.md-wal"
    lock = root / "private.md.lock"
    if case == "directory":
        index.mkdir()
        (index / "secret.md").write_text("secret")
    elif case == "sidecars":
        wal.write_bytes(b"wal")
        lock.write_bytes(b"lock")
    elif case == "corrupt":
        index.write_bytes(b"bad index")
    elif case == "newer":
        with sqlite3.connect(index) as conn:
            conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO meta VALUES ('schema_version', '99')")
    elif case in {"same", "override", "unrelated"}:
        vault = Vault(source_dir=root, settings=VaultSettings(index_path=index))
        try:
            vault.index.build_index()
        finally:
            vault.close()
    return root, index, tracker, vector, wal, lock


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "directory",
        "sidecars",
        "override",
        "same",
        "corrupt",
        "newer",
        "unrelated",
    ],
)
def test_cli_index_selection_preserves_policy(tmp_path, monkeypatch, case):
    # One matrix checks every index fallback against the same policy contract.
    import json

    from typer.testing import CliRunner

    from markdown_vault_mcp.cli import app

    root, index, tracker, vector, wal, lock = _index_policy_fixture(tmp_path, case)
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(root))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_INDEX_PATH", str(index))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_STATE_PATH", str(tracker))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH", str(root / "vectors"))
    if case == "override":
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path / "other"))
    options = ["--source-dir", str(root)] if case == "override" else []
    calls = []
    if case == "unrelated":
        from markdown_vault_mcp import _cli_bundle
        from markdown_vault_mcp.exceptions import StoreUnavailableError

        def fail_other_store(*_args, **_kwargs):
            calls.append(1)
            raise StoreUnavailableError(
                "other store unavailable", backend="sqlite", location="other.db"
            )

        monkeypatch.setattr(_cli_bundle, "_build_reader_vault", fail_other_store)
    result = CliRunner().invoke(
        app, ["okf-inventory", "", "--local", "--json", *options]
    )
    if case == "newer":
        assert result.exit_code == 1 and "schema_version" in result.output
    elif case == "unrelated":
        assert result.exit_code == 1 and "other store unavailable" in result.output
        assert calls == [1]
    else:
        assert result.exit_code == 0, result.output
        rows = json.loads(result.stdout)["entries"]
        assert [row["path"] for row in rows] == ["note.md"]
        assert rows[0]["search_state"] == (
            "searchable" if case == "same" else "not_evaluated"
        )
    assert tracker.read_text() == "private" and vector.read_bytes() == b"vector"
    if case == "missing":
        assert not index.exists()
    if case == "corrupt":
        assert index.read_bytes() == b"bad index"
    if case == "sidecars":
        assert wal.read_bytes() == b"wal" and lock.read_bytes() == b"lock"


@pytest.mark.parametrize("name", ["index.md", "log.md"])
def test_reserved_directory_boundary_can_be_resumed(tmp_path, name):
    (tmp_path / "a.md").write_text("note")
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
        assert second.entries == report.entries
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
