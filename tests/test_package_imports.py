"""Regression tests for the minimal package root (#665, Problem 3; #903).

The package root (``__init__.py``) is a bare template skeleton — a module
docstring and ``__version__`` — with no imports and no re-exports. Library
consumers import from the defining submodules (``from
markdown_vault_mcp.vault import Vault``), not the package root.

``pytest --cov=markdown_vault_mcp.<submodule>`` used to kill the whole test
session at conftest load with ``ImportError: cannot import name 'claw_state'
from partially initialized module 'beartype.claw._clawstate'``: coverage.py
resolves dotted source packages with ``importlib.util.find_spec`` inside a
sys.modules-restoring context (``coverage.misc.sys_modules_saved``), and an
eager package ``__init__`` dragged the full dependency tree (``config`` ->
``fastmcp_pvl_core`` -> ``beartype``, plus ``frontmatter`` -> PyYAML) into
that disposable import. The purge on context exit removed beartype's modules
but left its claw import hook in ``sys.path_hooks``, so the next import
routed through an orphaned hook into a circular re-import. The minimal root
keeps the fix by construction; these tests pin it: importing (or
find_spec-ing) the package root must stay light.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import markdown_vault_mcp

# Top-level distributions that must never be imported by the package root.
# beartype arrives via fastmcp_pvl_core (whose claw import hook survives a
# sys.modules purge); yaml arrives via frontmatter (whose single-phase-init
# C extension keeps first-generation class references across a purge).
_HEAVY = "{'beartype', 'fastmcp_pvl_core', 'frontmatter', 'yaml'}"


class TestMinimalRoot:
    """The package root is a bare skeleton; the public API lives in submodules."""

    def test_version_is_a_string(self):
        """``markdown_vault_mcp.__version__`` is exposed as a string."""
        assert isinstance(markdown_vault_mcp.__version__, str)

    def test_public_api_lives_in_submodules_not_the_root(self):
        """Classes are imported from their defining submodule, not the root.

        The pre-#903 lazy root re-exported ``Vault``/``ProjectConfig``/etc.;
        the minimal root does not, so a root import must fail while the
        submodule import succeeds.
        """
        from markdown_vault_mcp.config import ProjectConfig  # noqa: F401
        from markdown_vault_mcp.vault import Vault

        with pytest.raises(ImportError):
            from markdown_vault_mcp import (
                Vault,  # type: ignore[attr-defined]  # noqa: F401
            )


class TestImportIsLight:
    """The package root must not import the heavy dependency tree.

    Run in a subprocess so the assertions see a clean interpreter rather
    than whatever this test session has already imported.
    """

    def _run(self, code: str) -> None:
        """Execute code in a fresh interpreter and assert it succeeds."""
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    def test_package_import_is_light(self):
        """``import markdown_vault_mcp`` must not pull in the heavy deps."""
        self._run(
            "import sys; import markdown_vault_mcp; "
            f"heavy = {_HEAVY} & "
            "{m.split('.')[0] for m in sys.modules}; "
            "assert not heavy, f'package import loaded {heavy}'"
        )

    def test_find_spec_on_submodule_is_light(self):
        """``find_spec('markdown_vault_mcp.tracker')`` must stay light.

        This is exactly what coverage.py does (inside a sys.modules-restoring
        context) to resolve ``--cov=markdown_vault_mcp.tracker``; anything
        imported here is subsequently unloaded, orphaning beartype's claw
        ``sys.path_hooks`` entry and PyYAML's cached C extension.
        """
        self._run(
            "import importlib.util, sys; "
            "importlib.util.find_spec('markdown_vault_mcp.tracker'); "
            f"heavy = {_HEAVY} & "
            "{m.split('.')[0] for m in sys.modules}; "
            "assert not heavy, f'find_spec loaded {heavy}'"
        )

    def test_interpreter_survives_simulated_coverage_resolution(self):
        """Imports still work after coverage-style find_spec + module purge.

        Reproduces coverage.py's source-package resolution: find_spec on a
        dotted submodule, then purge every newly imported module. The
        interpreter must survive a subsequent heavy import (pre-fix this
        died in beartype's orphaned claw hook) and PyYAML parsing must
        still work (the 1.20.0-era symptom of the same root cause).
        """
        self._run(
            "import importlib.util, sys; "
            "before = set(sys.modules); "
            "importlib.util.find_spec('markdown_vault_mcp.tracker'); "
            "[sys.modules.pop(m) for m in set(sys.modules) - before]; "
            "import markdown_vault_mcp.config; "
            "import frontmatter; "
            "post = frontmatter.loads('---\\ntitle: Hello\\n---\\nbody\\n'); "
            "assert post.metadata == {'title': 'Hello'}, post.metadata"
        )

    def test_dotted_cov_invocation_passes(self, tmp_path):
        """The previously-fatal dotted --cov pytest invocation succeeds.

        End-to-end pin for #665 Problem 3: run the exact reported command in
        a subprocess. Pre-fix it aborted at conftest load with the beartype
        ``claw_state`` circular ImportError.
        """
        repo_root = Path(__file__).resolve().parent.parent
        env = {
            k: v
            for k, v in os.environ.items()
            # Strip outer pytest-cov subprocess hooks so the inner run
            # measures (and writes) its own coverage data only.
            if not k.startswith(("COV_CORE_", "COVERAGE_"))
        }
        # Keep the inner run's data file out of the repo root.
        env["COVERAGE_FILE"] = str(tmp_path / ".coverage")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/test_scanner.py",
                "--cov=markdown_vault_mcp.tracker",
                "--cov-fail-under=0",
                "-q",
                "-p",
                "no:cacheprovider",
            ],
            capture_output=True,
            text=True,
            check=False,
            cwd=repo_root,
            env=env,
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
