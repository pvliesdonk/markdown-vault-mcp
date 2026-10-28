"""The measurement harness end to end on a tiny vault (#1895).

Each phase runs in its own process, as in a real run; the vault, the vector
width and the counts are small enough to keep this a smoke test.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from scripts.envelope import __main__ as cli
from scripts.envelope import probes, synth, worker

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="reads /proc"
)

_KINDS = set(worker.KINDS)


def _run(
    tmp_path: Path, backend: str, *extra: str, kill_after: int = 2
) -> dict[str, Any]:
    out = tmp_path / "out"
    code = cli.main(
        [
            "--notes", "30", "--dim", "16", "--queries", "3", "--discard", "1",
            "--edits", "4", "--kill-after", str(kill_after), "--backends", backend,
            "--out", str(out), *extra,
        ]
    )  # fmt: skip
    assert code == 0
    (path,) = out.glob("*.json")
    result: dict[str, Any] = json.loads(path.read_text())
    return result


@pytest.mark.parametrize(
    ("backend", "search", "kill_after"),
    [("numpy", "resident", 2), ("sqlite", "streamed", 0)],
)
def test_a_run_measures_every_phase(
    tmp_path: Path,
    backend: str,
    search: str,
    kill_after: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = _run(tmp_path, backend, kill_after=kill_after)
    assert result["size"] == "custom"
    assert result["notes"] == 30
    assert result["chunks"] == 30 * synth.CHUNKS_PER_NOTE
    (config,) = result["configs"]
    assert (config["backend"], config["search"]) == (backend, search)

    build = config["build"]
    assert isinstance(build["peak_anon"], int)
    assert build["peak_resident"] >= build["peak_anon"] > 0

    memory = config["memory"]
    assert memory["store_closed_at_baseline"] is True
    for key in ("adds_anon", "adds_resident", "process_anon", "mapped_files"):
        assert isinstance(memory[key], int)
    assert memory["page_cache"] is None or memory["page_cache"] > 0
    partial = json.loads(
        (tmp_path / "out" / "work" / backend / "partial.json").read_text()
    )
    assert partial["recovery"] == config["recovery"]

    latency = config["latency"]
    assert set(latency["warm"]) == _KINDS
    assert set(latency["cold"]) == _KINDS
    assert all(0 < s["p50"] <= s["p95"] for s in latency["warm"].values())
    assert set(latency["eviction"]) == _KINDS

    edits = config["edits"]
    assert edits["count"] == 4
    assert edits["texts_embedded"]["max"] == 1  # only the rewritten section
    assert edits["note_vector_bytes"]["max"] == synth.CHUNKS_PER_NOTE * 16 * 4
    assert edits["wchar"]["median"] > 0

    restart = config["restart"]
    assert restart["texts_embedded"] == 0
    assert 0 < restart["seconds"] < restart["wall_seconds"]
    recovery = config["recovery"]
    assert recovery["finished_edits"] == kill_after  # never a stale count
    assert recovery["texts_embedded"] == recovery["texts_allowed"] == 1
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


def _config(tmp_path: Path, backend: str = "numpy") -> dict[str, Any]:
    synth.generate_vault(tmp_path / "vault", notes=8, seed=3)
    return {
        "work": str(tmp_path), "backend": backend, "search": cli.BACKENDS[backend],
        "dim": 16, "batch_size": 64, "drain_timeout_s": 60, "phase_timeout_s": 600,
    }  # fmt: skip


def test_the_build_reports_a_peak_freed_before_it_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = synth.StandInProvider.embed
    spiked: list[int] = []

    def embed(self: synth.StandInProvider, texts: list[str]) -> list[list[float]]:
        if not spiked:
            block = bytearray(64 << 20)
            block[::4096] = b"x" * len(block[::4096])
            spiked.append(len(block))
            time.sleep(0.05)  # five of the sampler's intervals
            del block
        return real(self, texts)

    monkeypatch.setattr(synth.StandInProvider, "embed", embed)
    config = _config(tmp_path)
    with probes.PeakSampler() as before:
        pass
    build = worker.build(config)
    assert spiked
    assert build["peak_anon"] >= before.peak_anon + (60 << 20)
    assert build["peak_resident"] >= build["peak_anon"]


def test_a_restart_embeds_a_note_changed_while_the_server_was_down(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    worker.build(config)
    note = tmp_path / "vault" / synth.FILTER_FOLDER / "note-000000.md"
    note.write_text(note.read_text() + "\nkalolo mimine rurusa\n")
    assert worker.restart(config)["texts_embedded"] == 1


def test_a_restart_reports_a_corrupt_store_moved_aside(tmp_path: Path) -> None:
    config = _config(tmp_path)
    worker.build(config)
    (tmp_path / "index-db-embeddings.v2.json").write_text("{not json")
    assert worker.restart(config)["moved_aside"] is True


def test_an_edit_that_never_drains_fails_instead_of_hanging() -> None:
    class _Stuck:
        class index:
            @staticmethod
            def wait_for_drain(timeout: float | None = None) -> bool:
                del timeout
                return False

    stuck, config = _Stuck(), {"drain_timeout_s": 5}
    with pytest.raises(RuntimeError, match="did not drain within 5 s"):
        worker._drain(stuck, config)  # type: ignore[arg-type]


def test_a_phase_that_outlasts_its_timeout_is_stopped(tmp_path: Path) -> None:
    config = {**_config(tmp_path), "phase_timeout_s": 0.01}
    with pytest.raises(SystemExit, match="did not finish within"):
        cli._spawn("build", config)


@pytest.mark.parametrize(
    "args",
    [
        ["--edits", "0"],
        ["--queries", "0"],
        ["--edits", "3", "--kill-after", "3"],
    ],
)
def test_counts_that_cannot_be_measured_are_refused(
    tmp_path: Path, args: list[str]
) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--notes", "3", "--out", str(tmp_path / "out"), *args])


def test_an_out_folder_inside_the_repository_is_refused() -> None:
    with pytest.raises(SystemExit, match="inside the repository"):
        cli.main(["--notes", "3", "--out", str(cli.REPO / "envelope-out")])


def _fake_spawn(finished: int) -> Any:
    def spawn(
        phase: str, config: dict[str, Any], *, expect_kill: bool = False, **_: Any
    ) -> tuple[dict[str, Any], float]:
        del phase
        if expect_kill:
            (Path(config["work"]) / "edits-finished").write_text(str(finished))
            return {}, 1.0
        return {"seconds": 0.5, "texts_embedded": 1, "moved_aside": False}, 1.0

    return spawn


def test_a_kill_the_harness_did_not_make_fails_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_spawn", _fake_spawn(finished=3))
    config = {**_config(tmp_path), "kill_after": 5, "edits": 10}
    with pytest.raises(SystemExit, match="killed after 3 edits, not 5"):
        cli._recovery(config, texts_per_edit=1)


def test_recovery_allows_what_an_edit_was_measured_to_embed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_spawn", _fake_spawn(finished=5))
    config = {**_config(tmp_path), "kill_after": 5, "edits": 10}
    recovery = cli._recovery(config, texts_per_edit=2)
    assert recovery["texts_allowed"] == 2
    assert recovery["texts_beyond_allowed"] == 0


def test_a_phase_that_prints_nothing_fails_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def silent(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", silent)
    config = _config(tmp_path)
    with pytest.raises(SystemExit, match="printed no figures"):
        cli._spawn("build", config)
