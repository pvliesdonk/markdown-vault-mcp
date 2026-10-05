"""The unresolved-link report on write / edit / append (#1725).

A writer that guesses a link target wrong learns about it at write time, from
the same index rows ``get_broken_links`` reads, and only for links its own call
added.  These tests drive a real :class:`Vault` so the index refresh and the
link resolution under test are the production ones.
"""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from fastmcp import Client

from markdown_vault_mcp._tools.writer import _write_payload
from markdown_vault_mcp.exceptions import ReadOnlyError
from markdown_vault_mcp.facets.writer import WriterFacet
from markdown_vault_mcp.managers.link_report import UnresolvedLinkReporter
from markdown_vault_mcp.types import EditResult, WriteResult
from markdown_vault_mcp.vault import Vault, VaultSettings
from tests.conftest import wait_for_mcp_writer_drain
from tests.server_factory import make_server

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    """A writable, indexed vault with one note reachable by stem and by alias."""
    (tmp_path / "people").mkdir()
    (tmp_path / "people" / "ada.md").write_text(
        "---\naliases: [Countess]\n---\n# Ada\n", encoding="utf-8"
    )
    vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
    vault.index.build_index()
    try:
        yield vault
    finally:
        vault.close()


#: The new links each call adds to a note seeded with ``[[Old]] keep``: two
#: that resolve (path stem, alias), two that do not, and the old link again.
_NEW_LINKS = "[[Old]] [[New]] [missing](missing.md) [[ada]] [[Countess]]"

#: write, edit and append, each asking for the report and adding _NEW_LINKS.
_REPORTED_CALLS: dict[str, Callable[[Vault, str], WriteResult | EditResult]] = {
    "write": lambda v, p: v.writer.write(
        p, f"[[Old]] keep {_NEW_LINKS}\n", report_unresolved_links=True
    ),
    "edit": lambda v, p: v.writer.edit(
        p, old_text="keep", new_text=_NEW_LINKS, report_unresolved_links=True
    ),
    "append": lambda v, p: v.writer.append(
        p, f"{_NEW_LINKS}\n", report_unresolved_links=True
    ),
}


def _broken_targets(vault: Vault, path: str) -> set[str]:
    """Return what get_broken_links lists for *path*, once the index is current."""
    vault.index.wait_for_drain(timeout=30)
    return {
        b.raw_target for b in vault.graph.get_broken_links() if b.source_path == path
    }


class TestReportedLinks:
    def test_write_reports_each_unresolved_kind(self, vault: Vault) -> None:
        result = vault.writer.write(
            "n.md",
            "[[Nope]] [md](missing.md) [ref][r]\n\n[r]: gone.md\n",
            report_unresolved_links=True,
        )
        assert sorted(result.unresolved_links or []) == [
            "Nope",
            "gone.md",
            "missing.md",
        ]

    def test_resolving_links_are_not_reported(self, vault: Vault) -> None:
        """Path stem, path-qualified, alias, fragment and markdown forms resolve."""
        result = vault.writer.write(
            "n.md",
            "[[ada]] [[people/ada]] [[Countess]] [[ada#Ada]] [a](people/ada.md)\n",
            report_unresolved_links=True,
        )
        assert result.unresolved_links == []

    def test_targets_keep_their_written_form_once_in_order(self, vault: Vault) -> None:
        result = vault.writer.write(
            "n.md",
            "[[Zed#part]] [[Alpha]] [[Zed#part]]\n",
            report_unresolved_links=True,
        )
        assert result.unresolved_links == ["Zed#part", "Alpha"]

    def test_edit_reports_only_the_link_it_added(self, vault: Vault) -> None:
        vault.writer.write("n.md", "[[Old]] keep\n")
        result = vault.writer.edit(
            "n.md",
            old_text="keep",
            new_text="[[New]] [[Old]] [[ada]]",
            report_unresolved_links=True,
        )
        assert result.unresolved_links == ["New"]

    def test_append_reports_only_the_link_it_added(self, vault: Vault) -> None:
        vault.writer.write("n.md", "[[Old]]\n")
        result = vault.writer.append(
            "n.md", "[[Old]] [[Fresh]]\n", report_unresolved_links=True
        )
        assert result.unresolved_links == ["Fresh"]

    def test_append_creating_the_note_reports_its_links(self, vault: Vault) -> None:
        result = vault.writer.append(
            "new.md",
            "[[Fresh]]\n",
            create_if_missing=True,
            report_unresolved_links=True,
        )
        assert result.created is True
        assert result.unresolved_links == ["Fresh"]

    def test_overwrite_keeps_out_links_the_note_held(self, vault: Vault) -> None:
        vault.writer.write("n.md", "[[Old]]\n")
        result = vault.writer.write(
            "n.md", "[[Old]] [[Other]]\n", report_unresolved_links=True
        )
        assert result.unresolved_links == ["Other"]

    def test_unparseable_old_note_reports_every_unresolved_link(
        self, vault: Vault, tmp_path: Path
    ) -> None:
        """An unreadable prior state is no evidence a link existed: report it."""
        (tmp_path / "n.md").write_text("---\n: [\n---\n[[Old]]\n", encoding="utf-8")
        result = vault.writer.write("n.md", "[[Old]]\n", report_unresolved_links=True)
        assert result.unresolved_links == ["Old"]


class TestAgreesWithGetBrokenLinks:
    @pytest.mark.parametrize("method", sorted(_REPORTED_CALLS))
    def test_report_is_exactly_the_new_broken_links(
        self, vault: Vault, method: str
    ) -> None:
        """Reported == broken after the call minus broken before it, both ways."""
        vault.writer.write("n.md", "[[Old]] keep\n")
        before = _broken_targets(vault, "n.md")
        assert before == {"Old"}
        result = _REPORTED_CALLS[method](vault, "n.md")
        after = _broken_targets(vault, "n.md")
        reported = set(result.unresolved_links or [])
        # Every reported link is one get_broken_links newly lists...
        assert reported <= after - before
        # ...and every link it newly lists is reported.
        assert after - before <= reported
        assert reported == {"New", "missing.md"}

    def test_note_outside_the_index_reports_nothing(self, tmp_path: Path) -> None:
        """An excluded note has no rows, so get_broken_links lists none either."""
        excluded = Vault(
            source_dir=tmp_path,
            settings=VaultSettings(read_only=False, exclude_patterns=["skip/*"]),
        )
        excluded.index.build_index()
        try:
            result = excluded.writer.write(
                "skip/n.md", "[[Nope]]\n", report_unresolved_links=True
            )
            assert result.unresolved_links == []
            assert _broken_targets(excluded, "skip/n.md") == set()
        finally:
            excluded.close()


class TestOffByDefault:
    def test_no_report_unless_asked(self, vault: Vault) -> None:
        written = vault.writer.write("n.md", "[[Nope]]\n")
        edited = vault.writer.edit("n.md", old_text="Nope", new_text="Nah")
        appended = vault.writer.append("n.md", "[[Zed]]\n")
        assert written.unresolved_links is None
        assert edited.unresolved_links is None
        assert appended.unresolved_links is None

    def test_no_index_wait_unless_asked(self, tmp_path: Path) -> None:
        reporter = MagicMock(spec=UnresolvedLinkReporter)
        vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
        try:
            vault.writer._link_reporter = reporter
            vault.writer.write("n.md", "[[Nope]]\n")
            vault.writer.append("n.md", "[[Zed]]\n")
            vault.writer.edit("n.md", old_text="Zed", new_text="Zee")
        finally:
            vault.close()
        assert reporter.method_calls == []


class TestCheckCannotRun:
    def test_unbuilt_index_returns_none(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=False))
        try:
            with caplog.at_level(logging.WARNING):
                result = vault.writer.write(
                    "n.md", "[[Nope]]\n", report_unresolved_links=True
                )
        finally:
            vault.close()
        assert result.unresolved_links is None
        assert any(
            r.msg.startswith("unresolved_link_check_skipped")
            and "index_not_built" in r.msg
            for r in caplog.records
        )

    @pytest.mark.parametrize("method", sorted(_REPORTED_CALLS))
    def test_failed_refresh_returns_none_and_keeps_the_write(
        self,
        vault: Vault,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        method: str,
    ) -> None:
        vault.writer.write("n.md", "[[Old]] keep\n")
        reporter = vault.writer._link_reporter
        assert reporter is not None

        def fail() -> None:
            raise TimeoutError("index refresh timed out")

        reporter._refresh_index = fail
        with caplog.at_level(logging.WARNING):
            result = _REPORTED_CALLS[method](vault, "n.md")
        assert result.unresolved_links is None
        assert "[[New]]" in (tmp_path / "n.md").read_text(encoding="utf-8")
        assert any("index_refresh_failed" in r.msg for r in caplog.records)

    def test_unconfigured_reporter_refuses(self, vault: Vault) -> None:
        facet = WriterFacet(vault._doc_mgr)
        with pytest.raises(RuntimeError, match="not configured"):
            facet.write("n.md", "[[Nope]]\n", report_unresolved_links=True)

    def test_read_only_vault_refuses_before_reading(self, tmp_path: Path) -> None:
        vault = Vault(source_dir=tmp_path, settings=VaultSettings(read_only=True))
        try:
            with pytest.raises(ReadOnlyError):
                vault.writer.write(
                    "../outside.md", "[[Nope]]\n", report_unresolved_links=True
                )
        finally:
            vault.close()


class TestIntroduced:
    """The set arithmetic, against a stubbed index."""

    @staticmethod
    def _reporter(
        tmp_path: Path, rows: list[dict[str, object]]
    ) -> UnresolvedLinkReporter:
        fts = MagicMock()
        fts.get_outlinks.return_value = rows
        return UnresolvedLinkReporter(
            fts=fts,
            source_dir=tmp_path,
            attachment_extensions=None,
            is_queryable=lambda: True,
            refresh_index=lambda: None,
        )

    def test_a_later_writers_link_is_not_this_writes(self, tmp_path: Path) -> None:
        """Broken in the index but absent from this write's text: not reported."""
        rows = [
            {"link_type": "wikilink", "raw_target": "Mine", "target_exists": False},
            {"link_type": "wikilink", "raw_target": "Theirs", "target_exists": False},
        ]
        reporter = self._reporter(tmp_path, rows)
        assert reporter.introduced("n.md", [], [("wikilink", "Mine")]) == ["Mine"]

    def test_link_kind_is_part_of_identity(self, tmp_path: Path) -> None:
        rows = [
            {"link_type": "markdown", "raw_target": "x.md", "target_exists": False},
        ]
        reporter = self._reporter(tmp_path, rows)
        before = [("wikilink", "x.md")]
        after = [("wikilink", "x.md"), ("markdown", "x.md")]
        assert reporter.introduced("n.md", before, after) == ["x.md"]

    def test_queries_the_indexers_spelling_of_the_path(self, tmp_path: Path) -> None:
        reporter = self._reporter(tmp_path, [])
        reporter.introduced("./notes//n.md", [], [])
        reporter._fts.get_outlinks.assert_called_once_with("notes/n.md")  # type: ignore[attr-defined]

    def test_missing_note_has_no_links(self, tmp_path: Path) -> None:
        assert self._reporter(tmp_path, []).note_links("absent.md") == []


def _serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, report: str | None
) -> None:
    """Point make_server at a writable vault holding ``people/ada.md``."""
    (tmp_path / "people").mkdir()
    (tmp_path / "people" / "ada.md").write_text("# Ada\n", encoding="utf-8")
    (tmp_path / "n.md").write_text("[[Old]] keep\n", encoding="utf-8")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.delenv("MARKDOWN_VAULT_MCP_INDEX_PATH", raising=False)
    if report is None:
        monkeypatch.delenv("MARKDOWN_VAULT_MCP_REPORT_UNRESOLVED_LINKS", raising=False)
    else:
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_REPORT_UNRESOLVED_LINKS", report)


async def _call_writes(client: Client) -> dict[str, dict[str, Any]]:
    """Drive write, edit and append, each adding one resolving and one broken link."""
    await wait_for_mcp_writer_drain(client)
    calls = {
        "write": {"path": "w.md", "content": "[[ada]] [[NoW]]\n"},
        "edit": {"path": "n.md", "old_text": "keep", "new_text": "[[ada]] [[NoE]]"},
        "append": {"path": "n.md", "content": "[[Old]] [[NoA]]\n"},
    }
    return {
        name: (await client.call_tool(name, args)).structured_content
        for name, args in calls.items()
    }


class TestTools:
    async def test_setting_on_reports_on_each_tool(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _serve(tmp_path, monkeypatch, report="true")
        async with Client(make_server()) as client:
            results = await _call_writes(client)
        assert results["write"]["unresolved_links"] == ["NoW"]
        assert results["edit"]["unresolved_links"] == ["NoE"]
        assert results["append"]["unresolved_links"] == ["NoA"]

    @pytest.mark.parametrize("report", [None, "false"])
    async def test_setting_off_leaves_responses_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report: str | None
    ) -> None:
        _serve(tmp_path, monkeypatch, report=report)
        async with Client(make_server()) as client:
            results = await _call_writes(client)
        assert set(results["write"]) == {"path", "created"}
        assert set(results["edit"]) == {"path", "replacements", "match_type"}
        assert set(results["append"]) == {"path", "created"}

    async def test_attachment_write_carries_no_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _serve(tmp_path, monkeypatch, report="true")
        async with Client(make_server()) as client:
            await wait_for_mcp_writer_drain(client)
            result = await client.call_tool(
                "write",
                {
                    "path": "pic.png",
                    "content_base64": base64.b64encode(b"png").decode(),
                },
            )
        assert "unresolved_links" not in result.structured_content


class TestPayload:
    @pytest.mark.parametrize(
        "result",
        [
            WriteResult(path="n.md", created=True),
            EditResult(path="n.md", replacements=1),
        ],
        ids=["write", "edit"],
    )
    def test_null_report_is_kept_when_asked(
        self, result: WriteResult | EditResult
    ) -> None:
        """Null there says the check could not run; absence would hide that."""
        data = _write_payload(result, link_report=True)
        assert "unresolved_links" in data
        assert data["unresolved_links"] is None

    def test_report_is_dropped_when_not_asked(self) -> None:
        result = EditResult(path="n.md", replacements=1, unresolved_links=["X"])
        assert "unresolved_links" not in _write_payload(result)
