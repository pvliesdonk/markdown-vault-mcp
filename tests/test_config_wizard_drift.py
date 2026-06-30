"""Drift guards between the config-wizard spec and the env surface it mirrors.

Two directions, both fail CI:

* Orphan — a wizard ``var`` (or option ``emit`` key) that no read site consumes.
* Coverage — a core (``ServerConfig``) or domain (``ProjectConfig``) env setting
  with no wizard ``var``/``emit``.

Runs in the main lane. On the scaffold the domain surface is empty (every
``ProjectConfig.from_env`` domain read is a commented example), so this reduces
to "the seed covers core"; downstream it checks core + the project's domain.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

from fastmcp_pvl_core import domain_env_suffixes, server_config_env_suffixes

from markdown_vault_mcp.config import ProjectConfig

_ENV_PREFIX = "MARKDOWN_VAULT_MCP"
_WIZARD_SPEC = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "javascripts"
    / "config-wizard"
    / "wizard-spec.json"
)

# Core ServerConfig suffixes with no dedicated wizard control by design.
# AUTH_MODE: ServerConfig infers the auth mode from which auth vars are set (the
# ``auth`` select is a no-var routing key — see the Config wizard section of
# CLAUDE.md), so there is no AUTH_MODE control to offer.
_COVERED_BY_INFERENCE = frozenset({"AUTH_MODE"})


def _spec() -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(_WIZARD_SPEC.read_text(encoding="utf-8")))


def _wizard_emitted_vars(spec: dict[str, Any]) -> set[str]:
    """Every env var the wizard can emit: question ``var``s + option ``emit`` keys."""
    out: set[str] = set()
    for q in spec["questions"]:
        if q.get("var"):
            out.add(q["var"])
        for opt in q.get("options", []):
            out.update((opt.get("emit") or {}).keys())
    return out


def _suffix(var: str) -> str | None:
    """The part after ``{PREFIX}_``, or None if ``var`` is not prefixed."""
    prefix = _ENV_PREFIX + "_"
    return var[len(prefix) :] if var.startswith(prefix) else None


def _surface() -> set[str]:
    """The config surface the wizard must COVER: core (ServerConfig) + domain.

    The domain half is :func:`fastmcp_pvl_core.domain_env_suffixes`, which
    AST-scans ``ProjectConfig.from_env`` and recurses into any sub-config
    sections it composes (each with its own ``from_env``) — so a decomposed
    config reports its full surface, not just the reads in ``from_env`` itself.
    Only literal ``env(prefix, "LITERAL")`` reads are visible (a renamed import,
    attribute-form call, or variable/keyword suffix is invisible), and nested
    generics expand one level — see :func:`fastmcp_pvl_core.domain_env_suffixes`
    for the exact limits. Vacuous on the scaffold (domain reads are commented
    examples), live downstream. The orphan check uses a broader read set (it
    also accepts
    scaffold-direct reads — see ``_src_text``), because the wizard may
    legitimately offer a var the scaffold reads outside ServerConfig (e.g.
    ``HTTP_PATH`` is read in ``cli.py``).
    """
    return set(server_config_env_suffixes()) | set(domain_env_suffixes(ProjectConfig))


def _src_text() -> str:
    """Concatenated text of the project's ``src/**/*.py``.

    Used by :func:`_read_in_src` to accept wizard vars read directly in the
    scaffold (e.g. ``HTTP_PATH`` in ``cli.py``) that are not
    ``ServerConfig``/``ProjectConfig`` fields and would otherwise be flagged as
    orphans.
    """
    src = Path(__file__).resolve().parent.parent / "src"
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(src.rglob("*.py")))


def _read_in_src(suffix: str, src_text: str) -> bool:
    """True if ``suffix`` appears as an env-read *token* in src, not a bare match.

    Matches the two scaffold read idioms: ``env(_ENV_PREFIX, "SUFFIX")`` (the
    quoted literal ``"SUFFIX"``) and ``os.environ.get(f"{_ENV_PREFIX}_SUFFIX")``
    (the ``_SUFFIX`` fragment). The fragment arm anchors a trailing
    word-boundary so a short suffix does not match inside a longer fragment
    (``_HOST`` must not clear via ``_HOSTNAME``). Requiring one of these — rather
    than a bare ``suffix in src_text`` substring — avoids clearing a real orphan
    just because its suffix happens to appear in an unrelated comment or
    identifier.
    """
    return (
        f'"{suffix}"' in src_text
        or re.search(rf"_{re.escape(suffix)}(?![A-Z0-9_])", src_text) is not None
    )


def _orphan_vars(spec: dict[str, Any]) -> list[str]:
    """Wizard vars/emits that resolve to no read site (excluding FASTMCP_* natives)."""
    core_domain = _surface()
    src_text = _src_text()
    out: list[str] = []
    for var in sorted(_wizard_emitted_vars(spec)):
        if var.startswith("FASTMCP_"):
            continue  # native, read by FastMCP / configure_logging_from_env
        suffix = _suffix(var)
        if suffix is None:
            # A non-prefixed var (e.g. a standard external-service var such as
            # OLLAMA_HOST / OPENAI_API_KEY read directly by a composed client,
            # not a {PREFIX}_ field) is legitimate IFF the scaffold actually
            # reads it in src — same read-site test as a prefixed var. Only flag
            # it when nothing consumes it. ``_read_in_src``'s quoted-literal arm
            # (``"VAR"``) matches the ``os.environ.get("VAR")`` idiom.
            if not _read_in_src(var, src_text):
                out.append(
                    f"{var} (not {_ENV_PREFIX}_-prefixed, not FASTMCP_*, "
                    "and not read in src)"
                )
        elif suffix not in core_domain and not _read_in_src(suffix, src_text):
            out.append(f"{var} (no read site consumes {suffix})")
    return out


def test_surface_composes_core_and_domain() -> None:
    """The coverage surface is core (``server_config_env_suffixes``) plus the
    domain half from ``domain_env_suffixes(ProjectConfig)``, which recurses into
    any sub-config sections. Guards the delegation wiring (callable on the
    rendered ``ProjectConfig``, returns a frozenset); the scanner's own behavior
    — literal vs variable reads, recursion, edges — is tested upstream in
    ``fastmcp_pvl_core``'s ``TestDomainEnvSuffixes``.
    """
    domain = domain_env_suffixes(ProjectConfig)
    assert isinstance(domain, frozenset)
    assert domain <= _surface()
    assert set(server_config_env_suffixes()) <= _surface()


def test_no_orphan_wizard_vars() -> None:
    """Every wizard var/emit resolves to a read site (or is a FASTMCP_* native)."""
    orphans = _orphan_vars(_spec())
    assert not orphans, "wizard offers vars nothing reads: " + "; ".join(orphans)


def test_orphan_guard_flags_unread_var() -> None:
    """A wizard var whose suffix no read site consumes is reported."""
    spec: dict[str, Any] = {
        "questions": [{"id": "x", "var": f"{_ENV_PREFIX}_NONSENSE_XYZ"}]
    }
    orphans = _orphan_vars(spec)
    assert any("NONSENSE_XYZ" in o for o in orphans)


def test_orphan_guard_flags_unread_non_prefixed_var() -> None:
    """A NON-prefixed var nothing in src reads is still reported as an orphan."""
    spec: dict[str, Any] = {
        "questions": [{"id": "x", "var": "TOTALLY_EXTERNAL_NEVER_READ_XYZ"}]
    }
    orphans = _orphan_vars(spec)
    assert any("TOTALLY_EXTERNAL_NEVER_READ_XYZ" in o for o in orphans)


def test_read_in_src_matches_non_prefixed_external_var() -> None:
    """``_read_in_src`` clears a bare external var read via the standard idiom.

    A composed client reads a non-prefixed env var directly (e.g.
    ``os.environ.get("OLLAMA_HOST")``); the quoted-literal arm matches it, so a
    wizard offering ``OLLAMA_HOST`` is not flagged as an orphan.
    """
    src = 'host = os.environ.get("OLLAMA_HOST") or "http://localhost:11434"'
    assert _read_in_src("OLLAMA_HOST", src) is True
    assert _read_in_src("UNRELATED_EXTERNAL_VAR", src) is False


def _missing_suffixes(surface: set[str], emitted_suffixes: set[str]) -> list[str]:
    """Surface suffixes not covered by a wizard var/emit and not inference-excepted."""
    return sorted(
        s
        for s in surface
        if s not in emitted_suffixes and s not in _COVERED_BY_INFERENCE
    )


def test_wizard_covers_full_env_surface() -> None:
    """Every core/domain setting is covered by a wizard var/emit (advanced ok).

    Hiding a setting is done with ``advancedGroup``, never by omission. A
    setting with no dedicated control by design goes in ``_COVERED_BY_INFERENCE``.
    """
    emitted_suffixes = {
        s for v in _wizard_emitted_vars(_spec()) if (s := _suffix(v)) is not None
    }
    missing = _missing_suffixes(_surface(), emitted_suffixes)
    assert not missing, (
        "env settings the server reads but the wizard does not offer "
        "(add a question/emit, or _COVERED_BY_INFERENCE if inferred by design): "
        + ", ".join(missing)
    )


def test_coverage_guard_flags_missing_suffix() -> None:
    """A surface suffix with no wizard var/emit is reported as missing."""
    assert _missing_suffixes({"VAULT_PATH"}, set()) == ["VAULT_PATH"]


def test_inference_exception_suppresses_auth_mode() -> None:
    """AUTH_MODE is in the surface and not emitted, yet not reported missing
    (the _COVERED_BY_INFERENCE exception, not coincidence, keeps it green)."""
    assert "AUTH_MODE" in _surface()
    emitted = {
        s for v in _wizard_emitted_vars(_spec()) if (s := _suffix(v)) is not None
    }
    assert "AUTH_MODE" not in emitted
    assert "AUTH_MODE" not in _missing_suffixes(_surface(), emitted)


def test_transport_covered_via_option_emit() -> None:
    """TRANSPORT is covered through the deployment option `emit`, not a question var."""
    emitted = _wizard_emitted_vars(_spec())
    assert f"{_ENV_PREFIX}_TRANSPORT" in emitted
    emitted_suffixes = {s for v in emitted if (s := _suffix(v)) is not None}
    assert "TRANSPORT" not in _missing_suffixes(_surface(), emitted_suffixes)


def test_orphan_guard_exempts_fastmcp_native() -> None:
    """A FASTMCP_* wizard var is exempt from the orphan check (read by FastMCP)."""
    assert _orphan_vars({"questions": [{"id": "x", "var": "FASTMCP_LOG_LEVEL"}]}) == []


def test_orphan_guard_accepts_scaffold_direct_read() -> None:
    """A wizard var read directly in src (not a ServerConfig/domain field) is not
    an orphan. HTTP_PATH is the case: read in cli.py, absent from the core/domain
    surface, cleared only by the ``_read_in_src`` token match.
    """
    assert "HTTP_PATH" not in _surface()
    assert _read_in_src("HTTP_PATH", _src_text())
    # The token match does not clear a suffix that is merely a coincidental
    # substring (no ``"ZZZNONSENSE"`` literal or ``_ZZZNONSENSE`` fragment).
    assert not _read_in_src("ZZZNONSENSE", _src_text())


def test_read_in_src_quoted_literal_arm() -> None:
    """The quoted-literal arm matches an ``env(prefix, "SUFFIX")`` read."""
    assert _read_in_src("WIDGET", 'x = env(_ENV_PREFIX, "WIDGET")\n')
    assert not _read_in_src("WIDGET", "x = widget_count  # WIDGET in a comment\n")


def test_read_in_src_fragment_arm_anchors_word_boundary() -> None:
    """The ``_SUFFIX`` arm matches an f-string read but not a longer fragment."""
    assert _read_in_src("HOST", 'os.environ.get(f"{_ENV_PREFIX}_HOST")\n')
    # ``_HOST`` inside ``_HOSTNAME`` must NOT clear HOST as read.
    assert not _read_in_src("HOST", 'os.environ.get(f"{_ENV_PREFIX}_HOSTNAME")\n')
