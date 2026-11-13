"""``[[tool.mypy.overrides]]`` only silences untyped third-party imports (template-owned).

``pyproject.toml`` gives a project the ``PROJECT-MYPY-OVERRIDES`` block for
the libraries it imports that ship no types.  An override there may set
``ignore_missing_imports = true`` and nothing else, and may not name this
project's own package or ``tests``: anything more would loosen the
``mypy src/ tests/`` gate the template ships.  Type a test helper instead of
relaxing ``tests.*``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _own_packages(config: dict[str, Any]) -> set[str]:
    packages = config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    return {Path(p).name for p in packages} | {"tests"}


def _modules(override: dict[str, Any]) -> list[str]:
    module = override.get("module", [])
    return [module] if isinstance(module, str) else list(module)


def findings(config: dict[str, Any]) -> list[str]:
    """Name every override that does more than silence a third-party import."""
    own = _own_packages(config)
    problems: list[str] = []
    for override in config.get("tool", {}).get("mypy", {}).get("overrides", []):
        modules = _modules(override)
        label = ", ".join(modules) or "<no module>"
        extra = sorted(set(override) - {"module", "ignore_missing_imports"})
        if extra:
            problems.append(
                f"{label}: sets {', '.join(extra)}; only ignore_missing_imports is allowed"
            )
        if override.get("ignore_missing_imports") is not True:
            problems.append(f"{label}: must set ignore_missing_imports = true")
        for module in modules:
            root = module.split(".", 1)[0]
            if root in own or root == "*":
                problems.append(
                    f"{label}: names {module}, which mypy src/ tests/ must check in full"
                )
    return problems


def test_overrides_only_silence_third_party_imports() -> None:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    problems = findings(config)
    assert not problems, "\n".join(problems)


def _config(*overrides: dict[str, Any]) -> dict[str, Any]:
    wheel = {"packages": ["src/my_server"]}
    return {
        "tool": {
            "hatch": {"build": {"targets": {"wheel": wheel}}},
            "mypy": {"overrides": list(overrides)},
        }
    }


def test_third_party_import_override_is_clean() -> None:
    override = {"module": ["lxml", "lxml.*"], "ignore_missing_imports": True}
    assert findings(_config(override)) == []


def test_relaxing_tests_is_named() -> None:
    override = {"module": "tests.*", "disable_error_code": ["no-untyped-def"]}
    problems = findings(_config(override))
    assert any("sets disable_error_code" in p for p in problems)
    assert any("names tests.*" in p for p in problems)


def test_ignoring_own_package_is_named() -> None:
    override = {"module": ["my_server.legacy"], "ignore_missing_imports": True}
    assert any("names my_server.legacy" in p for p in findings(_config(override)))


def test_ignore_errors_is_named() -> None:
    override = {"module": "requests", "ignore_errors": True}
    problems = findings(_config(override))
    assert any("sets ignore_errors" in p for p in problems)
    assert any("must set ignore_missing_imports" in p for p in problems)
