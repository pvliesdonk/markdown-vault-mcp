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


def test_queued_writes_do_not_open_health(isolated_repo):
    repo = isolated_repo
    _git(repo, "reset", "--hard", "-q")
    store = Libgit2Store(repo)
    for name in ("a.md", "b.md"):
        (repo / name).write_text(name)
    store(repo / "a.md", "", "write")
    assert store.sync_health() is None
    store(repo / "b.md", "", "write")
    assert store.sync_health() is None


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
    for dry_run in (False, True):
        assert native.force_push(dry_run=dry_run).reason == "push_failed"


@pytest.mark.parametrize("kind", ["rename", "rename_edit"])
def test_aggregate_rename_diff_matches_cli(isolated_repo, kind):
    repo = isolated_repo
    old, new = repo / "old.md", repo / "new.md"
    old.write_text("".join(f"line {i}\n" for i in range(12)))
    _git(repo, "add", "old.md")
    _git(repo, "commit", "-qm", "old")
    before = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "mv", "old.md", "new.md")
    if kind == "rename_edit":
        new.write_text(new.read_text() + "changed\n")
    _git(repo, "add", "new.md")
    _git(repo, "commit", "-qm", "new")
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    native = Libgit2Store(repo)
    if kind == "rename_edit":
        with pytest.raises(InvalidRequestError, match="GitWriteStrategy"):
            native.get_file_diff(repo, new, before, False)
    else:
        assert native.get_file_diff(repo, new, before, False) == cli.get_file_diff(
            repo, new, before, False
        )


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
        "noon today",
        "yesterday",
        "noon yesterday",
        "last monday",
        "2 weeks 3 days ago",
        "1.week.ago",
    ],
)
def test_supported_dates_match_git(isolated_repo, expression):
    expected = int(
        _git(isolated_repo, "rev-parse", f"--since={expression}").strip().split("=")[1]
    )
    assert abs(_date(expression) - expected) <= 2


@pytest.mark.parametrize("expression", ["tea", "fortnight ago", "this week"])
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


def test_seed56_aggregate_rename_reuse(isolated_repo):
    """Pin the observed seed 56 history, including a reused old name."""
    repo = isolated_repo
    (repo / "sub").mkdir()
    old = (
        "".join(f"line {n}\n" for n in (20, 16, 9, 24, 17, 29, 13, 22, 7, 3, 11))
        + "e0\nnew0\nnew9\nnew8\n"
    )
    middle = (
        "".join(f"line {n}\n" for n in (9, 24, 17, 29, 13, 22, 7, 3, 11))
        + "e0\nnew0\nnew9\nnew8\nnew6\nnew2\ne7\n"
    )
    final = "line 11\ne0\nnew0\nnew9\nnew8\nnew6\nnew2\ne7\nnew7\nnew5\nnew0\nnew0\nnew9\nnew5\nnew0\nnew9\ne10\ne14\n"
    (repo / "sub/e.md").write_text(old)
    _git(repo, "add", "sub/e.md")
    _git(repo, "commit", "-qm", "s2")
    ref_e = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "mv", "sub/e.md", "a.md")
    _git(repo, "commit", "-qm", "s3")
    ref_a = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "mv", "a.md", "b.md")
    _git(repo, "commit", "-qm", "s4")
    _git(repo, "mv", "b.md", "sub/e.md")
    _git(repo, "commit", "-qm", "s5")
    _git(repo, "mv", "sub/e.md", "sub/d.md")
    (repo / "sub/d.md").write_text(middle)
    _git(repo, "add", "sub/d.md")
    _git(repo, "commit", "-qm", "s7")
    _git(repo, "mv", "sub/d.md", "b.md")
    (repo / "b.md").write_text(final)
    _git(repo, "add", "b.md")
    _git(repo, "commit", "-qm", "s14")
    (repo / "sub/d.md").write_text("different note\n")
    _git(repo, "add", "sub/d.md")
    _git(repo, "commit", "-qm", "reuse")
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    native = Libgit2Store(repo)
    for ref in (ref_e, ref_a):
        with pytest.raises(InvalidRequestError, match="GitWriteStrategy"):
            native.get_file_diff(repo, repo / "b.md", ref, False)
    for ref in (ref_e, ref_a):
        assert native.get_file_diff(
            repo, repo / "a.md", ref, False
        ) == cli.get_file_diff(repo, repo / "a.md", ref, False)


@pytest.mark.parametrize("kind", ["binary", "mode"])
def test_aggregate_rename_binary_and_mode_match_cli(isolated_repo, kind):
    repo = isolated_repo
    old, new = repo / "old.md", repo / "new.md"
    old.write_bytes(b"first\x00content\n" if kind == "binary" else b"one\ntwo\n")
    _git(repo, "add", "old.md")
    _git(repo, "commit", "-qm", "old")
    before = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "mv", "old.md", "new.md")
    if kind == "binary":
        new.write_bytes(b"first\x00content\nmore\n")
    else:
        new.chmod(0o755)
    _git(repo, "add", "new.md")
    _git(repo, "commit", "-qm", "new")
    native = Libgit2Store(repo)
    for summary in (False, True):
        with pytest.raises(InvalidRequestError, match="GitWriteStrategy"):
            native.get_file_diff(repo, new, before, False, summarize_binary=summary)


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
    from markdown_vault_mcp._tools._outcomes import outcome_error

    for call in calls:
        with pytest.raises(InvalidRequestError, match="GitWriteStrategy") as exc:
            call()
        mapped = outcome_error(exc.value)
        assert mapped is not None and str(mapped) == str(exc.value)


def test_low_similarity_possible_pair_refused(isolated_repo):
    repo = isolated_repo
    old, new = repo / "old.md", repo / "new.md"
    old.write_text("old entirely unrelated content\n")
    _git(repo, "add", "old.md")
    _git(repo, "commit", "-qm", "old")
    before = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "rm", "-q", "old.md")
    new.write_text("new entirely different content\n")
    _git(repo, "add", "new.md")
    _git(repo, "commit", "-qm", "replace")
    with pytest.raises(InvalidRequestError, match="GitWriteStrategy"):
        Libgit2Store(repo).get_file_diff(repo, new, before, False)


def test_vault_overwrite_omits_uncertain_breadcrumb(isolated_repo):
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
        assert store.committed_revision(repo, new) is None
        result = vault.writer.write("new.md", "replacement\n")
        assert result.previous_revision is None
        assert vault._write_callback.drain()
        assert _git(repo, "show", "HEAD:new.md") == "replacement\n"
    finally:
        vault.close()
        store.close()


def test_whitespace_only_rename_requires_exact_blob(isolated_repo):
    repo = isolated_repo
    old, new = repo / "old.md", repo / "new.md"
    old.write_text("line with trailing space  \n")
    _git(repo, "add", "old.md")
    _git(repo, "commit", "-qm", "old")
    before = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "mv", "old.md", "new.md")
    new.write_text("line with trailing space\n")
    _git(repo, "add", "new.md")
    _git(repo, "commit", "-qm", "rename")
    with pytest.raises(InvalidRequestError, match="GitWriteStrategy"):
        Libgit2Store(repo).get_file_diff(repo, new, before, False)


def test_duplicate_exact_rename_candidates_refuse(isolated_repo):
    repo = isolated_repo
    for name in ("one.md", "two.md"):
        (repo / name).write_text("same\n")
    _git(repo, "add", "one.md", "two.md")
    _git(repo, "commit", "-qm", "old")
    before = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "rm", "-q", "one.md", "two.md")
    (repo / "new.md").write_text("same\n")
    _git(repo, "add", "new.md")
    _git(repo, "commit", "-qm", "ambiguous")
    with pytest.raises(InvalidRequestError, match="GitWriteStrategy"):
        Libgit2Store(repo).get_file_diff(repo, repo / "new.md", before, False)


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
