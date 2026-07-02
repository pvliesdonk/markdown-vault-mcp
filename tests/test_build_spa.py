"""Unit tests for the modular-SPA assembler (``scripts/build_spa.py``).

These exercise the assembler and its guards against **temporary copies** of the
real ``static/spa/`` partials, so no committed file is ever mutated (unlike a
subprocess-against-the-tree approach).
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_REPO = Path(__file__).resolve().parent.parent
_REAL_SPA = _REPO / "src" / "markdown_vault_mcp" / "static" / "spa"
_COMMITTED_SRC = _REAL_SPA.parent / "app.src.html"


def _load_build_spa() -> ModuleType:
    """Import scripts/build_spa.py (not an installed package) by file path."""
    spec = importlib.util.spec_from_file_location(
        "build_spa", _REPO / "scripts" / "build_spa.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build_spa = _load_build_spa()


@pytest.fixture
def spa_copy(tmp_path: Path) -> Path:
    """A writable copy of the real ``spa/`` partials under ``tmp_path``."""
    dest = tmp_path / "spa"
    shutil.copytree(_REAL_SPA, dest)
    return dest


def _patch_shell(spa_dir: Path, old: str, new: str, count: int = 1) -> None:
    shell = spa_dir / "shell.html"
    shell.write_text(shell.read_text().replace(old, new, count), encoding="utf-8")


def test_assemble_reproduces_committed_source(spa_copy: Path) -> None:
    """Assembling the real partials reproduces the committed app.src.html."""
    assert build_spa.assemble(spa_copy) == _COMMITTED_SRC.read_text(encoding="utf-8")


def test_assemble_injects_banner_after_doctype(spa_copy: Path) -> None:
    """The generated banner is injected right after the doctype (not before,
    which would risk quirks mode; not stored in shell.html)."""
    out = build_spa.assemble(spa_copy)
    assert out.startswith("<!DOCTYPE html>\n<!-- GENERATED FILE")
    assert "GENERATED FILE" not in (spa_copy / "shell.html").read_text()


def test_assemble_rejects_malformed_marker(spa_copy: Path) -> None:
    """A colon-less marker never matches the regex, survives as literal text,
    and is caught by the residual-@@FILE guard rather than silently dropped."""
    css = spa_copy / "styles.css"
    css.write_text(css.read_text() + "\n/*@@FILE styles.css@@*/\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="@@FILE include marker survived"):
        build_spa.assemble(spa_copy)


def test_assemble_rejects_include_cycle(spa_copy: Path) -> None:
    """A partial that includes itself trips the recursion-depth guard (a loud
    SystemExit rather than a bare RecursionError)."""
    (spa_copy / "loop.js").write_text("/*@@FILE:loop.js@@*/", encoding="utf-8")
    _patch_shell(spa_copy, "/*@@FILE:core.js@@*/", "/*@@FILE:loop.js@@*/")
    with pytest.raises(SystemExit, match="recursion too deep"):
        build_spa.assemble(spa_copy)


def test_assemble_rejects_missing_doctype(spa_copy: Path) -> None:
    """A shell that does not open with the doctype is rejected (the banner
    splice depends on the doctype being the first line)."""
    _patch_shell(spa_copy, "<!DOCTYPE html>\n", "")
    with pytest.raises(SystemExit, match="must begin with"):
        build_spa.assemble(spa_copy)


def test_assemble_rejects_path_escape(spa_copy: Path) -> None:
    """An include marker resolving outside spa/ is rejected."""
    _patch_shell(spa_copy, "/*@@FILE:core.js@@*/", "/*@@FILE:../escape.js@@*/")
    with pytest.raises(SystemExit, match="escapes spa/ dir"):
        build_spa.assemble(spa_copy)


def test_assemble_rejects_missing_partial(spa_copy: Path) -> None:
    """A well-formed marker pointing at a nonexistent partial is rejected."""
    _patch_shell(spa_copy, "/*@@FILE:core.js@@*/", "/*@@FILE:nope.js@@*/")
    with pytest.raises(SystemExit, match="not found"):
        build_spa.assemble(spa_copy)


def test_main_writes_assembled_source(
    spa_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-check ``main()`` writes the assembled app.src.html next to ``spa/``."""
    monkeypatch.setattr(build_spa, "_find_spa_dir", lambda: spa_copy)
    assert build_spa.main(["build_spa.py"]) == 0
    written = (tmp_path / "app.src.html").read_text(encoding="utf-8")
    assert written == build_spa.assemble(spa_copy)


def test_check_reports_up_to_date(
    spa_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--check`` returns 0 when the sibling app.src.html matches the partials."""
    (tmp_path / "app.src.html").write_text(
        build_spa.assemble(spa_copy), encoding="utf-8"
    )
    monkeypatch.setattr(build_spa, "_find_spa_dir", lambda: spa_copy)
    assert build_spa.main(["build_spa.py", "--check"]) == 0


def test_check_reports_stale(
    spa_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--check`` returns 1 when the sibling app.src.html is stale."""
    (tmp_path / "app.src.html").write_text("<!DOCTYPE html>\nstale\n", encoding="utf-8")
    monkeypatch.setattr(build_spa, "_find_spa_dir", lambda: spa_copy)
    assert build_spa.main(["build_spa.py", "--check"]) == 1


def _point_discovery_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Relocate the module ``__file__`` so ``_find_spa_dir`` globs under ``tmp_path/src``."""
    monkeypatch.setattr(
        build_spa, "__file__", str(tmp_path / "scripts" / "build_spa.py")
    )


def test_find_spa_dir_errors_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ``src/*/static/spa/shell.html`` under the repo → loud SystemExit."""
    _point_discovery_at(tmp_path, monkeypatch)
    (tmp_path / "src").mkdir()
    with pytest.raises(SystemExit, match=r"no .*shell\.html found under"):
        build_spa._find_spa_dir()


def test_find_spa_dir_errors_when_multiple(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """More than one ``spa/shell.html`` under the repo → loud SystemExit."""
    _point_discovery_at(tmp_path, monkeypatch)
    for pkg in ("a", "b"):
        d = tmp_path / "src" / pkg / "static" / "spa"
        d.mkdir(parents=True)
        (d / "shell.html").write_text("<!DOCTYPE html>\n", encoding="utf-8")
    with pytest.raises(SystemExit, match=r"multiple .*shell\.html found"):
        build_spa._find_spa_dir()
