"""``index.md`` regeneration after every change that can alter a listing (#1392).

The maintainer used to refresh a folder's ``index.md`` only after an MCP
``write`` / ``edit`` / ``append`` into that folder. A rename, delete or move,
and any change that arrived through a pull or the file watcher, left the
listing stale until such a write happened to land there.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp._okf_write import okf_write_suppressed
from markdown_vault_mcp.vault import Vault
from tests.conftest import wait_for_writer_drain

if TYPE_CHECKING:
    from pathlib import Path

_ROOT_INDEX = '---\nokf_version: "0.2"\n---\n# Root\n'


def _note(title: str) -> str:
    return f"---\ntitle: {title}\ntype: Note\n---\n# {title}\n"


def _vault(root: Path, **kw: object) -> Vault:
    col = Vault(source_dir=root, read_only=False, okf_mode="on", okf_write=True, **kw)  # type: ignore[arg-type]
    col.index.build_index()
    return col


@pytest.fixture
def enforced(tmp_path: Path) -> Vault:
    root = tmp_path / "vault"
    (root / "guides").mkdir(parents=True)
    (root / "index.md").write_text(_ROOT_INDEX, encoding="utf-8")
    (root / "guides" / "a.md").write_text(_note("A"), encoding="utf-8")
    col = _vault(root)
    # Settle the listings once so later assertions see deltas, not seeding.
    # Suppressed as the MCP tool does it: the library facet alone would
    # stamp provenance into the index.
    with okf_write_suppressed():
        col.writer.okf_generate_index(folder="guides")
        col.writer.okf_generate_index()
    wait_for_writer_drain(col)
    yield col
    col.close()


def _index(col: Vault, folder: str = "") -> str:
    path = f"{folder}/index.md" if folder else "index.md"
    return (col._source_dir / path).read_text(encoding="utf-8")


class TestReindexReportsChangedFolders:
    def test_folders_of_added_modified_and_deleted_paths(self, tmp_path: Path) -> None:
        root = tmp_path / "v"
        (root / "guides").mkdir(parents=True)
        (root / "notes").mkdir()
        (root / "top.md").write_text(_note("Top"), encoding="utf-8")
        (root / "notes" / "gone.md").write_text(_note("Gone"), encoding="utf-8")
        col = Vault(source_dir=root, read_only=False)
        try:
            col.index.build_index()
            (root / "guides" / "new.md").write_text(_note("New"), encoding="utf-8")
            (root / "top.md").write_text(_note("Top edited"), encoding="utf-8")
            (root / "notes" / "gone.md").unlink()
            result = col.index.reindex()
            assert result.folders_changed == ("", "guides", "notes")
            assert col.index.reindex().folders_changed == ()
        finally:
            col.close()


class TestExternalChangesRegenerate:
    def test_reindex_lists_a_note_that_arrived_on_disk(self, enforced: Vault) -> None:
        root = enforced._source_dir
        (root / "guides" / "b.md").write_text(_note("B"), encoding="utf-8")
        enforced.index.reindex()
        wait_for_writer_drain(enforced)
        assert "[B](/guides/b.md)" in _index(enforced, "guides")

    def test_reindex_adds_the_parent_pointer_for_a_new_subfolder(
        self, enforced: Vault
    ) -> None:
        root = enforced._source_dir
        (root / "guides" / "deep").mkdir()
        (root / "guides" / "deep" / "d.md").write_text(_note("D"), encoding="utf-8")
        enforced.index.reindex()
        wait_for_writer_drain(enforced)
        assert "[D](/guides/deep/d.md)" in _index(enforced, "guides/deep")
        assert "(/guides/deep/index.md)" in _index(enforced, "guides")

    def test_async_reindex_regenerates_before_its_future_resolves(
        self, enforced: Vault
    ) -> None:
        """The boot reconciliation and the ``reindex`` tool use the async path."""
        root = enforced._source_dir
        (root / "guides" / "b.md").write_text(_note("B"), encoding="utf-8")
        enforced.index.reindex_async().result(timeout=30)
        assert "[B](/guides/b.md)" in _index(enforced, "guides")

    def test_reindex_drops_a_note_deleted_on_disk(self, enforced: Vault) -> None:
        (enforced._source_dir / "guides" / "a.md").unlink()
        enforced.index.reindex()
        wait_for_writer_drain(enforced)
        assert "/guides/a.md" not in _index(enforced, "guides")

    def test_unchanged_listing_is_not_rewritten(self, enforced: Vault) -> None:
        """A regeneration that changes nothing must not touch the file, or the
        file watcher would see its own write and reindex forever (#830)."""
        path = enforced._source_dir / "guides" / "index.md"
        before = path.stat().st_mtime_ns
        with okf_write_suppressed():
            enforced.writer.okf_generate_index(folder="guides")
        wait_for_writer_drain(enforced)
        assert path.stat().st_mtime_ns == before


class TestServerOperationsRegenerate:
    def test_rename_across_folders_updates_both_listings(self, enforced: Vault) -> None:
        (enforced._source_dir / "notes").mkdir()
        enforced.writer.rename("guides/a.md", "notes/a.md")
        wait_for_writer_drain(enforced)
        assert "/guides/a.md" not in _index(enforced, "guides")
        assert "[A](/notes/a.md)" in _index(enforced, "notes")

    def test_delete_drops_the_entry(self, enforced: Vault) -> None:
        enforced.writer.delete("guides/a.md")
        wait_for_writer_drain(enforced)
        assert "/guides/a.md" not in _index(enforced, "guides")

    def test_move_folder_refreshes_a_carried_nested_listing(
        self, enforced: Vault
    ) -> None:
        """The moved listings are found on disk, so a slow drain cannot hide them."""
        root = enforced._source_dir
        (root / "guides" / "deep").mkdir()
        (root / "guides" / "deep" / "d.md").write_text(_note("D"), encoding="utf-8")
        (root / "guides" / "deep" / "index.md").write_text(
            "# deep\n\n- [D](/guides/deep/d.md)\n", encoding="utf-8"
        )
        enforced.writer.move_folder("guides", "docs/guides")
        wait_for_writer_drain(enforced)
        assert "[D](/docs/guides/deep/d.md)" in _index(enforced, "docs/guides/deep")

    def test_move_folder_updates_parents_and_the_moved_listing(
        self, enforced: Vault
    ) -> None:
        enforced.writer.move_folder("guides", "docs/guides")
        wait_for_writer_drain(enforced)
        assert "(/guides/index.md)" not in _index(enforced)
        assert "(/docs/index.md)" in _index(enforced)
        assert "(/docs/guides/index.md)" in _index(enforced, "docs")
        assert "[A](/docs/guides/a.md)" in _index(enforced, "docs/guides")

    def test_write_into_a_new_subfolder_updates_the_parent_pointer(
        self, enforced: Vault
    ) -> None:
        enforced.writer.write("guides/deep/d.md", _note("D"))
        wait_for_writer_drain(enforced)
        assert "(/guides/deep/index.md)" in _index(enforced, "guides")


class TestCommitScope:
    def test_regeneration_after_reindex_is_one_commit(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.git import GitWriteStrategy

        repo = tmp_path / "gitvault"
        (repo / "guides").mkdir(parents=True)
        (repo / "notes").mkdir()
        (repo / "index.md").write_text(_ROOT_INDEX, encoding="utf-8")
        (repo / "guides" / "a.md").write_text(_note("A"), encoding="utf-8")
        (repo / "notes" / "n.md").write_text(_note("N"), encoding="utf-8")

        def git(*args: str) -> str:
            return subprocess.run(
                ["git", "-C", str(repo), *args],
                capture_output=True,
                check=True,
                text=True,
            ).stdout

        git("init", "-q")
        git("config", "user.email", "t@t")
        git("config", "user.name", "T")
        git("add", ".")
        git("commit", "-q", "-m", "base")
        strategy = GitWriteStrategy(enable_push=False, push_delay_s=0)
        # As the server wires it: the strategy is both the store and the
        # write callback that commits.
        col = _vault(repo, git_strategy=strategy, on_write=strategy)
        try:
            # Two folders change outside the server, as a pull would leave them.
            (repo / "guides" / "b.md").write_text(_note("B"), encoding="utf-8")
            (repo / "notes" / "m.md").write_text(_note("M"), encoding="utf-8")
            git("add", ".")
            git("commit", "-q", "-m", "external")
            before = int(git("rev-list", "--count", "HEAD"))
            col.index.reindex()
            wait_for_writer_drain(col)
            col._write_callback.drain()
            after = int(git("rev-list", "--count", "HEAD"))
            assert after == before + 1, git("log", "--oneline", "-5")
            dirty = [
                line
                for line in git("status", "--porcelain").splitlines()
                if ".markdown_vault_mcp" not in line
            ]
            assert dirty == []
        finally:
            col.close()


class TestAsyncChaining:
    def test_writer_failure_reaches_the_caller_and_skips_the_hook(self) -> None:
        from concurrent.futures import Future

        from markdown_vault_mcp.facets.index import IndexFacet

        failed: Future[object] = Future()
        failed.set_exception(RuntimeError("writer job failed"))
        coordinator = type("C", (), {"reindex_async": lambda _self: failed})()
        calls: list[object] = []
        facet = IndexFacet(
            coordinator=coordinator,  # type: ignore[arg-type]
            index_mgr=None,  # type: ignore[arg-type]
            after_index_change=calls.append,
        )
        out = facet.reindex_async()
        with pytest.raises(RuntimeError, match="writer job failed"):
            out.result(timeout=10)
        assert calls == []

    def test_hook_failure_does_not_fail_the_reindex(self) -> None:
        from concurrent.futures import Future

        from markdown_vault_mcp.facets.index import IndexFacet
        from markdown_vault_mcp.types import ReindexResult

        done: Future[object] = Future()
        result = ReindexResult(added=1, modified=0, deleted=0, unchanged=0)
        done.set_result(result)
        coordinator = type("C", (), {"reindex_async": lambda _self: done})()

        def _boom(_r: object) -> None:
            raise RuntimeError("hook broke")

        facet = IndexFacet(
            coordinator=coordinator,  # type: ignore[arg-type]
            index_mgr=None,  # type: ignore[arg-type]
            after_index_change=_boom,
        )
        assert facet.reindex_async().result(timeout=10) is result


class TestFullBuild:
    """A full build cannot name a delta, so it refreshes every listing (#1392)."""

    def test_a_forced_rebuild_refreshes_the_listings(self, enforced: Vault) -> None:
        root = enforced._source_dir
        (root / "guides" / "b.md").write_text(_note("B"), encoding="utf-8")
        enforced.index.build_index(force=True)
        wait_for_writer_drain(enforced)
        assert "[B](/guides/b.md)" in _index(enforced, "guides")

    def test_an_async_forced_rebuild_refreshes_before_it_resolves(
        self, enforced: Vault
    ) -> None:
        """The `reindex(force=True)` tool takes this path."""
        root = enforced._source_dir
        (root / "guides" / "c.md").write_text(_note("C"), encoding="utf-8")
        enforced.index.build_index_async(force=True).result(timeout=30)
        assert "[C](/guides/c.md)" in _index(enforced, "guides")

    def test_a_stale_listing_with_no_indexed_notes_is_refreshed(
        self, tmp_path: Path
    ) -> None:
        """Nothing will ever write there again, so only a full build fixes it."""
        root = tmp_path / "vault"
        (root / "ghost").mkdir(parents=True)
        (root / "index.md").write_text(_ROOT_INDEX, encoding="utf-8")
        # A listing whose notes are gone: no indexed row names this folder.
        (root / "ghost" / "index.md").write_text(
            "# ghost\n\n- [Vanished](/ghost/vanished.md)\n", encoding="utf-8"
        )
        col = _vault(root)
        try:
            col.index.build_index(force=True)
            wait_for_writer_drain(col)
            assert "/ghost/vanished.md" not in _index(col, "ghost")
        finally:
            col.close()

    def test_a_warm_restart_refreshes_nothing(self) -> None:
        """The short-circuit rebuilt nothing, so there is nothing to follow."""
        from markdown_vault_mcp.facets.index import IndexFacet
        from markdown_vault_mcp.types import IndexStats

        seen: list[object] = []
        coordinator = type(
            "C",
            (),
            {
                "build_index": lambda _self, **_kw: IndexStats(
                    documents_indexed=3, chunks_indexed=0, skipped=0, rebuilt=False
                )
            },
        )()
        facet = IndexFacet(
            coordinator=coordinator,  # type: ignore[arg-type]
            index_mgr=None,  # type: ignore[arg-type]
            after_index_change=seen.append,
        )
        facet.build_index()
        assert seen == []

    def test_a_real_build_asks_for_every_folder(self) -> None:
        from markdown_vault_mcp.facets.index import IndexFacet
        from markdown_vault_mcp.types import IndexStats

        seen: list[object] = []
        coordinator = type(
            "C",
            (),
            {
                "build_index": lambda _self, **_kw: IndexStats(
                    documents_indexed=3, chunks_indexed=9, skipped=0
                )
            },
        )()
        facet = IndexFacet(
            coordinator=coordinator,  # type: ignore[arg-type]
            index_mgr=None,  # type: ignore[arg-type]
            after_index_change=seen.append,
        )
        facet.build_index()
        assert seen == [None]


class TestFollowUpScope:
    """The follow-up can outlive its request, so it never joins its scope."""

    def test_the_follow_up_binds_its_own_scope(self) -> None:
        """A promoted reindex closes the tool scope before the job finishes."""
        from concurrent.futures import Future

        from markdown_vault_mcp._commit_scope import (
            bound_commit_scope,
            current_commit_scope,
        )
        from markdown_vault_mcp.facets.index import IndexFacet
        from markdown_vault_mcp.types import ReindexResult

        done: Future[object] = Future()
        done.set_result(ReindexResult(added=1, modified=0, deleted=0, unchanged=0))
        coordinator = type("C", (), {"reindex_async": lambda _self: done})()
        seen: list[object] = []
        facet = IndexFacet(
            coordinator=coordinator,  # type: ignore[arg-type]
            index_mgr=None,  # type: ignore[arg-type]
            after_index_change=lambda _f: seen.append(current_commit_scope()),
        )
        with bound_commit_scope("reindex"):
            facet.reindex_async().result(timeout=10)
        assert seen == [None]


class TestRenameToAReservedName:
    """A rename can create a reserved file as directly as a write can."""

    def test_a_note_renamed_to_index_is_not_overwritten(self, enforced: Vault) -> None:
        root = enforced._source_dir
        (root / "notes").mkdir()
        enforced.writer.write("notes/n.md", _note("N"))
        wait_for_writer_drain(enforced)
        # Into a folder with no listing of its own, so nothing is overwritten
        # by the rename itself — only the refresh behind it could be.
        enforced.writer.rename("notes/n.md", "fresh/index.md")
        wait_for_writer_drain(enforced)

        kept = (root / "fresh" / "index.md").read_text(encoding="utf-8")
        assert "# N" in kept, kept

    def test_a_same_folder_rename_to_index_is_not_overwritten(
        self, enforced: Vault
    ) -> None:
        """The destination folder is reached through the source path too."""
        root = enforced._source_dir
        (root / "notes").mkdir()
        enforced.writer.write("notes/n.md", _note("N"))
        wait_for_writer_drain(enforced)
        (root / "notes" / "index.md").unlink()
        enforced.writer.rename("notes/n.md", "notes/index.md")
        wait_for_writer_drain(enforced)

        kept = (root / "notes" / "index.md").read_text(encoding="utf-8")
        assert "# N" in kept, kept


class TestCancellation:
    """A cancelled chained future must not leave the writer job orphaned."""

    def test_cancelling_the_chained_future_cancels_the_writer_job(self) -> None:
        from concurrent.futures import Future

        from markdown_vault_mcp.facets.index import IndexFacet

        done: Future[object] = Future()
        coordinator = type("C", (), {"reindex_async": lambda _self: done})()
        facet = IndexFacet(
            coordinator=coordinator,  # type: ignore[arg-type]
            index_mgr=None,  # type: ignore[arg-type]
            after_index_change=lambda _f: None,
        )
        out = facet.reindex_async()
        assert out.cancel()
        assert done.cancelled()


class TestFollowUpLifecycle:
    """Shutdown must not close the index out from under a follow-up."""

    def test_close_waits_for_a_running_follow_up(self) -> None:
        import threading
        from concurrent.futures import Future

        from markdown_vault_mcp.facets.index import IndexFacet
        from markdown_vault_mcp.types import ReindexResult

        done: Future[object] = Future()
        coordinator = type("C", (), {"reindex_async": lambda _self: done})()
        started, release, finished = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )

        def _slow(_folders: object) -> None:
            started.set()
            release.wait(timeout=5)
            finished.set()

        facet = IndexFacet(
            coordinator=coordinator,  # type: ignore[arg-type]
            index_mgr=None,  # type: ignore[arg-type]
            after_index_change=_slow,
        )
        facet.reindex_async()
        done.set_result(ReindexResult(added=1, modified=0, deleted=0, unchanged=0))
        assert started.wait(timeout=5)
        release.set()
        facet.stop_followers(timeout=5)
        assert finished.is_set()

    def test_a_follow_up_registered_during_shutdown_does_no_work(self) -> None:
        """A job still queued at shutdown registers only as it completes."""
        from concurrent.futures import Future

        from markdown_vault_mcp.facets.index import IndexFacet
        from markdown_vault_mcp.types import ReindexResult

        done: Future[object] = Future()
        coordinator = type("C", (), {"reindex_async": lambda _self: done})()
        calls: list[object] = []
        facet = IndexFacet(
            coordinator=coordinator,  # type: ignore[arg-type]
            index_mgr=None,  # type: ignore[arg-type]
            after_index_change=calls.append,
        )
        out = facet.reindex_async()
        # Shutdown begins while the job is still queued.
        facet.stop_followers(timeout=1)
        done.set_result(ReindexResult(added=1, modified=0, deleted=0, unchanged=0))
        out.result(timeout=5)
        assert calls == []


class TestRenameToTheLog:
    """Only the listing is spared; the log is not generated content."""

    def test_a_rename_to_log_still_refreshes_the_listing(self, enforced: Vault) -> None:
        root = enforced._source_dir
        (root / "notes").mkdir()
        enforced.writer.write("notes/n.md", _note("N"))
        wait_for_writer_drain(enforced)
        (root / "notes" / "log.md").unlink()
        enforced.writer.rename("notes/n.md", "notes/log.md")
        wait_for_writer_drain(enforced)

        listing = (root / "notes" / "index.md").read_text(encoding="utf-8")
        assert "/notes/n.md" not in listing, listing
        assert "# N" in (root / "notes" / "log.md").read_text(encoding="utf-8")

    def test_a_writer_side_cancellation_resolves_the_chained_future(self) -> None:
        """The writer cancels its queue when its worker dies; nothing may hang."""
        from concurrent.futures import Future

        from markdown_vault_mcp.facets.index import IndexFacet

        done: Future[object] = Future()
        coordinator = type("C", (), {"reindex_async": lambda _self: done})()
        facet = IndexFacet(
            coordinator=coordinator,  # type: ignore[arg-type]
            index_mgr=None,  # type: ignore[arg-type]
            after_index_change=lambda _f: None,
        )
        out = facet.reindex_async()
        # Cancelled from the writer's side, not through ``out``.
        assert done.cancel()
        assert out.done(), "a caller awaiting this would wait forever"
        assert out.cancelled()


class TestExcludedPurge:
    """A purge names folders nothing else in a reindex does (#1392).

    Needs a *persisted* index: with an in-memory one every start is a full
    build, which refreshes everything anyway and hides the gap.
    """

    def test_a_newly_excluded_note_refreshes_its_listing(self, tmp_path: Path) -> None:
        from markdown_vault_mcp.vault import Vault

        root = tmp_path / "vault"
        (root / "guides").mkdir(parents=True)
        (root / "index.md").write_text(_ROOT_INDEX, encoding="utf-8")
        (root / "guides" / "a.md").write_text(_note("A"), encoding="utf-8")
        (root / "guides" / "draft.md").write_text(_note("Draft"), encoding="utf-8")
        index_path = tmp_path / "fts.db"

        col = Vault(source_dir=root, read_only=False, index_path=index_path)
        try:
            col.index.build_index()
        finally:
            col.close()

        # The operator excludes it and restarts. The file is unchanged on
        # disk, so the tracker reports nothing; only the purge sees it, and
        # a warm restart does not refresh everything.
        col = Vault(
            source_dir=root,
            read_only=False,
            okf_mode="on",
            okf_write=True,
            index_path=index_path,
            exclude_patterns=["guides/draft.md"],
        )
        try:
            stats = col.index.build_index()
            assert stats.rebuilt is False, "a full build would mask the gap"
            result = col.index.reindex()
            assert result.deleted == 0, "the tracker does not see it"
            assert result.folders_changed == ("guides",), result
            wait_for_writer_drain(col)
            assert "/guides/draft.md" not in _index(col, "guides")
        finally:
            col.close()


class TestAttachmentOnlyMove:
    """`move_folder` supports attachment-only subtrees (#1392 round 11)."""

    def test_moving_a_folder_of_attachments_creates_no_listing(
        self, enforced: Vault
    ) -> None:
        root = enforced._source_dir
        (root / "assets").mkdir()
        (root / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        enforced.writer.move_folder("assets", "media/assets")
        wait_for_writer_drain(enforced)

        assert not (root / "media" / "assets" / "index.md").exists()
        assert not (root / "media" / "index.md").exists()
