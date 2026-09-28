"""Guards for the template-owned `config.py` customization contract.

`config.py` is template-owned (re-rendered on every `copier update`, not in
`_skip_if_exists`).  Its three domain sentinels — `CONFIG-FIELDS`,
`CONFIG-FROM-ENV`, `CONFIG-VALIDATE` — are the ONLY copier-safe places for
domain content; anything a project puts outside them lands in template-owned
prose and is at the mercy of the next merge.

These tests assert the contract those sentinels promise, so a template edit
that silently breaks it fails here rather than in every downstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastmcp_pvl_core import ConfigurationError, domain_env_surface
from typer.testing import CliRunner

from markdown_vault_mcp import config as config_module
from markdown_vault_mcp.cli import app
from markdown_vault_mcp.config import ProjectConfig


def _preset_contract_env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Set every variable the project's ``config_contract_env`` returns.

    The fixture in the project's ``tests/conftest.py`` supplies values for
    the variables its ``from_env`` reads with ``env(..., required=True)``.
    Resolved via ``getfixturevalue`` so a ``conftest.py`` that predates the
    fixture keeps passing with nothing preset.  Repeated verbatim in every
    template-owned test that builds from the environment rather than
    imported: a sibling import only resolves when ``tests/`` is not a package.
    """
    try:
        env = request.getfixturevalue("config_contract_env")
    except pytest.FixtureLookupError:
        return
    for key, value in dict(env).items():
        monkeypatch.setenv(key, value)


def _config_text() -> str:
    """The rendered `config.py` source — the sentinels live in the file, not the AST."""
    assert config_module.__file__ is not None
    return Path(config_module.__file__).read_text(encoding="utf-8")


def test_all_three_domain_sentinels_are_present() -> None:
    """Each sentinel is a matched START/END pair, exactly once.

    A dropped fence is worse than a dropped feature: copier's 3-way merge has
    nothing to anchor on, so a downstream's domain fields land in a conflict
    (or get silently overwritten) on the next update.
    """
    text = _config_text()
    for name in ("CONFIG-FIELDS", "CONFIG-FROM-ENV", "CONFIG-VALIDATE"):
        assert text.count(f"# {name}-START") == 1, f"{name}-START fence missing"
        assert text.count(f"# {name}-END") == 1, f"{name}-END fence missing"


def test_validate_sentinel_lives_inside_post_init() -> None:
    """The CONFIG-VALIDATE block must sit in `__post_init__`, not `from_env`.

    Placement is the whole point of the seam (#241): `from_env` would only
    cover the env path, leaving a direct `ProjectConfig(field=...)`
    unvalidated. Assert the block falls between the `__post_init__` def and
    the `from_env` def that follows it.
    """
    text = _config_text()
    post_init = text.index("def __post_init__")
    start = text.index("# CONFIG-VALIDATE-START")
    end = text.index("# CONFIG-VALIDATE-END")
    from_env = text.index("def from_env")
    assert post_init < start < end < from_env


def test_post_init_runs_on_both_construction_paths(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation added to CONFIG-VALIDATE fires for direct construction AND from_env.

    This is what `env_float` / `env_int` bounds cannot do — they check only the
    env-sourced value, so `ProjectConfig(field=<bad default>)` slips past them.
    A subclass standing in for a domain's filled-in CONFIG-VALIDATE block
    proves the dataclass actually dispatches to `__post_init__` on both paths.
    """
    _preset_contract_env(request, monkeypatch)
    calls: list[str] = []

    @dataclass(frozen=True)
    class _Validated(ProjectConfig):
        def __post_init__(self) -> None:
            calls.append("post_init")

    _Validated()
    assert calls == ["post_init"], "direct construction did not run __post_init__"

    _Validated.from_env()
    assert calls == ["post_init", "post_init"], "from_env did not run __post_init__"


def test_post_init_can_reject_a_value(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raise inside the seam propagates out of both construction paths."""
    _preset_contract_env(request, monkeypatch)

    @dataclass(frozen=True)
    class _Rejecting(ProjectConfig):
        def __post_init__(self) -> None:
            raise ConfigurationError("domain invariant violated")

    for construct in (_Rejecting, _Rejecting.from_env):
        try:
            construct()
        except ConfigurationError as exc:
            assert "domain invariant violated" in str(exc)
        else:  # pragma: no cover - the raise above always fires
            raise AssertionError(
                f"{construct} did not propagate the ConfigurationError"
            )


def test_every_required_var_is_supplied_by_config_contract_env(
    request: pytest.FixtureRequest,
) -> None:
    """Each var `from_env` reads with `required=True` has a test value.

    Template-owned tests build the config from the environment after
    applying that fixture; a required var missing from the
    project's `config_contract_env` would otherwise surface as setup errors
    scattered across unrelated test files.
    """
    required = {
        f"{config_module._ENV_PREFIX}_{var.suffix}"
        for var in domain_env_surface(ProjectConfig)
        if var.required
    }
    try:
        supplied = set(dict(request.getfixturevalue("config_contract_env")))
    except pytest.FixtureLookupError:
        supplied = set()
    missing = sorted(required - supplied)
    assert not missing, (
        f"Return {missing} from the `config_contract_env` fixture in "
        "tests/conftest.py, with values a test can build the server with."
    )


def test_validate_block_raises_configuration_error_not_value_error() -> None:
    """Live code in the CONFIG-VALIDATE block must not ``raise ValueError``.

    ``serve`` turns only ``ConfigurationError`` into its one-line
    ``ERROR: configuration error:`` exit; a ``ValueError`` from
    ``__post_init__`` escapes as a full Rich traceback (#692).  Commented
    lines are skipped, so the block's own examples do not count.
    """
    text = _config_text()
    start = text.index("# CONFIG-VALIDATE-START")
    end = text.index("# CONFIG-VALIDATE-END")
    offenders = [
        line.strip()
        for line in text[start:end].splitlines()
        if line.lstrip().startswith("raise ValueError")
    ]
    assert not offenders, (
        "CONFIG-VALIDATE raises ValueError, which `serve` reports as a "
        "traceback; raise fastmcp_pvl_core.ConfigurationError instead: "
        f"{offenders}"
    )


def test_serve_reports_a_validation_failure_in_one_line(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``ConfigurationError`` from ``__post_init__`` takes serve's one-line exit.

    This is the path the CONFIG-VALIDATE seam documents: one ``ERROR:`` line
    on stderr and exit 1, not Typer's Rich traceback (#616, #692).
    """
    _preset_contract_env(request, monkeypatch)

    @dataclass(frozen=True)
    class _Rejecting(ProjectConfig):
        def __post_init__(self) -> None:
            raise ConfigurationError("domain invariant violated")

    monkeypatch.setattr(
        "markdown_vault_mcp.cli.ProjectConfig",
        _Rejecting,
    )

    result = CliRunner().invoke(app, ["serve"])

    assert result.exit_code == 1, result.output
    assert "ERROR: configuration error: domain invariant violated" in result.output
    assert "Traceback" not in result.output
    assert "\u2502" not in result.output, "Rich traceback frame detected"


def test_config_is_frozen_so_validation_must_not_assign() -> None:
    """The docstring tells domains to read, not assign — verify that's true.

    If `ProjectConfig` ever stops being frozen, the `object.__setattr__`
    guidance in the seam's docstring becomes misleading and should change with
    it.
    """
    config = ProjectConfig()
    try:
        config.server = None  # type: ignore[misc, assignment]
    except AttributeError:
        pass
    else:  # pragma: no cover - frozen dataclasses always raise here
        raise AssertionError("ProjectConfig is no longer frozen")


def test_server_name_is_declared_exactly_once() -> None:
    """No second `server_name` annotation anywhere in the class body.

    Position alone is not enough: a redeclaration *after* `CONFIG-FIELDS-END`
    is still inside the class body and still wins, because Python does not
    error on a repeated annotation — the later one silently replaces the
    earlier.  Counting catches that, and catches a redeclaration inside the
    sentinels too, so it is the assertion that actually holds the contract.
    """
    annotations = [
        line
        for line in _config_text().splitlines()
        if line.strip().startswith("server_name:")
    ]
    assert len(annotations) == 1, (
        "server_name must be declared exactly once — a second annotation "
        f"silently shadows the template's, found: {annotations}"
    )


def test_server_name_is_template_owned_and_outside_the_field_sentinels() -> None:
    """`server_name` belongs to the scaffold, not to a domain block.

    `server.py` uses it for both `FastMCP(name=...)` and the shaped
    instruction identity, so the two cannot disagree.  A project that
    redeclares it inside `CONFIG-FIELDS` silently shadows the template's
    definition — Python does not error on a repeated annotation, the later
    one just wins — so the contract is that this field stays out here.
    """
    text = _config_text()
    declaration = text.index("server_name: str = field(")
    fields_start = text.index("# CONFIG-FIELDS-START")
    assert declaration < fields_start, (
        "server_name must be declared before CONFIG-FIELDS-START; inside the "
        "block a copier update would treat it as domain content"
    )


def test_server_name_prefers_an_explicit_value_over_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A programmatically supplied name wins; an absent one reads the env.

    The default is a `default_factory`, not a bare literal, so both halves
    hold on the same construction path (#555).
    """
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SERVER_NAME", "from-env")
    assert ProjectConfig().server_name == "from-env"
    assert ProjectConfig(server_name="explicit").server_name == "explicit"
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_SERVER_NAME")
    assert ProjectConfig().server_name == "markdown-vault-mcp"


def test_server_name_is_not_read_inside_from_env() -> None:
    """The env read must stay in the module-level factory.

    Moved into `from_env`, the config-surface generator's AST scan would
    discover `MARKDOWN_VAULT_MCP_SERVER_NAME` as a *domain* var while
    `config-presentation.yml` already declares it with template provenance,
    and generation would fail with the duplicate-name error.
    `docs/design/config-migration.md` documents this case.
    """
    text = _config_text()
    from_env = text[text.index("def from_env") :]
    assert '"SERVER_NAME"' not in from_env, (
        "SERVER_NAME is read inside from_env — the generator's AST scan will "
        "double-declare it; keep the read in _default_server_name()"
    )
