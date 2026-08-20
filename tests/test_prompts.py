"""Tests for user-defined prompt loading and override semantics.

Tests cover:
- _load_user_prompt_defs: happy path, non-existent dir, non-.md files
- Override: user prompt with same name as built-in replaces the built-in
- No-arg prompt: content returned as-is
- Args prompt: $placeholder substitution works
- Write tag: prompt tagged "write" gets FastMCP write tag
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client

from markdown_vault_mcp._server_prompts import (
    _load_builtin_prompt,
    _load_user_prompt_defs,
)
from markdown_vault_mcp.server import make_server

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

    def test_strips_bom_from_user_prompt(self, tmp_path: Path) -> None:
        """A UTF-8 BOM before the frontmatter must not break parsing (#673)."""
        (tmp_path / "greet.md").write_bytes(
            b"\xef\xbb\xbf---\ndescription: Greeter\n---\n\nHello\n"
        )
        result = _load_user_prompt_defs(str(tmp_path))
        assert "greet" in result
        assert (
            result["greet"]["description"] == "Greeter"
        )  # frontmatter parsed past the BOM

    def test_non_list_arguments_coerced_to_empty(self, tmp_path: Path) -> None:
        """A non-iterable ``arguments`` value is coerced to [] rather than raising.

        An int is used deliberately: without the isinstance guard, ``for arg in 5``
        raises TypeError, so this pins the guard (a string would iterate to [] even
        without it, masking a regression).
        """
        (tmp_path / "bad_args.md").write_text(
            "---\narguments: 5\n---\nBody.", encoding="utf-8"
        )
        result = _load_user_prompt_defs(str(tmp_path))
        assert result["bad_args"]["arguments"] == []

    def test_non_list_tags_coerced_to_empty(self, tmp_path: Path) -> None:
        """A scalar ``tags`` value is coerced to [] rather than char-iterated."""
        (tmp_path / "bad_tags.md").write_text(
            "---\ntags: write\n---\nBody.", encoding="utf-8"
        )
        result = _load_user_prompt_defs(str(tmp_path))
        assert result["bad_tags"]["tags"] == []

    def test_non_string_description_coerced_to_str(self, tmp_path: Path) -> None:
        """A non-string ``description`` (frontmatter 1.3 can return an int) is
        coerced to str rather than flowing through as a non-str value."""
        (tmp_path / "numdesc.md").write_text(
            "---\ndescription: 5\n---\nBody.", encoding="utf-8"
        )
        result = _load_user_prompt_defs(str(tmp_path))
        assert result["numdesc"]["description"] == "5"


class TestLoadBuiltinPrompt:
    """Unit tests for _load_builtin_prompt."""

    def test_strips_bom_from_builtin_prompt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The built-in prompt loader's hand-rolled utf-8-sig read strips a BOM (#673)."""
        from markdown_vault_mcp import _server_prompts

        (tmp_path / "demo.md").write_bytes(
            b"\xef\xbb\xbf---\ndescription: Demo\n---\n\nbody\n"
        )
        monkeypatch.setattr(_server_prompts, "_BUILTIN_PROMPTS_DIR", tmp_path)
        result = _load_builtin_prompt("demo")
        assert result is not None
        assert result["description"] == "Demo"  # frontmatter parsed past the BOM
        assert result["content"] == "body"

    def test_non_list_arguments_and_tags_coerced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scalar arguments/tags are coerced to [] (the guards this loader gained).

        The previous ``[str(t) for t in post.get("tags", [])]`` would char-iterate
        a scalar ``tags: write`` into ['w', 'r', ...]; the isinstance guard now
        yields []. ``arguments`` gains the same guard the user loader already had;
        an int pins it (``for arg in 5`` raises TypeError without the guard).
        """
        from markdown_vault_mcp import _server_prompts

        (tmp_path / "demo.md").write_text(
            "---\narguments: 5\ntags: write\n---\nbody", encoding="utf-8"
        )
        monkeypatch.setattr(_server_prompts, "_BUILTIN_PROMPTS_DIR", tmp_path)
        result = _load_builtin_prompt("demo")
        assert result is not None
        assert result["arguments"] == []
        assert result["tags"] == []


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
        assert "is a reserved Python keyword" in caplog.text

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
        assert "is not a valid Python identifier" in caplog.text

    def test_formerly_reserved_name_now_allowed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """'tmpl' was reserved only to protect the exec() namespace. With exec
        removed (#788) it is a plain identifier and is no longer rejected."""
        import logging

        from fastmcp import FastMCP

        from markdown_vault_mcp._server_prompts import _register_one_user_prompt

        mcp = FastMCP("test")
        defn: dict = {
            "description": "ok prompt",
            "arguments": [{"name": "tmpl", "description": "", "required": True}],
            "tags": [],
            "content": "Hello $tmpl",
        }
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp._server_prompts"
        ):
            _register_one_user_prompt(mcp, "ok_name", defn)
        assert "skipping prompt" not in caplog.text

    def test_builtin_prompt_skips_invalid_arg_name(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The built-in registration path also rejects non-identifier names
        (defense-in-depth for the shared synthetic-signature path, #788)."""
        import logging

        from fastmcp import FastMCP

        from markdown_vault_mcp._server_prompts import _register_one_builtin_prompt

        mcp = FastMCP("test")
        defn: dict = {
            "description": "bad builtin",
            "arguments": [{"name": "bad-name", "description": "", "required": True}],
            "tags": [],
            "icons": "",
            "content": "Hi $x",
        }
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp._server_prompts"
        ):
            _register_one_builtin_prompt(mcp, "bad_builtin", defn)
        assert "is not a valid Python identifier" in caplog.text

    def test_module_has_no_exec(self) -> None:
        """The prompt-registration module compiles no source and calls no exec
        (the S102 sink removed in #788)."""
        import markdown_vault_mcp._server_prompts as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "exec(" not in source

    def test_user_prompt_skips_invalid_arg_order(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An optional arg before a required one cannot form a valid signature;
        the prompt is skipped with a warning rather than crashing startup."""
        import logging

        from fastmcp import FastMCP

        from markdown_vault_mcp._server_prompts import _register_one_user_prompt

        mcp = FastMCP("test")
        defn: dict = {
            "description": "bad order",
            "arguments": [
                {"name": "opt", "description": "", "required": False},
                {"name": "req", "description": "", "required": True},
            ],
            "tags": [],
            "content": "$opt $req",
        }
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp._server_prompts"
        ):
            _register_one_user_prompt(mcp, "bad_order", defn)
        assert "cannot form a valid signature" in caplog.text

    def test_builtin_prompt_skips_invalid_arg_order(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The built-in path likewise skips an unformable signature (covers the
        second except-ValueError handler)."""
        import logging

        from fastmcp import FastMCP

        from markdown_vault_mcp._server_prompts import _register_one_builtin_prompt

        mcp = FastMCP("test")
        defn: dict = {
            "description": "bad order builtin",
            "arguments": [
                {"name": "opt", "description": "", "required": False},
                {"name": "req", "description": "", "required": True},
            ],
            "tags": [],
            "icons": "",
            "content": "$opt $req",
        }
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp._server_prompts"
        ):
            _register_one_builtin_prompt(mcp, "bad_order_builtin", defn)
        assert "cannot form a valid signature" in caplog.text


# ---------------------------------------------------------------------------
# Integration tests via FastMCP Client
# ---------------------------------------------------------------------------


@pytest.fixture
def _clear_vars(monkeypatch: pytest.MonkeyPatch, vault_path: Path) -> None:
    """Set minimal env vars for make_server and clear interfering vars."""
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

        server = make_server()
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

        server = make_server()
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

        server = make_server()
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

        server = make_server()
        async with Client(server) as client:
            result = await client.get_prompt("styled", {})
        text = result.messages[0].content.text
        assert "[]" in text  # empty string substituted

    @pytest.mark.usefixtures("_clear_vars")
    async def test_synthetic_signature_exposes_arguments(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FastMCP introspects the prompt's arguments from the synthetic
        signature (name + required), so clients see the declared arguments."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        content = (
            "---\n"
            "arguments:\n"
            "  - name: path\n"
            "    required: true\n"
            "  - name: style\n"
            "    required: false\n"
            "---\n"
            "$path in $style"
        )
        (prompts_dir / "twoarg.md").write_text(content, encoding="utf-8")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_PROMPTS_FOLDER", str(prompts_dir))

        server = make_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()
        prompt = next(p for p in prompts if p.name == "twoarg")
        args = {a.name: a.required for a in prompt.arguments}
        assert args == {"path": True, "style": False}

    @pytest.mark.usefixtures("_clear_vars")
    async def test_formerly_reserved_arg_name_substitutes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A prompt whose arg is named 'tmpl' (formerly exec-reserved) now
        registers and substitutes end-to-end (#788)."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        content = (
            "---\narguments:\n  - name: tmpl\n    required: true\n---\nHello $tmpl."
        )
        (prompts_dir / "greet.md").write_text(content, encoding="utf-8")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_PROMPTS_FOLDER", str(prompts_dir))

        server = make_server()
        async with Client(server) as client:
            result = await client.get_prompt("greet", {"tmpl": "World"})
        assert "Hello World." in result.messages[0].content.text


def test_research_derive_slugifies_topic() -> None:
    """_research_derive turns $topic into a filesystem-safe ${topic_slug}
    (lowercased; each non-word/dash char → '-'; leading/trailing '-' trimmed) —
    the one behaviour beyond plain substitution moved out of the exec (#788)."""
    from markdown_vault_mcp._server_prompts import _research_derive

    values: dict = {"topic": "Horror Fiction!"}
    _research_derive(values)
    assert values["topic_slug"] == "horror-fiction"


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

        server = make_server()
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

        server = make_server()
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

        server = make_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()
        summarize_entries = [p for p in prompts if p.name == "summarize"]
        assert len(summarize_entries) == 1

    @pytest.mark.usefixtures("_clear_vars")
    async def test_override_does_not_log_component_already_exists(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Overriding a built-in prunes it first, so FastMCP logs no duplicate warning.

        register_domain_prompts removes the shadowed built-in before registering
        the user prompt; without that, FastMCP's default ``on_duplicate="warn"``
        would emit "Component already exists: prompt:summarize@" on every startup
        for a fully-supported override.

        The warning rides FastMCP's own ``fastmcp`` logger, which sets
        ``propagate=False`` — so pytest's root-attached ``caplog`` never sees it.
        Capture that logger directly, and neutralize ``configure_logging_from_env``
        (make_server calls it, and it re-installs the ``fastmcp`` logger's handlers
        + propagate) so the capture handler survives.
        """
        import logging

        monkeypatch.setattr(
            "markdown_vault_mcp.server.configure_logging_from_env",
            lambda *_a, **_k: None,
        )
        records: list[str] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record.getMessage())

        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "summarize.md").write_text("OVERRIDE", encoding="utf-8")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_PROMPTS_FOLDER", str(prompts_dir))

        fastmcp_logger = logging.getLogger("fastmcp")
        handler = _Capture()
        prev_level = fastmcp_logger.level
        fastmcp_logger.setLevel(logging.WARNING)
        fastmcp_logger.addHandler(handler)
        try:
            make_server()
        finally:
            fastmcp_logger.removeHandler(handler)
            fastmcp_logger.setLevel(prev_level)
        assert not any("already exists" in m for m in records)

    @pytest.mark.usefixtures("_clear_vars")
    async def test_override_of_unregistered_builtin_does_not_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A user prompt shadowing a built-in that failed to register must not crash.

        register_domain_prompts prunes the shadowed built-in before re-registering;
        if that built-in was never registered (missing static file, or a #799
        backstop-caught error), remove_prompt raises KeyError. The prune is guarded
        so server construction still succeeds — the user prompt registers fresh.
        """
        from markdown_vault_mcp import _server_prompts

        # "summarize" never registers (loader returns None), yet the operator
        # ships a summarize.md — the prune would KeyError without the guard.
        original = _server_prompts._load_builtin_prompt
        monkeypatch.setattr(
            _server_prompts,
            "_load_builtin_prompt",
            lambda name: None if name == "summarize" else original(name),
        )
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "summarize.md").write_text("USER SUMMARIZE", encoding="utf-8")
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_PROMPTS_FOLDER", str(prompts_dir))

        server = make_server()  # must not raise
        async with Client(server) as client:
            names = {p.name for p in await client.list_prompts()}
        assert "summarize" in names  # the user prompt registered


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
        # Read-only is opt-in since #1113, so name it rather than relying
        # on the default.
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")

        server = make_server()
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

        server = make_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()
        names = {p.name for p in prompts}
        assert "mywriter" in names


class TestNoPromptsFolder:
    """When PROMPTS_FOLDER is not set, all built-ins are registered normally."""

    @pytest.mark.usefixtures("_clear_vars")
    async def test_all_builtins_present_without_prompts_folder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every built-in registers on a default (read-write) server (#1113)."""
        server = make_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()
        names = {p.name for p in prompts}
        assert "summarize" in names
        assert "related" in names
        assert "compare" in names
        # Write-tagged builtins are present too, now that the default is
        # read-write; opting in to read-only hides exactly those three.
        assert "research" in names
        assert "discuss" in names
        assert "propose-links" in names

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "true")
        server = make_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()
        names = {p.name for p in prompts}
        assert "summarize" in names
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
        server = make_server()
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
        server = make_server()
        async with Client(server) as client:
            result = await client.get_prompt(
                "propose-links", {"scope": "1-Projects", "per_note_limit": "7"}
            )
        text = result.messages[0].content.text
        assert "1-Projects" in text
        assert "7" in text
        assert "$scope" not in text
        assert "$per_note_limit" not in text

    @pytest.mark.usefixtures("_clear_vars")
    async def test_propose_links_checks_folder_conventions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The builtin body instructs a get_conventions check before proposing."""
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
        server = make_server()
        async with Client(server) as client:
            result = await client.get_prompt("propose-links", {})
        text = result.messages[0].content.text
        assert "get_conventions" in text
        assert "conventions" in text.lower()


class TestSummarizeSubtree:
    """The summarize-subtree prompt: client-side map-reduce recipe (#1035).

    Registered config-dependently (the ``create_from_template`` pattern): its
    ``${route_note}`` slot resolves at registration time to a
    prefer-the-tool note when a summarize backend is configured, and is
    removed when none is.
    """

    @pytest.mark.usefixtures("_clear_vars")
    async def test_registered_read_only_with_arguments(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Untagged (read) prompt: visible in read-only mode; paths required,
        focus optional."""
        for var in (
            "OPENAI_API_KEY",
            "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_API_KEY",
            "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL",
        ):
            monkeypatch.delenv(var, raising=False)
        server = make_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()
        prompt = next(p for p in prompts if p.name == "summarize-subtree")
        args = {a.name: a.required for a in (prompt.arguments or [])}
        assert args == {"paths": True, "focus": False}

    @pytest.mark.usefixtures("_clear_vars")
    async def test_substitutes_arguments(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "OPENAI_API_KEY",
            "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_API_KEY",
            "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL",
        ):
            monkeypatch.delenv(var, raising=False)
        server = make_server()
        async with Client(server) as client:
            result = await client.get_prompt(
                "summarize-subtree",
                {"paths": "projects/alpha, notes/b.md", "focus": "action items"},
            )
        text = result.messages[0].content.text
        assert "projects/alpha, notes/b.md" in text
        assert "action items" in text
        assert "$paths" not in text
        assert "$focus" not in text

    @pytest.mark.usefixtures("_clear_vars")
    async def test_recipe_is_subagent_optional_with_attribution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The body carries the batched recipe: toc-based planning, path
        attribution, the batch-context rule, and an explicit no-subagents
        execution path."""
        for var in (
            "OPENAI_API_KEY",
            "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_API_KEY",
            "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL",
        ):
            monkeypatch.delenv(var, raising=False)
        server = make_server()
        async with Client(server) as client:
            result = await client.get_prompt("summarize-subtree", {"paths": "x"})
        text = result.messages[0].content.text
        assert "get_toc" in text
        assert "subagent" in text
        assert "attribution" in text
        assert "Do not accumulate note bodies" in text
        assert "No subagents: process the batches yourself" in text
        # Truncation recovery must re-enumerate completely (higher max_notes
        # or the uncapped list_documents), never plan from a truncated toc —
        # notes sorted past the cutoff would be silently omitted.
        assert "never plan from a truncated listing" in text
        assert "list_documents" in text

    @pytest.mark.usefixtures("_clear_vars")
    async def test_no_backend_omits_tool_mention(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without a summarize backend the prompt never names the (absent)
        tool, and the route-note slot is fully removed."""
        for var in (
            "OPENAI_API_KEY",
            "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_API_KEY",
            "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL",
        ):
            monkeypatch.delenv(var, raising=False)
        server = make_server()
        async with Client(server) as client:
            result = await client.get_prompt("summarize-subtree", {"paths": "x"})
        text = result.messages[0].content.text
        assert "`summarize` tool" not in text
        assert "route_note" not in text

    @pytest.mark.usefixtures("_clear_vars")
    async def test_backend_configured_prefers_tool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With a backend configured the prompt opens by preferring the
        server-side tool over the recipe."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        server = make_server()
        async with Client(server) as client:
            result = await client.get_prompt("summarize-subtree", {"paths": "x"})
        text = result.messages[0].content.text
        assert "`summarize` tool" in text
        assert "Prefer the tool" in text
        assert "route_note" not in text

    def test_registration_failure_logged_not_raised(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A malformed summarize-subtree defn is logged at ERROR (packaging
        defect) without raising — the #799 backstop pattern."""
        import logging

        from fastmcp import FastMCP

        from markdown_vault_mcp import _server_prompts

        monkeypatch.setattr(
            _server_prompts,
            "_load_builtin_prompt",
            # Missing "description" -> _register_one_builtin_prompt raises.
            lambda _name: {"arguments": [], "tags": [], "icons": "", "content": "x"},
        )
        mcp = FastMCP("test")
        with caplog.at_level(
            logging.ERROR, logger="markdown_vault_mcp._server_prompts"
        ):
            _server_prompts._register_summarize_subtree(mcp, tool_available=False)
        assert "summarize-subtree' failed to register" in caplog.text

    @pytest.mark.usefixtures("_clear_vars")
    async def test_user_prompt_overrides(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A user prompt named summarize-subtree replaces the built-in (the
        create_from_template skip pattern), registered exactly once."""
        for var in (
            "OPENAI_API_KEY",
            "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_API_KEY",
            "MARKDOWN_VAULT_MCP_SUMMARIZE_OPENAI_BASE_URL",
        ):
            monkeypatch.delenv(var, raising=False)
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "summarize-subtree.md").write_text(
            "CUSTOM SUBTREE", encoding="utf-8"
        )
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_PROMPTS_FOLDER", str(prompts_dir))
        server = make_server()
        async with Client(server) as client:
            prompts = await client.list_prompts()
            result = await client.get_prompt("summarize-subtree", {})
        assert result.messages[0].content.text == "CUSTOM SUBTREE"
        assert len([p for p in prompts if p.name == "summarize-subtree"]) == 1


class TestRegisterPromptsPerPromptGuard:
    """A malformed prompt is skipped and its siblings still register.

    The per-prompt helpers can raise *outside* their narrow ``except ValueError``
    handlers — a def-dict missing a key (``KeyError``) or a ``mcp.prompt(...)``
    rejection. A loop backstop catches these so one bad prompt does not abort
    registration of the rest (#799). Built-in failures go through the backstop in
    :func:`register_prompts`; user-prompt failures through the one in
    :func:`register_domain_prompts`.
    """

    async def test_user_prompt_registration_failure_skips_and_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A user def missing ``content`` (KeyError in the helper) is skipped with
        a WARNING naming it; sibling user prompts still register."""
        import logging

        from fastmcp import FastMCP

        from markdown_vault_mcp import _server_prompts
        from markdown_vault_mcp._server_prompts import register_domain_prompts

        monkeypatch.setattr(
            _server_prompts,
            "_load_user_prompt_defs",
            lambda _folder: {
                # Missing "content" -> _register_one_user_prompt raises KeyError.
                "bad": {"description": "b", "arguments": [], "tags": []},
                "good": {
                    "description": "g",
                    "arguments": [],
                    "tags": [],
                    "content": "hello",
                },
            },
        )
        mcp = FastMCP("test")
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp._server_prompts"
        ):
            register_domain_prompts(
                mcp, templates_folder=None, prompts_folder="/whatever"
            )

        assert "User prompt 'bad' failed to register" in caplog.text
        async with Client(mcp) as client:
            names = {p.name for p in await client.list_prompts()}
        assert "good" in names
        assert "bad" not in names

    async def test_builtin_prompt_registration_failure_skips_and_errors(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A built-in whose defn is missing ``description`` (KeyError in the
        helper) is skipped at ERROR (packaging defect); other built-ins still
        register."""
        import logging

        from fastmcp import FastMCP

        from markdown_vault_mcp import _server_prompts
        from markdown_vault_mcp._server_prompts import register_prompts

        original = _server_prompts._load_builtin_prompt

        def _fake_load(name: str) -> dict[str, object] | None:
            if name == "summarize":
                # Missing "description" -> _register_one_builtin_prompt raises.
                return {"arguments": [], "tags": [], "icons": "", "content": "x"}
            return original(name)

        monkeypatch.setattr(_server_prompts, "_load_builtin_prompt", _fake_load)
        mcp = FastMCP("test")
        with caplog.at_level(
            logging.ERROR, logger="markdown_vault_mcp._server_prompts"
        ):
            register_prompts(mcp)

        assert "Built-in prompt 'summarize' failed to register" in caplog.text
        assert "packaging defect" in caplog.text
        async with Client(mcp) as client:
            names = {p.name for p in await client.list_prompts()}
        assert "summarize" not in names
        # A sibling built-in loaded normally is still registered.
        assert "related" in names

    async def test_guard_does_not_mask_helper_valueerror(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The helper's own narrow ``except ValueError`` (invalid signature) still
        fires its specific WARNING and returns — the loop backstop does not
        double-log a generic 'failed to register'."""
        import logging

        from fastmcp import FastMCP

        from markdown_vault_mcp import _server_prompts
        from markdown_vault_mcp._server_prompts import register_domain_prompts

        monkeypatch.setattr(
            _server_prompts,
            "_load_user_prompt_defs",
            lambda _folder: {
                # Duplicate arg name -> _build_prompt_fn raises ValueError, caught
                # by the helper's own handler (not the loop backstop).
                "dupe": {
                    "description": "d",
                    "arguments": [
                        {"name": "x", "required": True},
                        {"name": "x", "required": True},
                    ],
                    "tags": [],
                    "content": "$x",
                },
                "good2": {
                    "description": "g",
                    "arguments": [],
                    "tags": [],
                    "content": "hello",
                },
            },
        )
        mcp = FastMCP("test")
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp._server_prompts"
        ):
            register_domain_prompts(
                mcp, templates_folder=None, prompts_folder="/whatever"
            )

        assert "cannot form a valid signature" in caplog.text
        assert "failed to register" not in caplog.text
        async with Client(mcp) as client:
            names = {p.name for p in await client.list_prompts()}
        assert "good2" in names
        assert "dupe" not in names

    async def test_user_prompt_mcp_rejection_skips_and_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A ``mcp.prompt(...)`` rejection at decoration time (raised *outside*
        the helper's narrow ``except ValueError``) is caught by the loop
        backstop: the user prompt is skipped with a WARNING; siblings register."""
        import logging

        from fastmcp import FastMCP

        from markdown_vault_mcp import _server_prompts
        from markdown_vault_mcp._server_prompts import register_domain_prompts

        original_build = _server_prompts._build_prompt_fn

        def _reject_marked(
            template: str, arg_defs: list, derive: object = None
        ) -> object:
            # A prompt whose first argument is named "reject" gets a **kwargs
            # callable, which FastMCP rejects at mcp.prompt() decoration time.
            if arg_defs and arg_defs[0].get("name") == "reject":

                def _bad(**_kwargs: object) -> str:
                    return template

                return _bad
            return original_build(template, arg_defs, derive)

        monkeypatch.setattr(_server_prompts, "_build_prompt_fn", _reject_marked)
        # Kill Pass-2 built-in noise so only the user prompts are exercised.
        monkeypatch.setattr(_server_prompts, "_load_builtin_prompt", lambda _name: None)
        monkeypatch.setattr(
            _server_prompts,
            "_load_user_prompt_defs",
            lambda _folder: {
                "bad": {
                    "description": "b",
                    "arguments": [{"name": "reject", "required": True}],
                    "tags": [],
                    "content": "$reject",
                },
                "good": {
                    "description": "g",
                    "arguments": [],
                    "tags": [],
                    "content": "hello",
                },
            },
        )
        mcp = FastMCP("test")
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp._server_prompts"
        ):
            register_domain_prompts(
                mcp, templates_folder=None, prompts_folder="/whatever"
            )

        assert "User prompt 'bad' failed to register" in caplog.text
        async with Client(mcp) as client:
            names = {p.name for p in await client.list_prompts()}
        assert "good" in names
        assert "bad" not in names

    async def test_builtin_prompt_mcp_rejection_skips_and_errors(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A ``mcp.prompt(...)`` rejection for a built-in (raised *outside* the
        helper's narrow ``except ValueError``) is caught by the loop backstop at
        ERROR; other built-ins still register."""
        import logging

        from fastmcp import FastMCP

        from markdown_vault_mcp import _server_prompts
        from markdown_vault_mcp._server_prompts import register_prompts

        original_build = _server_prompts._build_prompt_fn
        original_load = _server_prompts._load_builtin_prompt

        def _reject_marked(
            template: str, arg_defs: list, derive: object = None
        ) -> object:
            if arg_defs and arg_defs[0].get("name") == "reject":

                def _bad(**_kwargs: object) -> str:
                    return template

                return _bad
            return original_build(template, arg_defs, derive)

        def _load_with_bad_summarize(name: str) -> dict | None:
            if name == "summarize":
                return {
                    "description": "s",
                    "arguments": [{"name": "reject", "required": True}],
                    "tags": [],
                    "icons": "",
                    "content": "$reject",
                }
            return original_load(name)

        monkeypatch.setattr(_server_prompts, "_build_prompt_fn", _reject_marked)
        monkeypatch.setattr(
            _server_prompts, "_load_builtin_prompt", _load_with_bad_summarize
        )
        mcp = FastMCP("test")
        with caplog.at_level(
            logging.ERROR, logger="markdown_vault_mcp._server_prompts"
        ):
            register_prompts(mcp)

        assert "Built-in prompt 'summarize' failed to register" in caplog.text
        assert "packaging defect" in caplog.text
        async with Client(mcp) as client:
            names = {p.name for p in await client.list_prompts()}
        assert "summarize" not in names
        # A sibling built-in (registered via the real _build_prompt_fn) survives.
        assert "related" in names
