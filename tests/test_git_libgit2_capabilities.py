"""Round-three local Git parity regressions."""

import pytest

from markdown_vault_mcp.exceptions import ConfigurationError, InvalidRequestError
from markdown_vault_mcp.git.libgit2 import Libgit2Store
from markdown_vault_mcp.git.libgit2_history import _date
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from tests.fixtures.libgit2 import _git
from tests.fixtures.libgit2 import isolated_repo as isolated_repo


def test_encoding_refused_at_open_and_after_open(isolated_repo):
    repo = isolated_repo
    store = Libgit2Store(repo)
    (repo / ".gitattributes").write_text("*.md working-tree-encoding=ISO-8859-1\n")
    with pytest.raises(
        ConfigurationError, match=r"working-tree-encoding.*GitWriteStrategy"
    ):
        Libgit2Store(repo)
    note = repo / "new.md"
    note.write_text("café\n")
    before = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(ConfigurationError, match="working-tree-encoding"):
        store(note, "", "write")
    assert _git(repo, "rev-parse", "HEAD") == before


@pytest.mark.parametrize(
    "attribute", ["-working-tree-encoding", "!working-tree-encoding"]
)
def test_encoding_unset_allowed(isolated_repo, attribute):
    (isolated_repo / ".gitattributes").write_text(f"*.md {attribute}\n")
    note = isolated_repo / "new.md"
    note.write_text("café\n")
    Libgit2Store(isolated_repo)(note, "", "write")
    assert _git(isolated_repo, "show", "HEAD:new.md") == "café\n"


def test_force_push_missing_and_present_tracking(isolated_repo):
    repo = isolated_repo
    native = Libgit2Store(repo)
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    for dry_run in (False, True):
        assert (
            native.force_push(dry_run=dry_run).reason
            == cli.force_push(dry_run=dry_run).reason
            == "no_remote"
        )
    branch = _git(repo, "branch", "--show-current").strip()
    _git(repo, "update-ref", f"refs/remotes/origin/{branch}", "HEAD")
    assert native.force_push() == cli.force_push()
    actual, expected = native.force_push(dry_run=True), cli.force_push(dry_run=True)
    assert (actual.reason, actual.remote_sha_before, actual.remote_sha_after) == (
        expected.reason,
        expected.remote_sha_before,
        expected.remote_sha_after,
    )
    (repo / "note.md").write_text("ahead\n")
    _git(repo, "add", "note.md")
    _git(repo, "commit", "-qm", "ahead", "--", "note.md")
    assert native.force_push().reason == "push_failed"


@pytest.mark.parametrize(
    "expression",
    [
        "2020-01-02",
        "2020-07-02",
        "2020-01-02T12:34:56+02:00",
        "2020-01-02T12:34:56Z",
        "today",
        "midnight",
        "noon",
        "yesterday",
        "last monday",
        "2 weeks 3 days ago",
        "1.week.ago",
        "1 second ago",
        "1 minute ago",
        "1 hour ago",
        "1 day ago",
        "1 week ago",
        "1 month ago",
        "1 year ago",
    ],
)
def test_supported_dates_match_git(isolated_repo, expression):
    expected = int(
        _git(isolated_repo, "rev-parse", f"--since={expression}").strip().split("=")[1]
    )
    assert abs(_date(expression) - expected) <= 2


@pytest.mark.parametrize(
    "expression",
    [
        "tea",
        "fortnight ago",
        "this week",
        "noon yesterday",
        "99999999999999999999 years ago",
    ],
)
def test_unsupported_dates_are_actionable(expression):
    with pytest.raises(InvalidRequestError, match=r"ISO 8601.*GitWriteStrategy"):
        _date(expression)


def test_renamed_away_path_with_reused_name_matches_cli(isolated_repo):
    repo = isolated_repo
    original = repo / "original.md"
    original.write_text("one\ntwo\nthree\n")
    _git(repo, "add", "original.md")
    _git(repo, "commit", "-qm", "original")
    before = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "mv", "original.md", "a.md")
    _git(repo, "commit", "-qm", "first rename")
    at_a = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "mv", "a.md", "b.md")
    _git(repo, "commit", "-qm", "second rename")
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    native = Libgit2Store(repo)
    assert cli.get_file_diff(repo, repo / "a.md", before, False) == ""
    assert native.get_file_diff(repo, repo / "a.md", before, False) == ""
    assert native.get_file_diff(repo, repo / "a.md", at_a, False) == cli.get_file_diff(
        repo, repo / "a.md", at_a, False
    )


def test_aggregate_rename_mode_change_is_refused(isolated_repo):
    repo = isolated_repo
    old, new = repo / "old.md", repo / "new.md"
    old.write_text("one\ntwo\n")
    _git(repo, "add", "old.md")
    _git(repo, "commit", "-qm", "old")
    before = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "mv", "old.md", "new.md")
    new.chmod(0o755)
    _git(repo, "add", "new.md")
    _git(repo, "commit", "-qm", "new")
    with pytest.raises(InvalidRequestError, match="GitWriteStrategy"):
        Libgit2Store(repo).get_file_diff(repo, new, before, False)


def test_inexact_pairing_refused_across_native_queries(isolated_repo):
    """Never choose a similarity scorer when resolving a rewritten rename."""
    from markdown_vault_mcp.git.types import RevisionQuery

    repo = isolated_repo
    old, new = repo / "old.md", repo / "new.md"
    old.write_text("".join(f"line {i}\n" for i in range(20)))
    _git(repo, "add", "old.md")
    _git(repo, "commit", "-qm", "old")
    before = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "mv", "old.md", "new.md")
    new.write_text(new.read_text() + "rewritten\n")
    _git(repo, "add", "new.md")
    _git(repo, "commit", "-qm", "renamed and edited")
    native = Libgit2Store(repo)
    calls = [
        lambda: native.get_file_history(repo, new, None, 20),
        lambda: native.get_file_at_ref(
            RevisionQuery(repo_path=repo, path=new, ref=before, max_bytes=0)
        ),
        lambda: native.get_file_diff(repo, new, before, False),
        lambda: native.get_file_diff(repo, new, before, True),
    ]
    for call in calls:
        with pytest.raises(InvalidRequestError, match="GitWriteStrategy"):
            call()


def test_vault_overwrite_keeps_edited_rename_breadcrumb(isolated_repo):
    from markdown_vault_mcp.vault import Vault, VaultSettings

    repo = isolated_repo
    old, new = repo / "old.md", repo / "new.md"
    old.write_text("".join(f"line {i}\n" for i in range(20)))
    _git(repo, "add", "old.md")
    _git(repo, "commit", "-qm", "old")
    _git(repo, "mv", "old.md", "new.md")
    new.write_text(new.read_text() + "rewritten\n")
    _git(repo, "add", "new.md")
    _git(repo, "commit", "-qm", "rename and rewrite")
    store = Libgit2Store(repo)
    vault = Vault(
        source_dir=repo,
        settings=VaultSettings(read_only=False),
        git_strategy=store,
        on_write=store,
    )
    try:
        revision = _git(repo, "rev-parse", "HEAD").strip()
        assert store.committed_revision(repo, new) == revision
        result = vault.writer.write("new.md", "replacement\n")
        assert result.previous_revision == revision
        assert vault._write_callback.drain()
        assert _git(repo, "show", "HEAD:new.md") == "replacement\n"
    finally:
        vault.close()
        store.close()


def test_binary_summary_refusal_preserves_full_patch(isolated_repo):
    repo = isolated_repo
    note = repo / "data.bin"
    note.write_bytes(b"old\x00data\n")
    _git(repo, "add", "data.bin")
    _git(repo, "commit", "-qm", "old")
    before = _git(repo, "rev-parse", "HEAD").strip()
    note.write_bytes(b"new\x00data\n")
    _git(repo, "commit", "-qam", "new")
    native = Libgit2Store(repo)
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    assert native.get_file_diff(repo, note, before, False) == cli.get_file_diff(
        repo, note, before, False
    )
    with pytest.raises(
        InvalidRequestError, match=r"binary diff summaries.*GitWriteStrategy"
    ):
        native.get_file_diff(repo, note, before, False, summarize_binary=True)
    with pytest.raises(
        InvalidRequestError, match=r"binary diff summaries.*GitWriteStrategy"
    ):
        native.get_file_diff(repo, note, before, True, summarize_binary=True)


@pytest.mark.parametrize("kind", ["dissimilar", "whitespace", "duplicate"])
def test_uncertain_rename_pair_refused(isolated_repo, kind):
    repo = isolated_repo
    old, new = repo / "old.md", repo / "new.md"
    old.write_text("line with trailing space  \n" if kind == "whitespace" else "same\n")
    if kind == "duplicate":
        (repo / "other.md").write_text("same\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "old")
    before = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "rm", "-q", "old.md")
    if kind == "duplicate":
        _git(repo, "rm", "-q", "other.md")
    new.write_text(
        {
            "dissimilar": "different content\n",
            "whitespace": "line with trailing space\n",
            "duplicate": "same\n",
        }[kind]
    )
    _git(repo, "add", "new.md")
    _git(repo, "commit", "-qm", "replacement")
    with pytest.raises(InvalidRequestError, match="GitWriteStrategy"):
        Libgit2Store(repo).get_file_diff(repo, new, before, False)
