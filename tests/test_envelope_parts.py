"""The measurement harness's pure parts (#1895): synthetic vault, probes, report."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest
from scripts.envelope import probes, report, synth

from markdown_vault_mcp.config_sections import VaultSettings
from markdown_vault_mcp.vault import Vault

linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="reads /proc"
)


class TestTheStandInProvider:
    def test_a_text_always_gets_the_same_unit_vector(self) -> None:
        one, two = synth.StandInProvider(), synth.StandInProvider()
        (a,), (b,) = one.embed(["tides"]), two.embed(["tides"])
        assert a == b
        assert len(a) == 1024 == one.dimension
        assert np.linalg.norm(a) == pytest.approx(1.0, abs=1e-5)

    def test_different_texts_get_different_vectors(self) -> None:
        a, b = synth.StandInProvider(dim=16).embed(["tides", "boats"])
        assert a != b

    def test_it_counts_texts_and_calls_without_keeping_them(self) -> None:
        provider = synth.StandInProvider(dim=8)
        provider.embed(["a", "b"])
        provider.embed_query(["q"])
        assert (provider.count, provider.calls) == (3, 2)
        assert provider.texts == []

    def test_it_keeps_the_texts_when_asked(self) -> None:
        provider = synth.StandInProvider(dim=8, keep_texts=True)
        provider.embed(["a", "b"])
        provider.embed_query(["q"])
        assert provider.texts == ["a", "b", "q"]


class TestTheSyntheticVault:
    def test_the_same_seed_writes_the_same_bytes(self, tmp_path: Path) -> None:
        first = synth.generate_vault(tmp_path / "one", notes=40, seed=7)
        second = synth.generate_vault(tmp_path / "two", notes=40, seed=7)
        assert first.paths == second.paths
        for path in first.paths:
            assert (tmp_path / "one" / path).read_bytes() == (
                tmp_path / "two" / path
            ).read_bytes()

    def test_another_seed_writes_other_notes(self, tmp_path: Path) -> None:
        first = synth.generate_vault(tmp_path / "one", notes=5, seed=1)
        synth.generate_vault(tmp_path / "two", notes=5, seed=2)
        path = first.paths[0]
        assert (tmp_path / "one" / path).read_bytes() != (
            tmp_path / "two" / path
        ).read_bytes()

    def test_about_a_tenth_of_the_notes_sit_in_the_filter_folder(
        self, tmp_path: Path
    ) -> None:
        spec = synth.generate_vault(tmp_path / "v", notes=200, seed=3)
        inside = [p for p in spec.paths if p.startswith(f"{spec.filter_folder}/")]
        assert len(spec.paths) == 200
        assert 15 <= len(inside) <= 25

    def test_each_note_indexes_to_the_calibrated_chunk_count(
        self, tmp_path: Path
    ) -> None:
        spec = synth.generate_vault(tmp_path / "v", notes=12, seed=5)
        vault = Vault(
            source_dir=tmp_path / "v",
            settings=VaultSettings(index_path=tmp_path / "index.db"),
        )
        try:
            stats = vault.index.build_index()
        finally:
            vault.close()
        assert stats.documents_indexed == 12
        assert stats.chunks_indexed == 12 * synth.CHUNKS_PER_NOTE
        assert synth.notes_for_chunks(22_764) == 3252
        assert synth.notes_for_chunks(100) == 14  # 98 chunks, the nearest
        assert synth.notes_for_chunks(1) == 1
        assert spec.chunks_per_note == synth.CHUNKS_PER_NOTE

    def test_an_edit_rewrites_one_section_of_one_note(self, tmp_path: Path) -> None:
        spec = synth.generate_vault(tmp_path / "v", notes=3, seed=5)
        text = (tmp_path / "v" / spec.paths[1]).read_text()
        frontmatter, body = synth.split_note(text)
        new_frontmatter, new_body = synth.edited_note(text, edit=4)
        assert synth.edited_note(text, edit=4) == (new_frontmatter, new_body)
        assert new_frontmatter == frontmatter
        old_sections, new_sections = body.split("\n## "), new_body.split("\n## ")
        assert len(old_sections) == len(new_sections)
        changed = [
            i
            for i, pair in enumerate(zip(old_sections, new_sections, strict=True))
            if len(set(pair)) > 1
        ]
        assert len(changed) == 1
        (index,) = changed
        assert new_sections[index].startswith(f"Section {index}\n")

    def test_the_queries_use_the_vault_vocabulary(self) -> None:
        words = set(synth.VOCABULARY)
        for query in synth.QUERIES:
            assert set(query.split()) <= words


SMAPS = """\
55d0c0000000-7ffd00000000 ---p 00000000 00:00 0                          [rollup]
Rss:              120000 kB
Pss:              100000 kB
Pss_Dirty:         60000 kB
Pss_Anon:          70000 kB
Pss_File:          28000 kB
Pss_Shmem:          2000 kB
Shared_Clean:      20000 kB
Anonymous:         70000 kB
"""

STATUS = """\
Name:\tpython
VmHWM:\t  150000 kB
VmRSS:\t  120000 kB
RssAnon:\t   70000 kB
RssFile:\t   48000 kB
RssShmem:\t    2000 kB
"""

IO = """\
rchar: 4096
wchar: 12345
syscr: 3
syscw: 4
read_bytes: 0
write_bytes: 8192
cancelled_write_bytes: 0
"""


class TestTheProcReaders:
    def test_smaps_rollup_splits_anonymous_from_file_backed(self) -> None:
        memory = probes.parse_smaps_rollup(SMAPS)
        assert memory.anon == (70000 + 2000) * 1024
        assert memory.file == 28000 * 1024

    def test_status_gives_resident_counters_in_bytes(self) -> None:
        status = probes.parse_status(STATUS)
        assert status == {
            "VmHWM": 150000 * 1024,
            "VmRSS": 120000 * 1024,
            "RssAnon": 70000 * 1024,
            "RssFile": 48000 * 1024,
            "RssShmem": 2000 * 1024,
        }

    def test_io_gives_the_write_counters(self) -> None:
        counters = probes.parse_io(IO)
        assert counters["wchar"] == 12345
        assert counters["write_bytes"] == 8192

    @linux_only
    def test_this_process_can_be_read(self) -> None:
        memory = probes.read_memory(os.getpid())
        assert memory.anon > 0
        assert probes.read_io(os.getpid())["wchar"] >= 0

    @linux_only
    def test_waiting_for_a_sample_takes_one_after_the_call(self) -> None:
        sampler = probes.PeakSampler(interval_s=3600)
        with sampler:
            block = bytearray(64 << 20)
            block[::4096] = b"x" * len(block[::4096])
            assert sampler.wait_for_sample()
            seen = sampler.peak_anon
            del block
        assert seen >= 60 << 20

    @linux_only
    def test_the_sampler_sees_a_peak_that_has_gone(self) -> None:
        with probes.PeakSampler(interval_s=0.002) as sampler:
            block = bytearray(64 << 20)
            block[::4096] = b"x" * len(block[::4096])
            assert sampler.wait_for_sample()
            del block
        assert sampler.peak_anon >= 60 << 20
        assert sampler.peak_resident >= sampler.peak_anon

    @linux_only
    def test_eviction_runs_on_files_and_skips_missing_ones(
        self, tmp_path: Path
    ) -> None:
        present = tmp_path / "store.bin"
        present.write_bytes(b"\0" * (1 << 20))
        assert probes.evict([present, tmp_path / "absent.bin"]) == [present]

    @linux_only
    def test_eviction_drops_a_disk_file_from_page_cache(self) -> None:
        # tmp_path may be tmpfs, whose pages cannot leave memory; the repo's
        # gitignored scratch folder is on whatever disk the checkout is.
        folder = Path(__file__).resolve().parents[1] / "docs" / "superpowers"
        folder.mkdir(parents=True, exist_ok=True)
        if report.environment(folder)["filesystem"] in {"tmpfs", "ramfs"}:
            pytest.skip("the checkout is on a memory-backed filesystem")
        present = folder / f"evict-{os.getpid()}.bin"
        try:
            present.write_bytes(b"x" * (1 << 20))
            probes.evict([present])
            resident = probes.resident_bytes([present])
        finally:
            present.unlink()
        if resident is None:
            pytest.skip("util-linux fincore is not installed")
        assert resident == 0


class TestProcessAge:
    def test_the_age_is_uptime_minus_the_start_time(self) -> None:
        fields = ["S"] + ["0"] * 18 + ["250"] + ["0"] * 10
        stat = "4242 (a name (with) spaces) " + " ".join(fields)
        assert probes.parse_process_age(stat, "12.75 99.0", 100) == 10.25

    @linux_only
    def test_this_process_has_an_age(self) -> None:
        assert probes.process_age() > 0


class TestPercentiles:
    def test_nearest_rank(self) -> None:
        values = [float(v) for v in range(1, 101)]
        assert probes.percentiles(values) == {"p50": 50.0, "p95": 95.0}

    def test_one_value(self) -> None:
        assert probes.percentiles([3.0]) == {"p50": 3.0, "p95": 3.0}

    def test_no_values(self) -> None:
        with pytest.raises(ValueError, match="no samples"):
            probes.percentiles([])


def _result() -> dict[str, object]:
    return {
        "size": "deployed",
        "notes": 3794,
        "chunks": 22764,
        "environment": {"git_sha": "abc1234", "python": "3.12.0"},
        "configs": [
            {
                "backend": "numpy",
                "search": "resident",
                "memory": {
                    "adds_anon": 95 << 20,
                    "adds_resident": 99 << 20,
                    "process_anon": 300 << 20,
                    "mapped_files": 40 << 20,
                    "page_cache": 120 << 20,
                },
                "latency": {
                    "warm": {"unfiltered": {"p50": 0.01, "p95": 0.02}},
                    "cold": {"unfiltered": 0.2},
                },
                "edits": {
                    "count": 200,
                    "wchar": {"median": 4_000_000, "max": 4_500_000},
                    "store_delta": {"median": 0, "max": 0},
                    "note_vector_bytes": {"median": 24576, "max": 24576},
                    "texts_embedded": {"median": 6, "max": 6},
                },
                "restart": {"seconds": 1.5, "texts_embedded": 0},
            },
            {
                "backend": "postgres",
                "search": "streamed",
                "source": "MVM_TEST_POSTGRES_DSN (hidden DSN)",
                "dsn": "host=db.example password=hunter2",
            },
        ],
    }


class TestTheReport:
    def test_the_table_names_each_configuration_and_both_adds_figures(self) -> None:
        text = report.render_markdown(_result())
        assert "numpy (resident)" in text
        assert "postgres (streamed)" in text
        assert "95.0 MiB" in text
        assert "99.0 MiB" in text
        assert "22,764" in text

    def test_adds_measured_with_the_store_open_are_marked_invalid(self) -> None:
        result = _result()
        memory = result["configs"][0]["memory"]  # type: ignore[index]
        memory["store_closed_at_baseline"] = False
        text = report.render_markdown(result)
        assert "invalid: store open" in text
        assert "95.0 MiB" not in text

    def test_page_cache_and_mapped_files_are_told_apart(self) -> None:
        text = report.render_markdown(_result())
        assert "| 40.0 MiB | 120.0 MiB |" in text
        assert "Mapped files" in text
        assert "Index and store in page cache" in text

    def test_both_restarts_show_whether_a_store_was_moved_aside(self) -> None:
        result = _result()
        config = result["configs"][0]  # type: ignore[index]
        config["restart"]["moved_aside"] = True
        config["recovery"] = {"moved_aside": False}
        assert "| yes / no |" in report.render_markdown(result)

    def test_each_cold_search_shows_its_eviction(self) -> None:
        result = _result()
        latency = result["configs"][0]["latency"]  # type: ignore[index]
        latency["eviction"] = {"unfiltered": "resident 4096 bytes"}
        assert "| resident 4096 bytes |" in report.render_markdown(result)

    def test_a_dsn_never_reaches_the_table_or_the_file(self, tmp_path: Path) -> None:
        result = _result()
        text = report.render_markdown(result)
        path = report.write_json(result, tmp_path)
        assert "hunter2" not in text
        assert "hunter2" not in path.read_text()
        assert "(hidden DSN)" in text
        assert path.name.startswith("abc1234-deployed-")
        assert json.loads(path.read_text())["configs"][1].get("dsn") is None

    def test_a_dsn_inside_a_value_or_a_tuple_is_hidden(self, tmp_path: Path) -> None:
        result = _result()
        result["configs"].append(  # type: ignore[attr-defined]
            {
                "backend": "postgres",
                "search": "streamed",
                "error": "connection to postgresql://u:hunter2@db/x failed",
                "detail": "host=db port=5432 user=u password=hunter2 dbname=x",
                "pairs": ({"conninfo": "hunter2"},),
                7: "an int key",
            }
        )
        text = report.render_markdown(result)
        written = report.write_json(result, tmp_path).read_text()
        for output in (text, written):
            assert "hunter2" not in output
        assert "<hidden>" in written

    def test_a_dirty_tree_and_a_rerun_get_their_own_file(self, tmp_path: Path) -> None:
        result = _result()
        result["environment"]["dirty"] = True  # type: ignore[index]
        first = report.write_json(result, tmp_path)
        second = report.write_json(result, tmp_path)
        assert first != second
        assert first.name.startswith("abc1234-dirty-deployed-")

    def test_the_environment_names_what_the_figures_depend_on(
        self, tmp_path: Path
    ) -> None:
        env = report.environment(tmp_path)
        for key in ("git_sha", "python", "sqlite", "numpy", "kernel", "cpu"):
            assert env[key]
        assert "filesystem" in env
        assert isinstance(env["dirty"], bool)


def test_the_postgres_service_gets_a_table_of_its_own() -> None:
    result = _result()
    result["configs"][1].update(  # type: ignore[index]
        {
            "service": {
                "memory": 80 << 20,
                "database_bytes": 300 << 20,
                "schema_bytes": 120 << 20,
                "server_version": "17.6",
                "pgvector_version": "0.8.1",
            },
            "edits": {"wal_bytes": {"median": 40_000, "max": 90_000}},
        }
    )
    text = report.render_markdown(result)
    assert "### PostgreSQL service" in text
    assert (
        "| postgres (streamed) | 80.0 MiB | 300.0 MiB | 120.0 MiB | "
        "40,000 / 90,000 | 17.6 / 0.8.1 |" in text
    )
    assert "PostgreSQL's store writes no local files" in text
    assert "Store growth" in text


def test_no_service_table_without_postgres() -> None:
    result = _result()
    result["configs"] = result["configs"][:1]  # type: ignore[index]
    assert "PostgreSQL service" not in report.render_markdown(result)
