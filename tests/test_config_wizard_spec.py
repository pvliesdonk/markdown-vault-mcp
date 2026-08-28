"""Drift cross-check + schema validity for the docs config-wizard spec.

The wizard's env-var knowledge lives in
``docs/javascripts/config-wizard/wizard-spec.json``. This module enumerates the
canonical env-var inventory the codebase actually reads and enforces two-way
containment so the spec cannot silently drift from the configuration surface
(see the design spec
``docs/superpowers/specs/2026-06-16-config-generator-design.md``).

Inventory tiers:

* **domain** -- every ``MARKDOWN_VAULT_MCP_*`` (and known-bare) env read across
  the ``markdown_vault_mcp`` package, covering both the config layer
  (``config_sections``/``config.py``) and the server/CLI layer.
* **server** -- the upstream ``fastmcp_pvl_core.ServerConfig`` source, resolved
  via :func:`inspect.getsourcefile` so it tracks the installed version.
* **framework** -- ``FASTMCP_*`` vars read by FastMCP itself, not by our code.
* **extra** -- vars read through runtime indirection the static extractor
  cannot resolve (documented individually in ``EXTRA_KNOWN_VARS``).

The extractor recognizes three read forms: the ``env``/``env_int``/``env_float``
/``opt_int`` helper family (alias-resolved per file), full-literal
``os.environ.get("MARKDOWN_VAULT_MCP_X")``, and prefix f-strings
``os.environ.get(f"{_ENV_PREFIX}_X")``.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
from pathlib import Path

from fastmcp_pvl_core import (
    JobsConfig,
    ServerConfig,
    TransferConfig,
    finalize_instructions,
)

import markdown_vault_mcp

PREFIX = "MARKDOWN_VAULT_MCP"

# Base names of the env-reading helpers (config_sections/_helpers.py). Files may
# import them directly or aliased (the extractor resolves aliases per file);
# config.py now imports ``env`` without an alias (#767).
_HELPER_BASENAMES = {"env", "env_int", "env_float", "opt_int", "env_weight_map"}

# Local names that stand in for the env prefix inside f-strings such as
# ``os.environ.get(f"{_ENV_PREFIX}_HTTP_PATH")``.
_PREFIX_NAMES = {"_ENV_PREFIX", "ENV_PREFIX", "PREFIX", "_PREFIX", "prefix"}

# Known bare (unprefixed) ecosystem-standard names read via os.environ.
_BARE_NAMES = {
    "OLLAMA_HOST",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_EMBEDDING_MODEL",
    "VOYAGE_API_KEY",
}

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "docs/javascripts/config-wizard/wizard-spec.json"
GENERATORS_JS_PATH = REPO_ROOT / "docs/javascripts/config-wizard/generators.js"

# Env-var names hardcoded in generators.js that are NOT part of the config
# inventory: FASTMCP_HOME is injected into the Docker container's environment to
# point FastMCP's state at the mounted volume; it is owned by FastMCP, not read
# by any config code here.
GENERATOR_INJECTED = {"FASTMCP_HOME"}

# FASTMCP_* runtime/logging vars: read by FastMCP itself, not by any config code
# we own. The only hand-listed framework tier; kept tiny on purpose.
FRAMEWORK_VARS = {
    "FASTMCP_LOG_LEVEL",
    "FASTMCP_ENABLE_RICH_LOGGING",
}

# Vars read through runtime indirection the static extractor cannot resolve:
# ``config_sections/indexing.py`` reads these via a local ``_path(name)`` helper
# that calls ``env(prefix, name)`` where ``name`` is a runtime variable, so the
# suffix literal never appears directly in an ``env(prefix, "LITERAL")`` call.
EXTRA_KNOWN_VARS = {
    f"{PREFIX}_INDEX_PATH",
    f"{PREFIX}_STATE_PATH",
    f"{PREFIX}_EMBEDDINGS_PATH",
}

_VALID_TYPES = {"select", "text", "bool", "number"}


def _package_source_files() -> list[Path]:
    """All Python source files in the ``markdown_vault_mcp`` package."""
    pkg_dir = Path(markdown_vault_mcp.__file__).parent
    return sorted(pkg_dir.rglob("*.py"))


def _server_config_source_file() -> Path:
    """Resolve the installed ``ServerConfig`` source file (server tier)."""
    src = inspect.getsourcefile(ServerConfig)
    assert src is not None, "ServerConfig source file not found"
    return Path(src)


def _transfer_config_source_file() -> Path:
    """Resolve the installed ``TransferConfig`` source file (server tier).

    ``TransferConfig`` is a pvl-core-owned section composed into
    :class:`ProjectConfig` (#979); its env reads live in pvl-core's own source,
    so it is resolved the same way as ``ServerConfig`` rather than scanned by the
    domain package walk.
    """
    src = inspect.getsourcefile(TransferConfig)
    assert src is not None, "TransferConfig source file not found"
    return Path(src)


def _jobs_config_source_file() -> Path:
    """Resolve the installed ``JobsConfig`` source file (server tier).

    ``JobsConfig`` is a pvl-core-owned section composed into
    :class:`ProjectConfig` (#1033), resolved the same way as
    ``TransferConfig`` above.
    """
    src = inspect.getsourcefile(JobsConfig)
    assert src is not None, "JobsConfig source file not found"
    return Path(src)


def _helper_alias_map(tree: ast.AST) -> dict[str, str]:
    """Map each locally-bound name to its helper base name for this source.

    Handles ``from ... import env`` (binds ``env``) and
    ``from ... import env as _env`` (binds ``_env``). Imports inside function
    bodies are included (``ast.walk`` is exhaustive).
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in _HELPER_BASENAMES:
                    aliases[alias.asname or alias.name] = alias.name
    return aliases


def _string_arg(node: ast.expr) -> str | None:
    """Return the value of a string-constant node, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _resolve_fstring_env(node: ast.JoinedStr) -> str | None:
    """Reconstruct ``f"{_ENV_PREFIX}_SUFFIX"`` -> ``MARKDOWN_VAULT_MCP_SUFFIX``.

    Returns None if any interpolation is not a recognized prefix name (so the
    f-string cannot be resolved to a concrete env-var name statically).
    """
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        elif isinstance(value, ast.FormattedValue):
            inner = value.value
            if isinstance(inner, ast.Name) and inner.id in _PREFIX_NAMES:
                parts.append(PREFIX)
            else:
                return None
        else:
            return None
    result = "".join(parts)
    return result if result.startswith(PREFIX) else None


def extract_env_vars_from_source(source: str) -> set[str]:
    """Extract env-var names referenced in a Python source string.

    Recognizes the ``env``/``env_int``/``env_float``/``opt_int`` helper family
    (alias-resolved; prefixed -> ``MARKDOWN_VAULT_MCP_SUFFIX``), full-literal
    ``os.environ.get("NAME")``, and prefix f-strings
    ``os.environ.get(f"{_ENV_PREFIX}_SUFFIX")``.
    """
    tree = ast.parse(source)
    helper_names = set(_helper_alias_map(tree))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # env(prefix, "SUFFIX", ...) family (alias-resolved).
        if (
            isinstance(func, ast.Name)
            and func.id in helper_names
            and len(node.args) >= 2
        ):
            suffix = _string_arg(node.args[1])
            if suffix is not None:
                found.add(f"{PREFIX}_{suffix}")
        # os.environ.get("NAME") / os.environ.get(f"{_ENV_PREFIX}_X").
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "environ"
            and node.args
        ):
            target = node.args[0]
            lit = _string_arg(target)
            if lit is not None:
                found.add(lit)
            elif isinstance(target, ast.JoinedStr):
                resolved = _resolve_fstring_env(target)
                if resolved is not None:
                    found.add(resolved)
    return found


def _filter_config_vars(names: set[str]) -> set[str]:
    """Keep only prefixed or known-bare names (drop unrelated env reads)."""
    return {n for n in names if n.startswith(PREFIX) or n in _BARE_NAMES}


def domain_inventory() -> set[str]:
    out: set[str] = set()
    for path in _package_source_files():
        out |= extract_env_vars_from_source(path.read_text(encoding="utf-8"))
    return _filter_config_vars(out)


def server_inventory() -> set[str]:
    src = _server_config_source_file().read_text(encoding="utf-8")
    return _filter_config_vars(extract_env_vars_from_source(src))


def _instructions_source_file() -> Path:
    """Resolve pvl-core's instructions module (server tier).

    ``INSTRUCTIONS`` and ``INSTRUCTIONS_EXTRA`` are read by
    :func:`fastmcp_pvl_core.finalize_instructions`, which lives outside
    ``ServerConfig``'s own module — so scanning ``ServerConfig`` alone misses
    them. Scanning the module that actually reads them keeps the inventory
    true if pvl-core renames either var, which a hardcoded allowlist would not.
    """
    src = inspect.getsourcefile(finalize_instructions)
    assert src is not None, "finalize_instructions source file not found"
    return Path(src)


def instructions_inventory() -> set[str]:
    src = _instructions_source_file().read_text(encoding="utf-8")
    return _filter_config_vars(extract_env_vars_from_source(src))


def transfer_inventory() -> set[str]:
    src = _transfer_config_source_file().read_text(encoding="utf-8")
    return _filter_config_vars(extract_env_vars_from_source(src))


def jobs_inventory() -> set[str]:
    src = _jobs_config_source_file().read_text(encoding="utf-8")
    return _filter_config_vars(extract_env_vars_from_source(src))


def full_inventory() -> set[str]:
    return (
        domain_inventory()
        | server_inventory()
        | instructions_inventory()
        | transfer_inventory()
        | jobs_inventory()
        | FRAMEWORK_VARS
        | EXTRA_KNOWN_VARS
    )


def load_spec() -> dict:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def spec_emitted_vars(spec: dict) -> set[str]:
    """All env vars the spec can emit: question ``var``s + option ``emit`` maps."""
    out: set[str] = set()
    for q in spec["questions"]:
        if "var" in q:
            out.add(q["var"])
        for opt in q.get("options", []):
            out.update(opt.get("emit", {}).keys())
    return out


# Canonical inventory vars deliberately NOT surfaced as wizard questions.
# Each entry MUST stay a real inventory var (a test asserts no stale entries).
NOT_IN_WIZARD = {
    # Transport/host/port are implied by the deployment target and the Docker
    # image CMD; the wizard never emits them as env vars.
    f"{PREFIX}_TRANSPORT": "implied by deployment target / image CMD",
    f"{PREFIX}_HOST": "bind host fixed per deployment (0.0.0.0 in Docker)",
    f"{PREFIX}_PORT": "default 8000; deployment-fixed",
    # Upstream ServerConfig explicit auth-mode override; the wizard derives auth
    # from credential presence (BEARER_TOKEN / OIDC_*), not an explicit selector,
    # and AUTH_MODE is undocumented in this project's configuration surface.
    f"{PREFIX}_AUTH_MODE": "upstream explicit auth-mode override; wizard derives from credentials",
    # Bare OPENAI_* fallbacks -- the wizard emits the prefixed forms instead.
    "OPENAI_BASE_URL": "bare fallback; wizard emits MARKDOWN_VAULT_MCP_OPENAI_BASE_URL",
    "OPENAI_EMBEDDING_MODEL": "bare fallback; wizard emits MARKDOWN_VAULT_MCP_OPENAI_EMBEDDING_MODEL",
    # Deprecated alias / auto-computed / alternate auth mode.
    f"{PREFIX}_EVENT_STORE_URL": "deprecated alias for KV_STORE_URL",
    f"{PREFIX}_APP_DOMAIN": "optional override; auto-computed from BASE_URL when unset",
    f"{PREFIX}_BEARER_TOKENS_FILE": "multi-token file auth; wizard covers single BEARER_TOKEN",
    f"{PREFIX}_BEARER_DEFAULT_SUBJECT": "multi-token file auth; wizard covers single BEARER_TOKEN",
}


# --------------------------------------------------------------------------- #
# Extractor / inventory tests
# --------------------------------------------------------------------------- #
def test_domain_inventory_includes_known_prefixed_var() -> None:
    assert f"{PREFIX}_GIT_TOKEN" in domain_inventory()


def test_domain_inventory_includes_bare_name_var() -> None:
    # OLLAMA_HOST / OPENAI_API_KEY are read via os.environ.get (bare).
    inv = domain_inventory()
    assert "OLLAMA_HOST" in inv
    assert "OPENAI_API_KEY" in inv


def test_domain_inventory_includes_top_level_helper_vars() -> None:
    # config.py reads these via the (un-aliased) ``env()`` helper at the top
    # level of ``from_env`` — the inventory extractor must catch plain reads.
    # (Its import-alias resolution is now exercised only by its own unit tests;
    #  no production file aliases the helper — see #767.)
    inv = domain_inventory()
    assert f"{PREFIX}_SOURCE_DIR" in inv
    assert f"{PREFIX}_READ_ONLY" in inv


def test_domain_inventory_includes_full_literal_environ_var() -> None:
    # _server_queryable.py reads os.environ.get("MARKDOWN_VAULT_MCP_BUILD_TIMEOUT_S").
    assert f"{PREFIX}_BUILD_TIMEOUT_S" in domain_inventory()


def test_domain_inventory_includes_fstring_environ_var() -> None:
    # _server_apps.py reads os.environ.get(f"{_ENV_PREFIX}_HTTP_PATH").
    assert f"{PREFIX}_HTTP_PATH" in domain_inventory()


def test_server_inventory_includes_oidc_var() -> None:
    assert f"{PREFIX}_OIDC_CLIENT_ID" in server_inventory()


# --------------------------------------------------------------------------- #
# Spec schema-validity tests
# --------------------------------------------------------------------------- #
def test_spec_questions_have_required_keys() -> None:
    spec = load_spec()
    ids: set[str] = set()
    for q in spec["questions"]:
        assert q["id"] not in ids, f"duplicate question id {q['id']}"
        ids.add(q["id"])
        assert q["label"], q["id"]
        assert q["type"] in _VALID_TYPES, q["id"]
        if q["type"] == "select":
            assert q.get("options"), f"select {q['id']} needs options"


def test_spec_secret_keys_are_emitted_vars() -> None:
    spec = load_spec()
    emitted = spec_emitted_vars(spec)
    for key in spec["secretKeys"]:
        assert key in emitted, f"secret {key} is not emitted by any question"


def test_spec_showif_references_known_question_ids() -> None:
    spec = load_spec()
    ids = {q["id"] for q in spec["questions"]}
    for q in spec["questions"]:
        for ref in q.get("showIf", {}):
            assert ref in ids, f"{q['id']} showIf references unknown {ref}"


# --------------------------------------------------------------------------- #
# Drift cross-check tests (two-way containment)
# --------------------------------------------------------------------------- #
def test_every_emitted_var_exists_in_inventory() -> None:
    """Spec emits only vars that real config code reads (catches typos/removals)."""
    inventory = full_inventory()
    unknown = spec_emitted_vars(load_spec()) - inventory
    assert not unknown, (
        f"spec emits vars not found in code inventory: {sorted(unknown)}"
    )


def test_every_inventory_var_is_wired_or_allowlisted() -> None:
    """Adding a config var without wiring the wizard fails here."""
    inventory = full_inventory()
    emitted = spec_emitted_vars(load_spec())
    uncovered = inventory - emitted - set(NOT_IN_WIZARD)
    assert not uncovered, (
        "config vars are neither in the wizard nor on the NOT_IN_WIZARD "
        f"allowlist: {sorted(uncovered)}"
    )


def test_not_in_wizard_allowlist_has_no_stale_entries() -> None:
    """Allowlist entries must remain real inventory vars."""
    inventory = full_inventory()
    stale = set(NOT_IN_WIZARD) - inventory
    assert not stale, (
        f"NOT_IN_WIZARD references vars no longer in code: {sorted(stale)}"
    )


def test_generators_js_vars_are_known() -> None:
    """Env-var names hardcoded in generators.js must be real config vars.

    Extends the drift guarantee to the output generators: a typo'd or renamed
    container-path key (or a stale FASTMCP_HOME) fails here.
    """
    source = GENERATORS_JS_PATH.read_text(encoding="utf-8")
    tokens = set(re.findall(r"\b(?:MARKDOWN_VAULT_MCP|FASTMCP)_[A-Z_]+\b", source))
    known = full_inventory() | GENERATOR_INJECTED
    unknown = tokens - known
    assert not unknown, f"generators.js references unknown env vars: {sorted(unknown)}"


def test_framework_vars_stays_small() -> None:
    """FRAMEWORK_VARS is a hand-maintained escape hatch; keep it tiny."""
    assert len(FRAMEWORK_VARS) <= 5, (
        f"FRAMEWORK_VARS grew unexpectedly: {FRAMEWORK_VARS}"
    )


def test_framework_vars_are_emitted_by_spec() -> None:
    """Each framework var must be wired into the wizard (else why list it?)."""
    emitted = spec_emitted_vars(load_spec())
    unused = FRAMEWORK_VARS - emitted
    assert not unused, (
        f"FRAMEWORK_VARS entries not emitted by the wizard: {sorted(unused)}"
    )
