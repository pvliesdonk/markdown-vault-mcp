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

from markdown_vault_mcp import config as config_module
from markdown_vault_mcp.config import ProjectConfig


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


def test_post_init_runs_on_both_construction_paths() -> None:
    """Validation added to CONFIG-VALIDATE fires for direct construction AND from_env.

    This is what `env_float` / `env_int` bounds cannot do — they check only the
    env-sourced value, so `ProjectConfig(field=<bad default>)` slips past them.
    A subclass standing in for a domain's filled-in CONFIG-VALIDATE block
    proves the dataclass actually dispatches to `__post_init__` on both paths.
    """
    calls: list[str] = []

    @dataclass(frozen=True)
    class _Validated(ProjectConfig):
        def __post_init__(self) -> None:
            calls.append("post_init")

    _Validated()
    assert calls == ["post_init"], "direct construction did not run __post_init__"

    _Validated.from_env()
    assert calls == ["post_init", "post_init"], "from_env did not run __post_init__"


def test_post_init_can_reject_a_value() -> None:
    """A raise inside the seam propagates out of both construction paths."""

    @dataclass(frozen=True)
    class _Rejecting(ProjectConfig):
        def __post_init__(self) -> None:
            raise ValueError("domain invariant violated")

    for construct in (_Rejecting, _Rejecting.from_env):
        try:
            construct()
        except ValueError as exc:
            assert "domain invariant violated" in str(exc)
        else:  # pragma: no cover - the raise above always fires
            raise AssertionError(f"{construct} did not propagate the ValueError")


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
