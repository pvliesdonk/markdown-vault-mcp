"""A wheel consumer sees the library's annotations, not Any (#1911)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

CONSUMER = """from markdown_vault_mcp.providers import EmbeddingProvider

class ConsumerProvider(EmbeddingProvider):
    \"\"\"An application may extend the typed provider interface.\"\"\"

def dimension(provider: EmbeddingProvider) -> int:
    return provider.dimension
"""


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run without source-path overrides that could hide a packaging defect."""
    env = os.environ.copy()
    env.pop("MYPYPATH", None)
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        args, cwd=cwd, env=env, capture_output=True, text=True, check=False, timeout=120
    )


def test_installed_wheel_exposes_provider_types(tmp_path: Path) -> None:
    """Accept a typed extension and reject its incorrect return annotation."""
    uv = shutil.which("uv")
    assert uv is not None, "The project's uv development tool is required"
    root = Path(__file__).resolve().parents[1]
    build = _run([uv, "build", "--wheel", "--out-dir", str(tmp_path)], root)
    assert build.returncode == 0, build.stdout + build.stderr
    wheel = next(tmp_path.glob("*.whl"))
    environment = tmp_path / "consumer-env"
    created = _run([uv, "venv", "--python", sys.executable, str(environment)], tmp_path)
    assert created.returncode == 0, created.stdout + created.stderr
    python = environment / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    installed = _run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            "--no-deps",
            "--no-index",
            str(wheel),
        ],
        tmp_path,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    config = tmp_path / "mypy.ini"
    config.write_text("[mypy]\n", encoding="utf-8")
    consumer = tmp_path / "consumer.py"
    command = [
        sys.executable,
        "-m",
        "mypy",
        "--strict",
        "--no-incremental",
        "--config-file",
        str(config),
        "--python-executable",
        str(python),
        "--follow-imports=silent",
        str(consumer),
    ]
    consumer.write_text(CONSUMER, encoding="utf-8")
    valid = _run(command, tmp_path)
    assert valid.returncode == 0, valid.stdout + valid.stderr
    consumer.write_text(CONSUMER.replace("-> int:", "-> str:"), encoding="utf-8")
    invalid = _run(command, tmp_path)
    assert invalid.returncode == 1, invalid.stdout + invalid.stderr
    assert "[return-value]" in invalid.stdout, invalid.stdout
    assert "[import-untyped]" not in invalid.stdout, invalid.stdout
