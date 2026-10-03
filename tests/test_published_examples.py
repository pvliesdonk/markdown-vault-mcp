"""Published examples do what their text says (template-owned, re-rendered).

A reader copies a fenced block as written, so each tagged block is checked
against its claim (``docs/contribute/docs-structure.md``, Examples):

- ```` ```python { .run data-expect="results" } ```` runs as written, after
  the literal substitutions ``docs_example_substitutions`` supplies (a
  project fixture in ``tests/conftest.py``, optional), and the named
  variable is truthy afterwards;
- ```` ```json { .config data-expect="read_only=True" } ```` (or a
  dotenv-shaped shell block with the same tag) configures what it claims:
  ``ProjectConfig.from_env()`` over the block's variables carries each
  ``field=literal``;
- every shell block quotes package extras (``"pkg[extra]"``), the form that
  works on macOS's default shell.

An untagged Python block is neither run nor failed; ``scripts/
check_docs_structure.py`` reports it as W4 until it carries ``.run`` or
``.fragment``.  A shell block tagged ``.fragment`` (shown output, say) is
not checked.  The parsing lives in ``scripts/published_examples.py``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from markdown_vault_mcp.config import ProjectConfig

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))
from published_examples import (  # type: ignore[import-not-found]  # noqa: E402
    PYTHON_LANGS,
    SHELL_LANGS,
    Block,
    blocks,
    env_from_config,
    expectations,
    published_pages,
    unquoted_extras,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

ENV_PREFIX = "MARKDOWN_VAULT_MCP_"


def _tagged() -> Iterator[tuple[str, Block]]:
    for page in published_pages(_ROOT):
        rel = page.relative_to(_ROOT).as_posix()
        for block in blocks(page.read_text(encoding="utf-8-sig")):
            where = f"{rel}:{block.line}"
            if block.heading_trail:
                where += f" § {block.heading_trail[-1]}"
            yield where, block


def _cases(kind: str) -> list[Any]:
    return [
        pytest.param(where, block, id=where)
        for where, block in _tagged()
        if kind in block.classes
    ]


def _shell_cases() -> list[Any]:
    return [
        pytest.param(where, block, id=where)
        for where, block in _tagged()
        if block.lang in SHELL_LANGS and "fragment" not in block.classes
    ]


def _optional_fixture(request: pytest.FixtureRequest, name: str) -> Any:
    try:
        return request.getfixturevalue(name)
    except pytest.FixtureLookupError:
        return None


@pytest.mark.parametrize(("where", "block"), _cases("run"))
def test_run_blocks_run_as_written(
    where: str, block: Block, request: pytest.FixtureRequest
) -> None:
    assert block.lang in PYTHON_LANGS, (
        f"{where}: .run is for Python blocks, not {block.lang!r}"
    )
    code = block.code
    substitutions = _optional_fixture(request, "docs_example_substitutions") or {}
    for old, new in substitutions.items():
        code = code.replace(old, str(new))
    # Not "__main__": a Quick Start's `if __name__ == "__main__": mcp.run()`
    # guard must stay closed, or the block would start a server under pytest.
    namespace: dict[str, object] = {"__name__": "published_example"}
    exec(compile(code, where, "exec"), namespace)  # the published example is the test
    expect = block.attrs.get("data-expect")
    if expect:
        assert expect in namespace, f"{where}: the block never assigns {expect!r}"
        assert namespace[expect], f"{where}: {expect!r} is falsy after the block ran"


@pytest.mark.parametrize(("where", "block"), _cases("config"))
def test_config_blocks_configure_what_they_claim(
    where: str,
    block: Block,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = expectations(block.attrs.get("data-expect", ""))
    assert expected, f'{where}: a .config block needs data-expect="field=literal"'
    contract = _optional_fixture(request, "config_contract_env") or {}
    for name, env in env_from_config(block):
        for key in list(os.environ):
            if key.startswith(ENV_PREFIX):
                monkeypatch.delenv(key, raising=False)
        for key, value in {**contract, **env}.items():
            monkeypatch.setenv(key, value)
        config = ProjectConfig.from_env()
        for field_name, literal in expected.items():
            actual = getattr(config, field_name)
            assert actual == literal, (
                f"{where}: server {name!r} is shown with {field_name}={literal!r} "
                f"but configures {actual!r}"
            )


@pytest.mark.parametrize(("where", "block"), _shell_cases())
def test_shell_blocks_quote_package_extras(where: str, block: Block) -> None:
    found = unquoted_extras(block.code)
    assert not found, "\n".join(
        f"{where} line {line}: {token} is unquoted; zsh expands it as a glob, "
        f"write {quoted}"
        for line, token, quoted in found
    )
