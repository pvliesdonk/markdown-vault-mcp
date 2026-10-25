"""The measurement harness end to end on a tiny vault (#1895).

Each phase runs in its own process, as in a real run; the vault, the vector
width and the counts are small enough to keep this a smoke test.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import pytest
from scripts.envelope import __main__ as cli
from scripts.envelope import synth, worker

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="reads /proc"
)

_KINDS = set(worker.KINDS)


def _run(tmp_path: Path, backend: str, *extra: str) -> dict[str, Any]:
    out = tmp_path / "out"
    code = cli.main(
        [
            "--notes", "30", "--dim", "16", "--queries", "3", "--discard", "1",
            "--edits", "4", "--kill-after", "2", "--backends", backend,
            "--out", str(out), *extra,
        ]
    )  # fmt: skip
    assert code == 0
    (path,) = out.glob("*.json")
    result: dict[str, Any] = json.loads(path.read_text())
    return result


@pytest.mark.parametrize(
    ("backend", "search"), [("numpy", "resident"), ("sqlite", "streamed")]
)
def test_a_run_measures_every_phase(
    tmp_path: Path,
    backend: str,
    search: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = _run(tmp_path, backend)
    assert result["size"] == "custom"
    assert result["notes"] == 30
    assert result["chunks"] == 30 * synth.CHUNKS_PER_NOTE
    (config,) = result["configs"]
    assert (config["backend"], config["search"]) == (backend, search)

    memory = config["memory"]
    assert memory["store_closed_at_baseline"] is True
    for key in ("adds_anon", "adds_resident", "process_anon", "file_backed"):
        assert isinstance(memory[key], int)

    latency = config["latency"]
    assert set(latency["warm"]) == _KINDS
    assert set(latency["cold"]) == _KINDS
    assert all(0 < s["p50"] <= s["p95"] for s in latency["warm"].values())
    assert latency["eviction"] in {"confirmed", "unconfirmed"} or (
        latency["eviction"].startswith("resident ")
    )

    edits = config["edits"]
    assert edits["count"] == 4
    assert edits["texts_embedded"]["max"] == 1  # only the rewritten section
    assert edits["note_vector_bytes"]["max"] == synth.CHUNKS_PER_NOTE * 16 * 4
    assert edits["wchar"]["median"] > 0

    assert config["restart"]["texts_embedded"] == 0
    assert config["restart"]["seconds"] > 0
    recovery = config["recovery"]
    assert recovery["finished_edits"] == 2
    assert recovery["texts_beyond_allowed"] == 0
    assert recovery["moved_aside"] is False

    table = capsys.readouterr().out
    assert f"{backend} ({search})" in table


def test_the_work_folder_of_another_tool_is_refused(tmp_path: Path) -> None:
    work = tmp_path / "out" / "work"
    work.mkdir(parents=True)
    (work / "precious.txt").write_text("keep me")
    with pytest.raises(SystemExit, match="not made by the envelope harness"):
        cli.main(
            ["--notes", "3", "--backends", "numpy", "--out", str(tmp_path / "out")]
        )
    assert (work / "precious.txt").read_text() == "keep me"


def test_an_unknown_backend_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--backends", "faiss", "--out", str(tmp_path)])


def test_a_memory_backed_work_folder_is_flagged() -> None:
    assert "not cold" in (cli.cold_warning({"filesystem": "tmpfs"}) or "")
    assert cli.cold_warning({"filesystem": "ext2/ext3"}) is None
