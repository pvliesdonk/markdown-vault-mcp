"""The instructions builder: roles, pruning, env contract, budgets (#2305).

Ported from fastmcp-pvl-core v10.2.0 ``tests/test_instructions.py``; its
``test_old_priority_api_is_gone`` pinned pvl-core's own export history and did
not come along.
"""

from __future__ import annotations

import logging

import pytest
from fastmcp import FastMCP

from markdown_vault_mcp._instructions_builder import (
    CLAUDE_CODE_INSTRUCTIONS_LIMIT_UTF16,
    GENERATED_INSTRUCTIONS_TARGET_UTF16,
    InstructionRole,
    InstructionsBuilder,
    finalize_instructions,
    instructions_for,
    utf16_code_units,
)
from markdown_vault_mcp._tool_visibility import apply_tool_visibility
from markdown_vault_mcp.config_sections.server import ServerConfig
from markdown_vault_mcp.exceptions import ConfigurationError

ALL = frozenset({"alpha", "beta", "gamma"})


def _server(*tool_names: str) -> FastMCP:
    mcp = FastMCP("t")
    for name in tool_names:
        mcp.tool(name=name)(lambda: "x")
    return mcp


def _identity(mcp: FastMCP, name: str = "t", description: str = "Ident.") -> None:
    instructions_for(mcp).identity(name, description)


def _messages(
    caplog: pytest.LogCaptureFixture, event: str, level: int = logging.WARNING
) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "markdown_vault_mcp._instructions_builder"
        and record.levelno == level
        and record.getMessage().startswith(event)
    ]


def _finalize_with_routing_units(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    total_units: int,
) -> tuple[str, list[str]]:
    """Finalize text of an exact size whose variable portion is routing."""
    # "t: d\n\n" contributes six UTF-16 units before routing text.
    monkeypatch.setenv("MY_APP_INSTANCE_DESCRIPTION", "x" * (total_units - 6))
    mcp = _server()
    _identity(mcp, description="d")
    with caplog.at_level(
        logging.WARNING, logger="markdown_vault_mcp._instructions_builder"
    ):
        text = finalize_instructions(mcp, ServerConfig(), env_prefix="MY_APP")
    assert not _messages(caplog, "instructions_generated_budget_exceeded")
    return text, _messages(caplog, "instructions_client_budget_exceeded")


class TestRoles:
    def test_declared_order_is_the_render_order(self):
        assert list(InstructionRole) == [
            InstructionRole.IDENTITY,
            InstructionRole.ROUTING,
            InstructionRole.INSTANCE,
            InstructionRole.POLICY,
            InstructionRole.CAPABILITIES,
            InstructionRole.WORKFLOWS,
            InstructionRole.DOCUMENTATION,
        ]

    def test_roles_are_not_numeric_extension_points(self):
        with pytest.raises(TypeError):
            InstructionRole.IDENTITY + 1  # type: ignore[operator]


class TestAdd:
    def test_orders_by_role_then_insertion(self):
        builder = InstructionsBuilder()
        builder.add("workflow one", role=InstructionRole.WORKFLOWS)
        builder.identity("app", "Product")
        builder.add("capability", role=InstructionRole.CAPABILITIES)
        builder.add("instance", role=InstructionRole.INSTANCE)
        builder.add("workflow two", role=InstructionRole.WORKFLOWS)
        builder.documentation("https://example.test/llms.txt")
        assert builder._render(ALL, ALL) == (
            "app: Product\n\ninstance\n\ncapability\n\nworkflow one\n\n"
            "workflow two\n\nFull documentation for this server: "
            "https://example.test/llms.txt"
        )

    @pytest.mark.parametrize("text", ["", "   ", "\n\t"])
    def test_rejects_blank_text(self, text: str):
        with pytest.raises(ConfigurationError, match="empty"):
            InstructionsBuilder().add(text, role=InstructionRole.WORKFLOWS)

    def test_strips_surrounding_whitespace(self):
        builder = InstructionsBuilder()
        builder.add("  padded  \n", role=InstructionRole.WORKFLOWS)
        assert builder._render(ALL, ALL) == "padded"

    @pytest.mark.parametrize(
        "role",
        [
            InstructionRole.IDENTITY,
            InstructionRole.ROUTING,
            InstructionRole.POLICY,
            InstructionRole.DOCUMENTATION,
        ],
    )
    def test_general_add_rejects_reserved_roles(self, role: InstructionRole):
        with pytest.raises(ConfigurationError, match="reserved"):
            InstructionsBuilder().add("text", role=role)

    def test_general_add_rejects_non_role_values(self):
        with pytest.raises(ConfigurationError, match="invalid"):
            InstructionsBuilder().add("text", role="workflows")  # type: ignore[arg-type]

    def test_identity_is_core_shaped(self):
        builder = InstructionsBuilder()
        builder.identity(" work-vault ", " Searchable notes. ")
        assert builder._render(ALL, ALL) == "work-vault: Searchable notes."

    @pytest.mark.parametrize("value", ["", "  ", "\n"])
    @pytest.mark.parametrize("field", ["server_name", "product_description"])
    def test_identity_rejects_blank_values(self, value: str, field: str):
        kwargs = {"server_name": "app", "product_description": "Product"}
        kwargs[field] = value
        with pytest.raises(ConfigurationError, match=field):
            InstructionsBuilder().identity(**kwargs)

    @pytest.mark.parametrize("value", ["a\nb", "a\rb", "a\r\nb"])
    @pytest.mark.parametrize("field", ["server_name", "product_description"])
    def test_identity_rejects_multiline_values(self, value: str, field: str):
        kwargs = {"server_name": "app", "product_description": "Product"}
        kwargs[field] = value
        with pytest.raises(ConfigurationError, match="one line"):
            InstructionsBuilder().identity(**kwargs)

    def test_documentation_is_core_shaped_and_last(self):
        builder = InstructionsBuilder()
        builder.identity("app", "Product")
        builder.add("caps", role=InstructionRole.CAPABILITIES)
        builder.documentation("https://example.test/llms.txt")
        assert builder._render(ALL, ALL).endswith(
            "Full documentation for this server: https://example.test/llms.txt"
        )

    def test_documentation_rejects_blank_url(self):
        with pytest.raises(ConfigurationError, match="empty"):
            InstructionsBuilder().documentation("  ")


class TestPrune:
    def test_snippet_without_required_tools_is_kept(self):
        builder = InstructionsBuilder()
        builder.add("kept", role=InstructionRole.WORKFLOWS)
        assert builder._render(frozenset(), ALL) == "kept"

    def test_all_required_tools_must_be_exposed(self):
        builder = InstructionsBuilder()
        builder.add(
            "dropped",
            role=InstructionRole.WORKFLOWS,
            requires_tools={"alpha", "zeta"},
        )
        builder.add("kept", role=InstructionRole.WORKFLOWS)
        assert builder._render(ALL, ALL) == "kept"

    def _drop_message(
        self,
        caplog: pytest.LogCaptureFixture,
        required: set[str],
        exposed: frozenset[str],
        registered: frozenset[str],
    ) -> str:
        builder = InstructionsBuilder()
        builder.add(
            "dropped",
            role=InstructionRole.WORKFLOWS,
            requires_tools=required,
        )
        caplog.clear()
        with caplog.at_level(
            logging.DEBUG, logger="markdown_vault_mcp._instructions_builder"
        ):
            assert builder._render(exposed, registered) == ""
        messages = _messages(caplog, "instructions_snippet_dropped", logging.DEBUG)
        assert len(messages) == 1
        return messages[0]

    def test_drop_log_distinguishes_hidden_and_absent_tools(
        self, caplog: pytest.LogCaptureFixture
    ):
        message = self._drop_message(
            caplog,
            {"hidden", "absent"},
            ALL,
            ALL | {"hidden"},
        )
        assert "role=workflows" in message
        assert "hidden by the operator visibility rule: hidden" in message
        assert "not registered on this server: absent" in message


class TestRegistry:
    def test_same_server_same_builder(self):
        mcp = FastMCP("t")
        assert instructions_for(mcp) is instructions_for(mcp)

    def test_different_servers_different_builders(self):
        assert instructions_for(FastMCP("a")) is not instructions_for(FastMCP("b"))


def _raise_drift(_mcp: FastMCP) -> frozenset[str]:
    raise RuntimeError("component enumeration drift")


class TestFinalize:
    def test_sets_mcp_instructions_and_returns_it(self):
        mcp = _server("alpha")
        _identity(mcp, "server", "Product")
        text = finalize_instructions(mcp, ServerConfig(), env_prefix="MY_APP")
        assert text == mcp.instructions == "server: Product"

    def test_zero_identities_raise(self):
        with pytest.raises(ConfigurationError, match="found none") as exc:
            finalize_instructions(_server(), ServerConfig(), env_prefix="MY_APP")
        assert "IDENTITY fragment" in str(exc.value)
        assert "identity(server_name, product_description)" in str(exc.value)

    def test_two_identities_raise(self):
        mcp = _server()
        builder = instructions_for(mcp)
        builder.identity("one", "Product")
        builder.identity("two", "Product")
        with pytest.raises(ConfigurationError, match="found 2"):
            finalize_instructions(mcp, ServerConfig(), env_prefix="MY_APP")

    def test_prunes_operator_hidden_and_absent_tools(self):
        mcp = _server("alpha", "beta")
        builder = instructions_for(mcp)
        builder.identity("app", "Product")
        builder.add(
            "uses alpha",
            role=InstructionRole.WORKFLOWS,
            requires_tools={"alpha"},
        )
        builder.add(
            "uses beta",
            role=InstructionRole.WORKFLOWS,
            requires_tools={"beta"},
        )
        builder.add(
            "uses ghost",
            role=InstructionRole.WORKFLOWS,
            requires_tools={"ghost"},
        )
        config = ServerConfig(tools_deny=("beta",))
        apply_tool_visibility(mcp, config)
        text = finalize_instructions(mcp, config, env_prefix="MY_APP")
        assert text == "app: Product\n\nuses alpha"

    def test_prunes_tools_hidden_by_fastmcp_tag_transform(self):
        mcp = FastMCP("t")

        @mcp.tool(tags={"hidden"})
        def hidden() -> str:
            return "hidden"

        mcp.disable(tags={"hidden"}, components={"tool"})
        builder = instructions_for(mcp)
        builder.identity("app", "Product")
        builder.add(
            "uses hidden",
            role=InstructionRole.WORKFLOWS,
            requires_tools={"hidden"},
        )
        assert (
            finalize_instructions(mcp, ServerConfig(), env_prefix="MY_APP")
            == "app: Product"
        )

    def test_enumeration_failure_leaves_builder_unmutated(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("MY_APP_INSTANCE_DESCRIPTION", "routing")
        monkeypatch.setenv("MY_APP_INSTRUCTIONS_EXTRA", "policy")
        monkeypatch.setattr(
            "markdown_vault_mcp._instructions_builder.registered_tool_names",
            _raise_drift,
        )
        mcp = _server("alpha")
        builder = instructions_for(mcp)
        builder.identity("app", "Product")
        with pytest.raises(RuntimeError, match="drift"):
            finalize_instructions(mcp, ServerConfig(), env_prefix="MY_APP")
        assert [snippet.role for snippet in builder._snippets] == [
            InstructionRole.IDENTITY
        ]
        assert builder._frozen is False

    def test_visibility_conflict_leaves_builder_unmutated(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("MY_APP_INSTANCE_DESCRIPTION", "routing")
        monkeypatch.setenv("MY_APP_INSTRUCTIONS_EXTRA", "policy")
        mcp = _server()
        builder = instructions_for(mcp)
        builder.identity("app", "Product")
        config = ServerConfig(tools_allow=("a",), tools_deny=("b",))
        with pytest.raises(ConfigurationError):
            finalize_instructions(mcp, config, env_prefix="MY_APP")
        assert [snippet.role for snippet in builder._snippets] == [
            InstructionRole.IDENTITY
        ]
        assert builder._frozen is False

    def test_freezes_and_is_idempotent(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        mcp = _server()
        builder = instructions_for(mcp)
        builder.identity("app", "Product")
        first = finalize_instructions(mcp, ServerConfig(), env_prefix="MY_APP")
        with pytest.raises(RuntimeError, match="finalized"):
            builder.add("late", role=InstructionRole.WORKFLOWS)
        monkeypatch.setenv("MY_APP_INSTRUCTIONS", "changed")
        caplog.clear()
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp._instructions_builder"
        ):
            second = finalize_instructions(mcp, ServerConfig(), env_prefix="MY_APP")
        assert first == second == mcp.instructions == "app: Product"
        assert caplog.records == []


class TestEnvContract:
    def test_routing_and_policy_render_in_semantic_order(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("MY_APP_INSTANCE_DESCRIPTION", "  Work material.  ")
        monkeypatch.setenv("MY_APP_INSTRUCTIONS_EXTRA", "  Use PARA.  ")
        mcp = _server()
        builder = instructions_for(mcp)
        builder.add("workflow", role=InstructionRole.WORKFLOWS)
        builder.add("capability", role=InstructionRole.CAPABILITIES)
        builder.add("instance fact", role=InstructionRole.INSTANCE)
        builder.identity("work-vault", "Searchable notes")
        builder.documentation("https://example.test/llms.txt")
        assert finalize_instructions(mcp, ServerConfig(), env_prefix="MY_APP") == (
            "work-vault: Searchable notes\n\nWork material.\n\ninstance fact\n\n"
            "Use PARA.\n\ncapability\n\nworkflow\n\nFull documentation for "
            "this server: https://example.test/llms.txt"
        )

    @pytest.mark.parametrize("suffix", ["INSTANCE_DESCRIPTION", "INSTRUCTIONS_EXTRA"])
    @pytest.mark.parametrize("value", ["", "   ", "\n"])
    def test_whitespace_operator_value_is_unset(
        self, monkeypatch: pytest.MonkeyPatch, suffix: str, value: str
    ):
        monkeypatch.setenv(f"MY_APP_{suffix}", value)
        mcp = _server()
        _identity(mcp)
        assert finalize_instructions(mcp, ServerConfig(), env_prefix="MY_APP") == (
            "t: Ident."
        )

    def test_legacy_replaces_everything_and_warns_once(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        monkeypatch.setenv("MY_APP_INSTRUCTIONS", "Operator text.")
        mcp = _server()
        _identity(mcp)
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp._instructions_builder"
        ):
            text = finalize_instructions(mcp, ServerConfig(), env_prefix="MY_APP")
        assert text == mcp.instructions == "Operator text."
        messages = _messages(caplog, "instructions_legacy_override")
        assert len(messages) == 1
        assert "MY_APP_INSTRUCTIONS" in messages[0]
        assert "ignored=none" in messages[0]

    def test_legacy_names_every_ignored_additive_variable(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        monkeypatch.setenv("MY_APP_INSTRUCTIONS", "Operator text.")
        monkeypatch.setenv("MY_APP_INSTANCE_DESCRIPTION", "routing")
        monkeypatch.setenv("MY_APP_INSTRUCTIONS_EXTRA", "policy")
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp._instructions_builder"
        ):
            text = finalize_instructions(_server(), ServerConfig(), env_prefix="MY_APP")
        assert text == "Operator text."
        message = _messages(caplog, "instructions_legacy_override")[0]
        assert "MY_APP_INSTANCE_DESCRIPTION" in message
        assert "MY_APP_INSTRUCTIONS_EXTRA" in message

    def test_legacy_skips_identity_requirement(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MY_APP_INSTRUCTIONS", "Operator text.")
        assert (
            finalize_instructions(_server(), ServerConfig(), env_prefix="MY_APP")
            == "Operator text."
        )

    def test_prefix_trailing_underscore_normalised(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("MY_APP_INSTANCE_DESCRIPTION", "routing")
        first, second = _server(), _server()
        _identity(first)
        _identity(second)
        assert finalize_instructions(
            first, ServerConfig(), env_prefix="MY_APP"
        ) == finalize_instructions(second, ServerConfig(), env_prefix="MY_APP_")


class TestBudgets:
    def test_utf16_code_units_handles_bmp_and_astral_text(self):
        assert utf16_code_units("abc") == 3
        assert utf16_code_units("a😀b") == 4
        assert utf16_code_units("a\udcffb") == 3

    @pytest.mark.parametrize(
        ("total_units", "warns"),
        [
            (GENERATED_INSTRUCTIONS_TARGET_UTF16, False),
            (GENERATED_INSTRUCTIONS_TARGET_UTF16 + 1, True),
        ],
    )
    def test_generated_target_boundary(
        self,
        caplog: pytest.LogCaptureFixture,
        total_units: int,
        warns: bool,
    ):
        mcp = _server()
        # "t: " contributes three UTF-16 units.
        _identity(mcp, description="x" * (total_units - 3))
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp._instructions_builder"
        ):
            finalize_instructions(mcp, ServerConfig(), env_prefix="MY_APP")
        messages = _messages(caplog, "instructions_generated_budget_exceeded")
        assert bool(messages) is warns
        if warns:
            assert "phase=generated" in messages[0]
            assert "crossing_role=identity" in messages[0]
            assert f"role_units=identity:{total_units}" in messages[0]
            assert "separator_units=0" in messages[0]

    def test_final_client_limit_does_not_warn_at_exact_boundary(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        limit = CLAUDE_CODE_INSTRUCTIONS_LIMIT_UTF16
        text, messages = _finalize_with_routing_units(monkeypatch, caplog, limit)
        assert utf16_code_units(text) == limit
        assert messages == []

    def test_final_client_limit_warns_one_unit_over_with_role_breakdown(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        units = CLAUDE_CODE_INSTRUCTIONS_LIMIT_UTF16 + 1
        text, messages = _finalize_with_routing_units(monkeypatch, caplog, units)
        assert utf16_code_units(text) == units
        assert len(messages) == 1
        assert "phase=final" in messages[0]
        assert "client=claude-code" in messages[0]
        assert "crossing_role=routing" in messages[0]
        assert "role_units=identity:4,routing:2043" in messages[0]
        assert "separator_units=2" in messages[0]

    def test_legacy_over_budget_uses_synthetic_role(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        units = CLAUDE_CODE_INSTRUCTIONS_LIMIT_UTF16 + 1
        monkeypatch.setenv("MY_APP_INSTRUCTIONS", "x" * units)
        with caplog.at_level(
            logging.WARNING, logger="markdown_vault_mcp._instructions_builder"
        ):
            finalize_instructions(_server(), ServerConfig(), env_prefix="MY_APP")
        message = _messages(caplog, "instructions_client_budget_exceeded")[0]
        assert "phase=final" in message
        assert "crossing_role=legacy_override" in message
        assert f"role_units=legacy_override:{units}" in message
        assert "separator_units=0" in message
