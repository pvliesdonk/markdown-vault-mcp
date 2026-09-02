"""Guard: importing `ProjectConfig` must stay inside the generator's dep floor.

The template-owned `scripts/gen_config_surface.py` imports this project's
`markdown_vault_mcp.config` to discover its domain env vars, and it does so
under `uv run --no-project --with fastmcp-pvl-core --with pyyaml` — copier's
tasks run before any project virtualenv exists, so the generator bootstraps
with exactly those two distributions and nothing else the project declares.

That makes "the import closure of `markdown_vault_mcp.config`" a real
contract, not an implementation detail. Break it and the failure is quiet in
the wrong direction: `_import_project_config` treats the `ModuleNotFoundError`
as tolerable (warning, `None`), the domain-var set comes back empty, and only
the *next* step — the mcpb install-screen `files:` guard, which names domain
vars — turns that into `exit 1`. The weekly copier-update workflow failed that
way twice before #1259, while a local run stayed green because `uv run
--no-project` still resolves the repo's own `.venv` from inside the repo.

The check runs in a subprocess because the pytest session has already imported
most of the package: only a fresh interpreter can observe what importing
`config` alone pulls in. `fastmcp_pvl_core` and `yaml` are imported first, so
whatever they legitimately drag in forms the baseline and the assertion is
about what `config` adds *beyond* the floor. A module the bootstrap does
install but core happens to import lazily would surface here as a false
positive; the fix for that one is to name it in the probe's baseline, not to
relax the assertion.

Fixing a real violation means moving the offending import out of module
scope — `TYPE_CHECKING` for annotations, a function-local import at the use
site for runtime use. `config_sections/_assembly.py` does both for
`markdown_vault_mcp.git`, and `config_sections/vault_settings.py` does the
latter for `markdown_vault_mcp.okf`.

Sibling guard, deliberately separate: `test_package_imports.py` pins that the
package *root* stays light, which is about surviving coverage.py's
find_spec-then-purge cycle (#665, #903). Same subprocess technique, different
contract — that one bounds `import markdown_vault_mcp`, this one bounds
`import markdown_vault_mcp.config`, and neither implies the other.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

#: Imports `fastmcp_pvl_core` and `yaml` to establish the dependency floor,
#: then reports the third-party top-level modules that importing
#: `markdown_vault_mcp.config` adds on top of it. Underscore-prefixed names
#: are interpreter internals (`_frozen_importlib_external` and friends), not
#: distributions.
_PROBE = """
import json
import sys

import fastmcp_pvl_core  # noqa: F401
import yaml  # noqa: F401

baseline = set(sys.modules)
import markdown_vault_mcp.config  # noqa: F401

added = {name.split(".")[0] for name in set(sys.modules) - baseline}
print(json.dumps(sorted(
    name for name in added
    if not name.startswith("_")
    and name not in sys.stdlib_module_names
    and name != "markdown_vault_mcp"
)))
"""


def test_config_import_adds_no_third_party_beyond_the_floor() -> None:
    """`markdown_vault_mcp.config` imports under core + PyYAML alone."""
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert result.returncode == 0, f"the probe interpreter failed:\n{result.stderr}"

    extra = json.loads(result.stdout.splitlines()[-1])
    assert extra == [], (
        "importing markdown_vault_mcp.config reaches third-party packages the "
        f"config-surface generator does not install: {extra}. Move the import "
        "to TYPE_CHECKING (annotations) or into the function that uses it "
        "(runtime) — see this module's docstring and #1259."
    )
