"""Tests for user-defined prompt loading and override semantics.

Tests cover:
- _load_user_prompt_defs: happy path, non-existent dir, non-.md files
- Override: user prompt with same name as built-in replaces the built-in
- No-arg prompt: content returned as-is
- Args prompt: $placeholder substitution works
- Write tag: prompt tagged "write" gets FastMCP write tag
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003

import pytest
from fastmcp import Client

from markdown_vault_mcp._server_prompts import _load_user_prompt_defs
from markdown_vault_mcp.mcp_server import create_server

# ---------------------------------------------------------------------------
# _load_user_prompt_defs unit tests
# ---------------------------------------------------------------------------


class TestLoadUserPromptDefs:
    """Unit tests for _load_user_prompt_defs."""

    def test_returns_empty_for_none(self) -> None:
        result = _load_user_prompt_defs(None)
        assert result == {}

    def test_returns_empty_for_nonexistent_dir(self, tmp_path: Path) -> None:
        missing = tmp_path / "no_such_folder"
        result = _load_user_prompt_defs(str(missing))
        assert result == {}

    def test_warns_for_nonexistent_dir(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        missing = tmp_path / "no_such_folder"
        import logging

        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp._server_prompts"
        ):
            _load_user_prompt_defs(str(missing))
        assert "does not exist" in caplog.text

    def test_finds_md_files(self, tmp_path: Path) -> None:
        (tmp_path / "hello.md").write_text("Hello world", encoding="utf-8")
        result = _load_user_prompt_defs(str(tmp_path))
        assert "hello" in result
        assert result["hello"]["content"] == "Hello world"

    def test_skips_non_md_files(self, tmp_path: Path) -> None:
        (tmp_path / "hello.md").write_text("Hello world", encoding="utf-8")
        (tmp_path / "readme.txt").write_text("ignore me", encoding="utf-8")
        result = _load_user_prompt_defs(str(tmp_path))
        assert set(result.keys()) == {"hello"}

    def test_parses_description(self, tmp_path: Path) -> None:
        content = "---\ndescription: My custom prompt\n---\nDo something."
        (tmp_path / "custom.md").write_text(content, encoding="utf-8")
        result = _load_user_prompt_defs(str(tmp_path))
        assert result["custom"]["description"] == "My custom prompt"

    def test_parses_arguments(self, tmp_path: Path) -> None:
        content = (
            "---\n"
            "arguments:\n"
            "  - name: path\n"
            "    description: File path\n"
            "    required: true\n"
            "  - name: style\n"
            "    description: Output style\n"
            "    required: false\n"
            "---\n"
            "Do something with $path in $style style."
        )
        (tmp_path / "custom.md").write_text(content, encoding="utf-8")
        result = _load_user_prompt_defs(str(tmp_path))
        args = result["custom"]["arguments"]
        assert len(args) == 2
        assert args[0] == {"name": "path", "description": "File path", "required": True}
        assert args[1] == {
            "name": "style",
            "description": "Output style",
            "required": False,
        }

    def test_parses_tags(self, tmp_path: Path) -> None:
        content = "---\ntags:\n  - write\n  - custom\n---\nContent."
        (tmp_path / "mytool.md").write_text(content, encoding="utf-8")
        result = _load_user_prompt_defs(str(tmp_path))
        assert result["mytool"]["tags"] == ["write", "custom"]

    def test_defaults_when_no_frontmatter(self, tmp_path: Path) -> None:
        (tmp_path / "bare.md").write_text("Just some text.", encoding="utf-8")
        result = _load_user_prompt_defs(str(tmp_path))
        assert result["bare"]["description"] == ""
        assert result["bare"]["arguments"] == []
        assert result["bare"]["tags"] == []
        assert result["bare"]["content"] == "Just some text."

    def test_multiple_files_all_loaded(self, tmp_path: Path) -> None:
        (tmp_path / "alpha.md").write_text("Alpha content", encoding="utf-8")
        (tmp_path / "beta.md").write_text("Beta content", encoding="utf-8")
        result = _load_user_prompt_defs(str(tmp_path))
        assert set(result.keys()) == {"alpha", "beta"}

    def test_skips_malformed_frontmatter(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Invalid YAML that causes python-frontmatter to raise
        (tmp_path / "broken.md").write_text(
            "---\n: invalid: yaml: [\n---\nContent.", encoding="utf-8"
        )
        import logging

        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp._server_prompts"
        ):
            result = _load_user_prompt_defs(str(tmp_path))
        assert "broken" not in result
        assert "Failed to parse" in caplog.text


class TestRegisterOneUserPromptArgValidation:
    """Security: invalid and keyword arg names are rejected without crashing."""

    def test_skips_prompt_with_keyword_arg_name(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Reserved Python keyword as arg name is rejected with a warning, not a crash."""
        import logging

        from fastmcp import FastMCP

        from markdown_vault_mcp._server_prompts import _register_one_user_prompt

        mcp = FastMCP("test")
        defn: dict = {
            "description": "bad prompt",
            "arguments": [{"name": "class", "description": "", "required": True}],
            "tags": [],
            "content": "Hello $class",
        }
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp._server_prompts"
        ):
            _register_one_user_prompt(mcp, "bad_prompt", defn)
        assert "invalid or reserved argument name" in caplog.text

    def test_skips_prompt_with_non_identifier_arg_name(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Arg name with invalid characters is rejected with a warning."""
        import logging

        from fastmcp import FastMCP

        from markdown_vault_mcp._server_prompts import _register_one_user_prompt

        mcp = FastMCP("test")
        defn: dict = {
            "description": "bad prompt",
            "arguments": [{"name": "my-arg", "description": "", "required": True}],
            "tags": [],
            "content": "Hello",
        }
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp._server_prompts"
        ):
            _register_one_user_prompt(mcp, "bad_prompt2", defn)
        assert "invalid or reserved argument name" in caplog.text

    def test_skips_prompt_with_reserved_exec_name(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Arg names 'tmpl'/'_Template' shadow exec closure vars — must be rejected."""
        import logging

        from fastmcp import FastMCP

        from markdown_vault_mcp._server_prompts import _register_one_user_prompt

        mcp = FastMCP("test")
        defn: dict = {
            "description": "bad prompt",
            "arguments": [{"name": "tmpl", "description": "", "required": True}],
            "tags": [],
            "content": "Hello $tmpl",
        }
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp._server_prompts"
        ):
            _register_one_user_prompt(mcp, "bad_exec_name", defn)
        assert "invalid or reserved argument name" in caplog.text


# ---------------------------------------------------------------------------
# Integration tests via FastMCP Client
# ---------------------------------------------------------------------------


@pytest.fixture
def _clear_vars(monkeypatch: pytest.MonkeyPatch, vault_path: Path) -> None:
    """Set minimal env vars for create_server and clear interfering vars."""
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault_path))
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_READ_ONLY", raising=False)
    for var in (
        "MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER",
        "MARKDOWN_VAULT_MCP_PROMPTS_FOLDER",
        "MARKDOWN_VAULT_MCP_SERVER_NAME",
        "MARKDOWN_VAULT_MCP_INSTRUCTIONS",
        "MARKDOWN_VAULT_MCP_INDEX_PATH",
        "MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH",
        "MARKDOWN_VAULT_MCP_STATE_PATH",
        "MARKDOWN_VAULT_MCP_INDEXED_FIELDS",
        "MARKDOWN_VAULT_MCP_REQUIRED_FIELDS",
        "MARKDOWN_VAULT_MCP_EXCLUDE",
        "MARKDOWN_VAULT_MCP_GIT_TOKEN",
        "MARKDOWN_VAULT_MCP_BEARER_TOKEN",
        "MARKDOWN_VAULT_MCP_BASE_URL",
        "MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL",
        "MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID",
        "MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET",
        "MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY",
        "MARKDOWN_VAULT_MCP_OIDC_AUDIENCE",
        "MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES",
    ):
        monkeypatch.delenv(var, raising=False)


class TestUserPromptNoArgs:
    """User-defined prompt with no arguments returns content as-is."""

    @pytest.mark.usefixtures("_clear_vars")
    async def test_no_arg_prompt_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "greet.md").write_text(
            "---\ndescription: Say hello\n---\nHello from user prompt!",
            encoding="utf-8",
        )
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_PROMPTS_FOLDER", str(prompts_dir))

        server = create_server()
        async with Client(server) as client:
            result = await client.get_prompt("greet", {})
        text = result.messages[0].content.text
        assert text == "Hello from user prompt!"

    @pytest.mark.usefixtures("_clear_vars")
    async def test_no_arg_prompt_listed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "greet.md").write_text(
            "---\ndescription: Say hello\n---\nHello!",
            encoding="utf-8",
        )
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_PROMPTS_FOLDER", str(prompts_dir))

        server = create_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()
        names = {p.name for p in prompts}
        assert "greet" in names


class TestUserPromptWithArgs:
    """User-defined prompts with argument placeholders substitute correctly."""

    @pytest.mark.usefixtures("_clear_vars")
    async def test_required_arg_substituted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        content = (
            "---\n"
            "description: Path-based prompt\n"
            "arguments:\n"
            "  - name: path\n"
            "    description: File path\n"
            "    required: true\n"
            "---\n"
            "Read the file at $path and summarize it."
        )
        (prompts_dir / "myread.md").write_text(content, encoding="utf-8")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_PROMPTS_FOLDER", str(prompts_dir))

        server = create_server()
        async with Client(server) as client:
            result = await client.get_prompt("myread", {"path": "notes/foo.md"})
        text = result.messages[0].content.text
        assert "notes/foo.md" in text
        assert "$path" not in text

    @pytest.mark.usefixtures("_clear_vars")
    async def test_optional_arg_defaults_to_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        content = (
            "---\n"
            "arguments:\n"
            "  - name: style\n"
            "    required: false\n"
            "---\n"
            "Output in [$style] style."
        )
        (prompts_dir / "styled.md").write_text(content, encoding="utf-8")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_PROMPTS_FOLDER", str(prompts_dir))

        server = create_server()
        async with Client(server) as client:
            result = await client.get_prompt("styled", {})
        text = result.messages[0].content.text
        assert "[]" in text  # empty string substituted


class TestUserPromptOverride:
    """User prompts with the same name as a built-in replace the built-in."""

    @pytest.mark.usefixtures("_clear_vars")
    async def test_user_overrides_builtin_summarize(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        content = (
            "---\n"
            "description: Custom summarize\n"
            "arguments:\n"
            "  - name: path\n"
            "    required: true\n"
            "---\n"
            "CUSTOM SUMMARIZE for $path"
        )
        (prompts_dir / "summarize.md").write_text(content, encoding="utf-8")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_PROMPTS_FOLDER", str(prompts_dir))

        server = create_server()
        async with Client(server) as client:
            result = await client.get_prompt("summarize", {"path": "some.md"})
        text = result.messages[0].content.text
        assert "CUSTOM SUMMARIZE" in text
        assert "some.md" in text
        # Built-in text should NOT appear
        assert "concise summary" not in text

    @pytest.mark.usefixtures("_clear_vars")
    async def test_non_overridden_builtins_still_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When only summarize is overridden, other built-ins still work."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "summarize.md").write_text("OVERRIDE", encoding="utf-8")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_PROMPTS_FOLDER", str(prompts_dir))

        server = create_server()
        async with Client(server) as client:
            result = await client.get_prompt(
                "compare", {"path1": "a.md", "path2": "b.md"}
            )
        text = result.messages[0].content.text
        assert "a.md" in text
        assert "b.md" in text

    @pytest.mark.usefixtures("_clear_vars")
    async def test_only_one_prompt_registered_per_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The overriding user prompt is listed only once."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "summarize.md").write_text("OVERRIDE", encoding="utf-8")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_PROMPTS_FOLDER", str(prompts_dir))

        server = create_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()
        summarize_entries = [p for p in prompts if p.name == "summarize"]
        assert len(summarize_entries) == 1


class TestUserPromptWriteTag:
    """User prompts tagged 'write' are hidden in read-only mode."""

    @pytest.mark.usefixtures("_clear_vars")
    async def test_write_tagged_user_prompt_hidden_in_readonly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        content = "---\ntags:\n  - write\n---\nWrite something."
        (prompts_dir / "mywriter.md").write_text(content, encoding="utf-8")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_PROMPTS_FOLDER", str(prompts_dir))
        # READ_ONLY is True by default (env not set)

        server = create_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()
        names = {p.name for p in prompts}
        assert "mywriter" not in names

    @pytest.mark.usefixtures("_clear_vars")
    async def test_write_tagged_user_prompt_visible_when_writable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        content = "---\ntags:\n  - write\n---\nWrite something."
        (prompts_dir / "mywriter.md").write_text(content, encoding="utf-8")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_PROMPTS_FOLDER", str(prompts_dir))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")

        server = create_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()
        names = {p.name for p in prompts}
        assert "mywriter" in names


class TestNoPromptsFolder:
    """When PROMPTS_FOLDER is not set, all built-ins are registered normally."""

    @pytest.mark.usefixtures("_clear_vars")
    async def test_all_builtins_present_without_prompts_folder(self) -> None:
        server = create_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()
        names = {p.name for p in prompts}
        # Read-only mode: these built-ins should be present
        assert "summarize" in names
        assert "related" in names
        assert "compare" in names
        # Write-tagged builtins (research, discuss, propose-links) should be hidden.
        assert "research" not in names
        assert "discuss" not in names
        assert "propose-links" not in names


class TestProposeLinks:
    """The propose-links builtin prompt is registered with the expected shape."""

    @pytest.mark.usefixtures("_clear_vars")
    async def test_propose_links_registered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # propose-links is tagged "write"; must enable write mode to see it.
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
        server = create_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()
        propose_links = next(p for p in prompts if p.name == "propose-links")
        assert (
            propose_links.description is not None
            and "link" in propose_links.description.lower()
        )
        # Both arguments are optional (scope, per_note_limit).
        arg_names = {arg.name for arg in (propose_links.arguments or [])}
        assert arg_names == {"scope", "per_note_limit"}
        assert all(not arg.required for arg in (propose_links.arguments or []))

    @pytest.mark.usefixtures("_clear_vars")
    async def test_propose_links_substitutes_arguments(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Invoking propose-links substitutes $scope and $per_note_limit into the body."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
        server = create_server()
        async with Client(server) as client:
            result = await client.get_prompt(
                "propose-links", {"scope": "1-Projects", "per_note_limit": "7"}
            )
        text = result.messages[0].content.text
        assert "1-Projects" in text
        assert "7" in text
        assert "$scope" not in text
        assert "$per_note_limit" not in text
