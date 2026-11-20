"""Composable MCP server instructions: the builder and the finalizer.

Ported from ``fastmcp_pvl_core._instructions`` 10.2.0 (#2305). The vault's own
snippets are in ``_instructions``; this module collects and renders them.

Instructions carry what no single tool description can carry: identity, a
documentation pointer, the capability map, cross-tool workflows, enforced
instance facts, and operator context. Core features and domain code each
add snippets to the one builder per server (the job poller, the transfer
links, ``_instructions``); :func:`finalize_instructions`
prunes snippets whose tools are absent or operator-hidden, serialises by
semantic role, applies the env contract, and sets ``FastMCP.instructions``.

Design, as written in pvl-core:
https://github.com/pvliesdonk/fastmcp-pvl-core/blob/ea6741640fdd2ace32072f71766b39476f2a2201/docs/superpowers/specs/2026-08-31-instruction-roles-budget-design.md

The tool-visibility helpers still come from pvl-core through the shim, until
#2278 moves them.
"""

from __future__ import annotations

import logging
import weakref
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from markdown_vault_mcp._env import env
from markdown_vault_mcp._pvl_core import effective_tool_names, registered_tool_names
from markdown_vault_mcp.exceptions import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fastmcp import FastMCP

    from markdown_vault_mcp._pvl_core import ServerConfig

logger = logging.getLogger(__name__)


class InstructionRole(Enum):
    """Semantic placement and ownership of one instruction fragment."""

    IDENTITY = "identity"
    ROUTING = "routing"
    INSTANCE = "instance"
    POLICY = "policy"
    CAPABILITIES = "capabilities"
    WORKFLOWS = "workflows"
    DOCUMENTATION = "documentation"


CLAUDE_CODE_INSTRUCTIONS_LIMIT_UTF16 = 2_048
GENERATED_INSTRUCTIONS_TARGET_UTF16 = 1_536

_ROLE_ORDER = {role: index for index, role in enumerate(InstructionRole)}
_CONTRIBUTOR_ROLES = frozenset(
    {
        InstructionRole.INSTANCE,
        InstructionRole.CAPABILITIES,
        InstructionRole.WORKFLOWS,
    }
)
_OPERATOR_ROLES = frozenset({InstructionRole.ROUTING, InstructionRole.POLICY})
_SEPARATOR = "\n\n"


@dataclass(frozen=True)
class _Snippet:
    text: str
    role: InstructionRole
    requires_tools: frozenset[str]
    seq: int


def utf16_code_units(text: str) -> int:
    """Return JavaScript-compatible UTF-16 code units for *text*."""
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def _drop_reason(missing: frozenset[str], registered: frozenset[str]) -> str:
    """Explain why the non-empty *missing* tool names are not exposed.

    The two cases call for different action and are not otherwise
    distinguishable from the log: a registered name was hidden by the
    operator's ``TOOLS_ALLOW`` / ``TOOLS_DENY``, while an unregistered one is
    either a tool this instance's configuration did not register — the case
    ``requires_tools=`` exists for — or a rename or typo in that declaration.
    """
    hidden = sorted(missing & registered)
    absent = sorted(missing - registered)
    parts = []
    if hidden:
        parts.append("hidden by the operator visibility rule: " + ", ".join(hidden))
    if absent:
        parts.append("not registered on this server: " + ", ".join(absent))
    return "; ".join(parts)


def _identity_error(count: int) -> str:
    """Return the error for a missing or duplicated shaped identity."""
    if count == 0:
        return (
            "instructions need one IDENTITY fragment, found none; call "
            "instructions_for(mcp).identity(server_name, product_description) once"
        )
    return (
        f"instructions allow one IDENTITY fragment, found {count}; "
        "call identity(server_name, product_description) exactly once"
    )


def _clean_one_line(value: str, *, name: str) -> str:
    """Strip and validate one non-empty, single-line identity value."""
    cleaned = value.strip()
    if not cleaned:
        raise ConfigurationError(f"instruction identity {name} is empty")
    if "\n" in cleaned or "\r" in cleaned:
        raise ConfigurationError(f"instruction identity {name} must be one line")
    return cleaned


def _join_snippets(snippets: Iterable[_Snippet]) -> str:
    """Join already ordered snippets in the model-facing plain-text shape."""
    return _SEPARATOR.join(snippet.text for snippet in snippets)


def _role_units(snippets: tuple[_Snippet, ...]) -> str:
    """Format non-empty per-role UTF-16 contributions for diagnostics."""
    totals = dict.fromkeys(InstructionRole, 0)
    for snippet in snippets:
        totals[snippet.role] += utf16_code_units(snippet.text)
    return ",".join(
        f"{role.value}:{totals[role]}" for role in InstructionRole if totals[role]
    )


def _separator_units(snippets: tuple[_Snippet, ...]) -> int:
    """Return UTF-16 units occupied by blank-line separators."""
    return max(0, len(snippets) - 1) * utf16_code_units(_SEPARATOR)


def _crossing_role(snippets: tuple[_Snippet, ...], threshold: int) -> str:
    """Return the first role whose cumulative end exceeds *threshold*."""
    cumulative = 0
    for index, snippet in enumerate(snippets):
        if index:
            cumulative += utf16_code_units(_SEPARATOR)
        cumulative += utf16_code_units(snippet.text)
        if cumulative > threshold:
            return snippet.role.value
    raise AssertionError("instruction text did not cross the supplied threshold")


def _warn_generated_budget(prefix: str, snippets: tuple[_Snippet, ...]) -> None:
    """Warn when retained non-operator guidance exceeds its family target."""
    generated = tuple(s for s in snippets if s.role not in _OPERATOR_ROLES)
    units = utf16_code_units(_join_snippets(generated))
    if units <= GENERATED_INSTRUCTIONS_TARGET_UTF16:
        return
    logger.warning(
        "instructions_generated_budget_exceeded phase=generated units=%s target=%s "
        "crossing_role=%s role_units=%s separator_units=%s env_prefix=%s",
        units,
        GENERATED_INSTRUCTIONS_TARGET_UTF16,
        _crossing_role(generated, GENERATED_INSTRUCTIONS_TARGET_UTF16),
        _role_units(generated),
        _separator_units(generated),
        prefix,
    )


def _warn_client_budget(prefix: str, text: str, snippets: tuple[_Snippet, ...]) -> None:
    """Warn when final guidance exceeds Claude Code's known boundary."""
    units = utf16_code_units(text)
    if units <= CLAUDE_CODE_INSTRUCTIONS_LIMIT_UTF16:
        return
    logger.warning(
        "instructions_client_budget_exceeded phase=final client=claude-code "
        "units=%s limit=%s crossing_role=%s role_units=%s separator_units=%s "
        "env_prefix=%s",
        units,
        CLAUDE_CODE_INSTRUCTIONS_LIMIT_UTF16,
        _crossing_role(snippets, CLAUDE_CODE_INSTRUCTIONS_LIMIT_UTF16),
        _role_units(snippets),
        _separator_units(snippets),
        prefix,
    )


def _warn_legacy_client_budget(prefix: str, text: str) -> None:
    """Warn when a legacy full replacement exceeds the client boundary."""
    units = utf16_code_units(text)
    if units <= CLAUDE_CODE_INSTRUCTIONS_LIMIT_UTF16:
        return
    logger.warning(
        "instructions_client_budget_exceeded phase=final client=claude-code "
        "units=%s limit=%s crossing_role=legacy_override "
        "role_units=%s separator_units=0 env_prefix=%s",
        units,
        CLAUDE_CODE_INSTRUCTIONS_LIMIT_UTF16,
        f"legacy_override:{units}",
        prefix,
    )


class InstructionsBuilder:
    """Ordered, tool-aware collection of instruction snippets for one server.

    Obtain it with :func:`instructions_for`; do not construct one per call
    site. Every ``add`` is a plain string with a semantic role and the tool
    names it requires. Rendering happens once, in :func:`finalize_instructions`.
    """

    def __init__(self) -> None:
        self._snippets: list[_Snippet] = []
        self._frozen = False
        self._result: str | None = None

    def _append(
        self,
        text: str,
        *,
        role: InstructionRole,
        requires_tools: Iterable[str] = (),
    ) -> None:
        """Validate and append one fragment, including reserved roles."""
        if self._frozen:
            raise RuntimeError(
                "instructions already finalized; "
                "add snippets before finalize_instructions"
            )
        cleaned = text.strip()
        if not cleaned:
            raise ConfigurationError("instruction snippet text is empty")
        self._snippets.append(
            _Snippet(
                cleaned,
                role,
                frozenset(requires_tools),
                len(self._snippets),
            )
        )

    def add(
        self,
        text: str,
        *,
        role: InstructionRole,
        requires_tools: Iterable[str] = (),
    ) -> None:
        """Add one snippet.

        Args:
            text: Model-facing prose. Surrounding whitespace is stripped.
            role: Contributor-owned semantic placement. Only ``INSTANCE``,
                ``CAPABILITIES``, and ``WORKFLOWS`` are accepted here.
            requires_tools: Tool names required by the snippet. If any is
                absent or hidden by the operator visibility rule at finalize,
                the whole snippet is dropped.

        Raises:
            ConfigurationError: *text* is empty or *role* is reserved.
            RuntimeError: The builder was already finalized.
        """
        if not isinstance(role, InstructionRole):
            raise ConfigurationError(f"invalid instruction role: {role!r}")
        if role not in _CONTRIBUTOR_ROLES:
            raise ConfigurationError(
                f"instruction role {role.value} is reserved; use its shaped API"
            )
        self._append(text, role=role, requires_tools=requires_tools)

    def identity(self, server_name: str, product_description: str) -> None:
        """Add the one-line deployment and product identity.

        Args:
            server_name: Configured FastMCP server name.
            product_description: Concise description shared by the product.
        """
        name = _clean_one_line(server_name, name="server_name")
        description = _clean_one_line(product_description, name="product_description")
        self._append(f"{name}: {description}", role=InstructionRole.IDENTITY)

    def documentation(self, url: str) -> None:
        """Add the documentation pointer in its fixed shape.

        Role: ``DOCUMENTATION``.
        """
        cleaned = url.strip()
        if not cleaned:
            raise ConfigurationError("documentation url is empty")
        self._append(
            f"Full documentation for this server: {cleaned}",
            role=InstructionRole.DOCUMENTATION,
        )

    def _retained(
        self,
        exposed: frozenset[str],
        registered: frozenset[str],
        additional: Iterable[_Snippet] = (),
    ) -> tuple[_Snippet, ...]:
        """Prune against *exposed* tool names and order retained snippets.

        *registered* is every tool name on the server before the operator
        rule; it does not change which snippets are dropped, only how the
        ``DEBUG`` line explains a drop. No env or identity check.
        """
        kept: list[_Snippet] = []
        for snippet in (*self._snippets, *additional):
            missing = snippet.requires_tools - exposed
            if missing:
                logger.debug(
                    "instructions_snippet_dropped role=%s reason=%s",
                    snippet.role.value,
                    _drop_reason(missing, registered),
                )
                continue
            kept.append(snippet)
        kept.sort(key=lambda snippet: (_ROLE_ORDER[snippet.role], snippet.seq))
        return tuple(kept)

    def _render(self, exposed: frozenset[str], registered: frozenset[str]) -> str:
        """Prune, order, and serialize builder-owned snippets."""
        return _join_snippets(self._retained(exposed, registered))


_builders: weakref.WeakKeyDictionary[FastMCP, InstructionsBuilder] = (
    weakref.WeakKeyDictionary()
)


def instructions_for(mcp: FastMCP) -> InstructionsBuilder:
    """Return the builder for *mcp*, creating it on first use.

    One builder per server instance; ``register_*`` helpers and the vault's
    snippets reach it through the ``mcp`` they already hold, so no helper grows
    a kwarg.
    """
    builder = _builders.get(mcp)
    if builder is None:
        builder = InstructionsBuilder()
        _builders[mcp] = builder
    return builder


def _legacy_instructions(prefix: str, legacy: str, routing: str, policy: str) -> str:
    """Apply legacy replacement diagnostics and return its verbatim text."""
    ignored = [
        key
        for key, value in (
            (f"{prefix}_INSTANCE_DESCRIPTION", routing),
            (f"{prefix}_INSTRUCTIONS_EXTRA", policy),
        )
        if value
    ]
    logger.warning(
        "instructions_legacy_override env_var=%s deprecated=true ignored=%s",
        f"{prefix}_INSTRUCTIONS",
        ",".join(ignored) if ignored else "none",
    )
    _warn_legacy_client_budget(prefix, legacy)
    return legacy


def _operator_snippets(
    builder: InstructionsBuilder, routing: str, policy: str
) -> tuple[_Snippet, ...]:
    """Build finalizer-owned routing and policy fragments without mutation."""
    snippets: list[_Snippet] = []
    for role, text in (
        (InstructionRole.ROUTING, routing),
        (InstructionRole.POLICY, policy),
    ):
        if text:
            snippets.append(
                _Snippet(
                    text,
                    role,
                    frozenset(),
                    len(builder._snippets) + len(snippets),
                )
            )
    return tuple(snippets)


def _instruction_availability(
    mcp: FastMCP,
    config: ServerConfig,
    builder: InstructionsBuilder,
) -> tuple[frozenset[str], frozenset[str]]:
    """Validate identity and obtain effective plus registered tool names."""
    identities = [
        snippet
        for snippet in builder._snippets
        if snippet.role is InstructionRole.IDENTITY
    ]
    if len(identities) != 1:
        raise ConfigurationError(_identity_error(len(identities)))

    exposed = effective_tool_names(mcp, config)
    registered = registered_tool_names(mcp)
    return exposed, registered


def _generated_instructions(
    mcp: FastMCP,
    config: ServerConfig,
    builder: InstructionsBuilder,
    prefix: str,
) -> str:
    """Build, render, and budget the non-legacy instruction path."""
    exposed, registered = _instruction_availability(mcp, config, builder)
    routing = env(prefix, "INSTANCE_DESCRIPTION") or ""
    policy = env(prefix, "INSTRUCTIONS_EXTRA") or ""
    operator_snippets = _operator_snippets(builder, routing, policy)
    retained = builder._retained(exposed, registered, operator_snippets)
    text = _join_snippets(retained)
    _warn_generated_budget(prefix, retained)
    _warn_client_budget(prefix, text, retained)
    return text


def finalize_instructions(
    mcp: FastMCP, config: ServerConfig, *, env_prefix: str
) -> str:
    """Render the server's instructions once, apply the env contract, and set them.

    Call after :func:`apply_tool_visibility`, and after every ``register_*``
    helper and domain contribution — ``finalize_instructions`` must run last,
    after every tool registration and after :func:`apply_tool_visibility`.
    It runs during synchronous server construction, before entering an event
    loop, because FastMCP's authoritative tool listing path is asynchronous.
    Under that precondition the pruned set reflects FastMCP's global provider,
    mount, namespace, and ordered visibility transforms. Tools registered after
    finalize are not seen. Per-session transforms and per-subject authorization
    are separately out of scope because one static instruction string cannot
    vary by request.

    If ``{P}_INSTRUCTIONS`` is set (non-whitespace), the numbered steps below
    are skipped entirely and its value is used verbatim — no identity
    requirement, no pruning. The numbered list describes the non-legacy path.

    Order of operations:

    1. exactly one shaped ``IDENTITY`` fragment must exist; it carries no tool
       dependencies and cannot be pruned
    2. exposed tools = :func:`effective_tool_names`, using FastMCP's global
       listing path, plus :func:`registered_tool_names` for drop diagnostics —
       both computed before rendering so a failure leaves the builder mutable
    3. ``{P}_INSTANCE_DESCRIPTION`` and ``{P}_INSTRUCTIONS_EXTRA`` become
       ``ROUTING`` and ``POLICY`` fragments; legacy ``{P}_INSTRUCTIONS``
       replaces the whole text with one deprecation ``WARNING``
    4. drop every snippet whose ``requires_tools`` are not all exposed — one
       ``DEBUG`` per drop, naming the missing tools and saying of each
       whether it was operator-hidden or never registered
    5. serialise by semantic role then insertion order, blank-line separated
    6. measure generated and final UTF-16 units and warn above known budgets
    7. set ``mcp.instructions``, cache, freeze the builder

    A second call returns the cached string without re-reading env or
    logging.

    Args:
        mcp: The server whose builder to finalize.
        config: Universal server config; only ``tools_allow`` / ``tools_deny``
            are read.
        env_prefix: Env-var prefix, with or without a trailing underscore.

    Returns:
        The final instructions string, also set on ``mcp.instructions``.

    Raises:
        ConfigurationError: No ``IDENTITY`` fragment, more than one, or both
            visibility lists set.
        RuntimeError: FastMCP's component enumeration changed shape.
            Also raised when called from an active event loop.
    """
    builder = instructions_for(mcp)
    if builder._result is not None:
        return builder._result

    prefix = env_prefix.rstrip("_")
    legacy = env(prefix, "INSTRUCTIONS") or ""

    if legacy:
        routing = env(prefix, "INSTANCE_DESCRIPTION") or ""
        policy = env(prefix, "INSTRUCTIONS_EXTRA") or ""
        text = _legacy_instructions(prefix, legacy, routing, policy)
    else:
        text = _generated_instructions(mcp, config, builder, prefix)

    mcp.instructions = text
    builder._result = text
    builder._frozen = True
    return text
