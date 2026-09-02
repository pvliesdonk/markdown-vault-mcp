"""End-to-end overwrite recovery on a git-backed vault (#1137).

Two pieces meet here: ``read(path, revision=...)`` returns a note as it stood
at one commit, and an overwriting ``write`` returns the ``previous_revision``
to read it back at. Together they make an overwrite reversible from the seat
it was made from, without shell access to the checkout.

The commits these tests read are seeded synchronously through ``git`` in the
fixture, never by waiting on the server's own write callback — that callback
runs on a queue thread, so a test that raced it would assert on whichever
commits happened to have landed.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from tests.server_factory import make_server

if TYPE_CHECKING:
    from pathlib import Path

_CLEAR_VARS = (
    "MARKDOWN_VAULT_MCP_GIT_REPO_URL",
    "MARKDOWN_VAULT_MCP_GIT_TOKEN",
    "MARKDOWN_VAULT_MCP_EMBEDDING_PROVIDER",
    "MARKDOWN_VAULT_MCP_BEARER_TOKEN",
    "MARKDOWN_VAULT_MCP_AUTH_MODE",
)

_ORIGINAL = "---\ntitle: Original\n---\n# Original\n\n## Detail\n\nthe first body\n"


def _git(cwd: Path, *args: str) -> str:
    """Run one git command in *cwd* and return its stdout."""
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _commit_all(vault: Path, message: str) -> str:
    """Stage the whole tree, commit, and return the new SHA."""
    _git(vault, "add", "-A")
    _git(vault, "commit", "-m", message)
    return _git(vault, "rev-parse", "HEAD").strip()


@pytest.fixture
def git_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A read-write vault that is its own git repository, with one commit.

    Commit-only mode: no remote is configured, so the strategy commits
    locally and never pulls or pushes.
    """
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)
    vault = tmp_path / "vault"
    vault.mkdir()
    _git(vault, "init")
    _git(vault, "config", "user.email", "test@test.com")
    _git(vault, "config", "user.name", "Test")
    (vault / "note.md").write_text(_ORIGINAL, encoding="utf-8")
    _commit_all(vault, "add note")

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S", "0")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S", "0")
    return vault


@pytest.fixture
def commitless_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git-backed vault whose branch has no commit yet.

    What a managed clone of an empty remote looks like on first boot, and the
    one state where ``git log`` exits non-zero rather than reporting an empty
    history.
    """
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)
    vault = tmp_path / "vault"
    vault.mkdir()
    _git(vault, "init")
    _git(vault, "config", "user.email", "test@test.com")
    _git(vault, "config", "user.name", "Test")
    (vault / "note.md").write_text(_ORIGINAL, encoding="utf-8")

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S", "0")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S", "0")
    return vault


@pytest.fixture
def plain_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A read-write vault that is not inside a git repository."""
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text(_ORIGINAL, encoding="utf-8")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    return vault


@pytest.fixture
def bom_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A committed, unmodified note whose bytes exercise the shaping rules.

    Carries a UTF-8 BOM, frontmatter, and a section, so a single read of it
    covers decode, frontmatter parsing, title resolution, and extraction. The
    working tree matches the commit, which is what makes the two readers
    comparable.
    """
    for var in _CLEAR_VARS:
        monkeypatch.delenv(var, raising=False)
    vault = tmp_path / "vault"
    vault.mkdir()
    _git(vault, "init")
    _git(vault, "config", "user.email", "test@test.com")
    _git(vault, "config", "user.name", "Test")
    (vault / "note.md").write_bytes(
        "\ufeff---\ntitle: Bommy\n---\n# Bommy\n\n## Detail\n\nthe detail body\n".encode()
    )
    _commit_all(vault, "add note")

    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(vault))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_READ_ONLY", "false")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S", "0")
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S", "0")
    return vault


def _data(result: Any) -> Any:
    """Return a tool result's structured payload."""
    return result.data


class TestOverwriteRecovery:
    """The two pieces used together, as a caller would."""

    async def test_write_reports_the_revision_to_recover_from(
        self, git_vault: Path
    ) -> None:
        """An overwrite names the note's newest commit before it."""
        seeded = _git(git_vault, "rev-parse", "HEAD").strip()

        server = make_server()
        async with Client(server) as client:
            written = _data(
                await client.call_tool(
                    "write", {"path": "note.md", "content": "replacement\n"}
                )
            )

        assert written["created"] is False
        assert written["previous_revision"] == seeded

    @pytest.mark.usefixtures("git_vault")
    async def test_the_reported_revision_reads_back_the_replaced_content(self) -> None:
        """The breadcrumb is executable: write, then read at what it named."""
        server = make_server()
        async with Client(server) as client:
            written = _data(
                await client.call_tool(
                    "write", {"path": "note.md", "content": "replacement\n"}
                )
            )
            recovered = _data(
                await client.call_tool(
                    "read",
                    {"path": "note.md", "revision": written["previous_revision"]},
                )
            )
            current = _data(await client.call_tool("read", {"path": "note.md"}))

        assert recovered["content"] == _ORIGINAL
        assert recovered["revision"] == written["previous_revision"]
        assert recovered["historical_path"] == "note.md"
        assert recovered["title"] == "Original"
        assert recovered["frontmatter"] == {"title": "Original"}
        assert current["content"] == "replacement\n"

    @pytest.mark.usefixtures("git_vault")
    async def test_recovered_content_restores_the_note_verbatim(self) -> None:
        """The documented two-step round-trips, frontmatter included.

        The recovered content is the whole file, so it goes back through
        ``write`` as ``content`` alone — passing ``frontmatter`` as well would
        emit the YAML header twice.
        """
        server = make_server()
        async with Client(server) as client:
            written = _data(
                await client.call_tool(
                    "write", {"path": "note.md", "content": "replacement\n"}
                )
            )
            recovered = _data(
                await client.call_tool(
                    "read",
                    {"path": "note.md", "revision": written["previous_revision"]},
                )
            )
            current = _data(await client.call_tool("read", {"path": "note.md"}))
            await client.call_tool(
                "write",
                {
                    "path": "note.md",
                    "content": recovered["content"],
                    "if_match": current["etag"],
                },
            )
            restored = _data(await client.call_tool("read", {"path": "note.md"}))

        assert restored["content"] == _ORIGINAL
        assert restored["frontmatter"] == {"title": "Original"}

    @pytest.mark.usefixtures("git_vault")
    async def test_no_breadcrumb_when_the_write_creates_the_note(self) -> None:
        """Nothing was replaced, so there is nothing to point at."""
        server = make_server()
        async with Client(server) as client:
            written = _data(
                await client.call_tool(
                    "write", {"path": "fresh.md", "content": "new\n"}
                )
            )

        assert written["created"] is True
        assert "previous_revision" not in written

    async def test_no_breadcrumb_when_the_note_has_no_commit(
        self, git_vault: Path
    ) -> None:
        """A note written but never committed has no revision to name."""
        (git_vault / "uncommitted.md").write_text("on disk only\n", encoding="utf-8")

        server = make_server()
        async with Client(server) as client:
            written = _data(
                await client.call_tool(
                    "write", {"path": "uncommitted.md", "content": "replacement\n"}
                )
            )

        assert written["created"] is False
        assert "previous_revision" not in written

    @pytest.mark.usefixtures("commitless_vault")
    async def test_write_succeeds_when_the_branch_has_no_commit_yet(self) -> None:
        """The breadcrumb is best-effort: it never fails the write it annotates.

        ``git log`` exits non-zero on an unborn branch rather than reporting an
        empty history, so a breadcrumb read that let that surface would abort
        every write on a repository that has not been committed to yet —
        including the one creating its first note.
        """
        server = make_server()
        async with Client(server) as client:
            written = _data(
                await client.call_tool(
                    "write", {"path": "note.md", "content": "replacement\n"}
                )
            )

        assert written["path"] == "note.md"
        assert "previous_revision" not in written

    @pytest.mark.usefixtures("plain_vault")
    async def test_no_breadcrumb_on_a_vault_without_git(self) -> None:
        """Without git write-through there is no history to offer."""
        server = make_server()
        async with Client(server) as client:
            written = _data(
                await client.call_tool(
                    "write", {"path": "note.md", "content": "replacement\n"}
                )
            )

        assert written["created"] is False
        assert "previous_revision" not in written

    async def test_no_breadcrumb_on_an_attachment_overwrite(
        self, git_vault: Path
    ) -> None:
        """Attachments have no revision read, so they are offered no route to one."""
        (git_vault / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        _commit_all(git_vault, "add attachment")

        server = make_server()
        async with Client(server) as client:
            written = _data(
                await client.call_tool(
                    "write",
                    {"path": "shot.png", "content_base64": "aGVsbG8="},
                )
            )

        assert written["created"] is False
        assert "previous_revision" not in written


class TestReadAtRevision:
    """``read(path, revision=...)`` on its own."""

    async def test_reads_across_an_overwrite_and_a_rename(
        self, git_vault: Path
    ) -> None:
        """The note's earlier name is resolved even when its content changed too.

        Both happening between the revision and now is the shape that defeats
        whole-range rename detection, and it is exactly what an overwrite
        followed by a reorganisation produces.
        """
        seeded = _git(git_vault, "rev-parse", "HEAD").strip()
        (git_vault / "note.md").write_text("wholly different\n", encoding="utf-8")
        _commit_all(git_vault, "overwrite note")
        _git(git_vault, "mv", "note.md", "archive.md")
        _commit_all(git_vault, "rename note")

        server = make_server()
        async with Client(server) as client:
            recovered = _data(
                await client.call_tool(
                    "read", {"path": "archive.md", "revision": seeded}
                )
            )

        assert recovered["content"] == _ORIGINAL
        assert recovered["historical_path"] == "note.md"
        assert recovered["path"] == "archive.md"

    async def test_reads_a_note_that_no_longer_exists(self, git_vault: Path) -> None:
        """A deleted note is still readable at a revision that had it."""
        seeded = _git(git_vault, "rev-parse", "HEAD").strip()
        (git_vault / "note.md").unlink()
        _commit_all(git_vault, "delete note")

        server = make_server()
        async with Client(server) as client:
            recovered = _data(
                await client.call_tool("read", {"path": "note.md", "revision": seeded})
            )

        assert recovered["content"] == _ORIGINAL

    async def test_section_narrows_a_revision_read(self, git_vault: Path) -> None:
        """``section`` composes with ``revision`` as it does with a current read."""
        seeded = _git(git_vault, "rev-parse", "HEAD").strip()

        server = make_server()
        async with Client(server) as client:
            recovered = _data(
                await client.call_tool(
                    "read",
                    {"path": "note.md", "revision": seeded, "section": "Detail"},
                )
            )

        assert recovered["content"].strip() == "the first body"
        assert recovered["frontmatter"] == {}

    @pytest.mark.usefixtures("git_vault")
    async def test_current_read_is_unchanged_without_revision(self) -> None:
        """The working-tree read keeps its own shape: an etag, and no revision."""
        server = make_server()
        async with Client(server) as client:
            current = _data(await client.call_tool("read", {"path": "note.md"}))

        assert current["etag"]
        assert "revision" not in current

    @pytest.mark.usefixtures("plain_vault")
    async def test_vault_without_git_reports_the_reason(self) -> None:
        """No silent fall-back to current content on a vault with no history.

        A config-built server always has a strategy object, so the refusal
        here comes from the git layer finding no repository around the source
        directory rather than from the manager's no-strategy branch (which
        library callers constructing ``Vault(git_strategy=None)`` reach).
        """
        server = make_server()
        async with Client(server) as client:
            with pytest.raises(ToolError, match="git-backed"):
                await client.call_tool(
                    "read", {"path": "note.md", "revision": "abcd1234"}
                )

    async def test_attachment_at_a_revision_is_rejected(self, git_vault: Path) -> None:
        """Revision reads return text; an attachment would be binary."""
        seeded = _git(git_vault, "rev-parse", "HEAD").strip()

        server = make_server()
        async with Client(server) as client:
            with pytest.raises(ToolError, match="notes only"):
                await client.call_tool("read", {"path": "shot.png", "revision": seeded})

    @pytest.mark.usefixtures("git_vault")
    async def test_symbolic_revision_is_rejected(self) -> None:
        """Only plain SHAs, the form get_history and write results hand out."""
        server = make_server()
        async with Client(server) as client:
            with pytest.raises(ToolError, match="Invalid revision"):
                await client.call_tool(
                    "read", {"path": "note.md", "revision": "HEAD~1"}
                )

    @pytest.mark.usefixtures("git_vault")
    async def test_unknown_revision_is_rejected(self) -> None:
        server = make_server()
        async with Client(server) as client:
            with pytest.raises(ToolError, match="unknown revision"):
                await client.call_tool(
                    "read", {"path": "note.md", "revision": "0" * 40}
                )


class TestParityWithTheWorkingTreeRead:
    """A revision read and a working-tree read of the same bytes agree.

    The revision path is a second reader: it acquires bytes from git rather
    than from disk, then shapes them. Every invariant the on-disk reader
    already encodes has to be carried across, and the review round on #1281
    found four that had not been — the BOM strip, the cap's relationship to
    ``section=``, path confinement, and tolerance of a failed git call. Those
    were four patches to one gap.

    These pin the gap itself: for a note whose working tree still matches its
    newest commit, the two readers must produce the same document. A future
    invariant dropped on the way across fails here rather than in review.
    """

    async def test_same_bytes_shape_the_same_document(self, bom_vault: Path) -> None:
        """Frontmatter, title, and body survive both routes identically.

        The BOM is the load-bearing part: the on-disk reader strips it before
        parsing (#673), and a revision reader that did not would return a note
        whose frontmatter block sits behind a ``\ufeff`` and goes unparsed.
        """
        head = _git(bom_vault, "rev-parse", "HEAD").strip()

        server = make_server()
        async with Client(server) as client:
            current = _data(await client.call_tool("read", {"path": "note.md"}))
            historical = _data(
                await client.call_tool("read", {"path": "note.md", "revision": head})
            )

        assert historical["content"] == current["content"]
        assert historical["frontmatter"] == current["frontmatter"]
        assert historical["title"] == current["title"]
        assert historical["folder"] == current["folder"]
        assert current["frontmatter"] == {"title": "Bommy"}

    async def test_same_section_comes_back_from_both(self, bom_vault: Path) -> None:
        """``section=`` narrows identically on both routes, once the index is up.

        The barrier below is not ceremony. A working-tree section read resolves
        the note through the FTS index first, so it fails on a cold index; the
        revision read shapes the section straight from the blob and answers
        either way. The two agree on *content*, and this pins that — but they
        do not agree on when they can answer, which is why the parity is
        asserted after the index is ready rather than asserted unconditionally.
        """
        head = _git(bom_vault, "rev-parse", "HEAD").strip()

        server = make_server()
        async with Client(server) as client:
            await client.call_tool("list_documents", {"wait_for_pending_writes": True})
            current = _data(
                await client.call_tool("read", {"path": "note.md", "section": "Detail"})
            )
            historical = _data(
                await client.call_tool(
                    "read",
                    {"path": "note.md", "revision": head, "section": "Detail"},
                )
            )

        assert historical["content"] == current["content"]
        assert historical["frontmatter"] == current["frontmatter"] == {}

    async def test_the_read_cap_governs_both_the_same_way(
        self, bom_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Whole-document reads are capped on both routes; section reads are not.

        The asymmetry is the point: the cap's own error sends the caller to
        ``section=``, so a route that capped section reads too would refuse
        the recovery it had just recommended.
        """
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_MAX_NOTE_READ_BYTES", "32")
        head = _git(bom_vault, "rev-parse", "HEAD").strip()

        server = make_server()
        async with Client(server) as client:
            with pytest.raises(ToolError, match="MAX_NOTE_READ_BYTES"):
                await client.call_tool("read", {"path": "note.md"})
            with pytest.raises(ToolError, match="MAX_NOTE_READ_BYTES"):
                await client.call_tool("read", {"path": "note.md", "revision": head})

            current = _data(
                await client.call_tool("read", {"path": "note.md", "section": "Detail"})
            )
            historical = _data(
                await client.call_tool(
                    "read",
                    {"path": "note.md", "revision": head, "section": "Detail"},
                )
            )

        assert historical["content"] == current["content"]
