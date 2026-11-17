"""Round-three local Git parity regressions."""

import pytest

from markdown_vault_mcp.exceptions import ConfigurationError, InvalidRequestError
from markdown_vault_mcp.git.libgit2 import Libgit2Store
from markdown_vault_mcp.git.libgit2_history import _date
from markdown_vault_mcp.git.strategy import GitWriteStrategy
from markdown_vault_mcp.git.types import RevisionQuery
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


def test_noon_before_noon_matches_frozen_git(isolated_repo, monkeypatch):
    import datetime as dt
    import time

    import markdown_vault_mcp.git.libgit2_history as history

    class FrozenDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2024, 1, 2, 9, 30, tzinfo=tz)

    try:
        with monkeypatch.context() as frozen:
            frozen.setenv("TZ", "UTC")
            time.tzset()
            frozen.setenv("GIT_TEST_DATE_NOW", "1704187800")
            expected = int(
                _git(isolated_repo, "rev-parse", "--since=noon").split("=")[1]
            )
            assert expected == 1704110400  # 2024-01-01 12:00 UTC
            frozen.setattr(history.dt, "datetime", FrozenDateTime)
            assert _date("noon") == expected
    finally:
        time.tzset()


def test_utc_author_timestamp_matches_cli(isolated_repo, monkeypatch):
    repo = isolated_repo
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2024-01-02T03:04:05+00:00")
    note = repo / "utc.md"
    note.write_text("utc\n")
    _git(repo, "add", "utc.md")
    _git(repo, "commit", "-qm", "UTC author")
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    expected = cli.get_file_history(repo, note, None, 1)
    assert _git(repo, "log", "-1", "--format=%aI").strip() == "2024-01-02T03:04:05Z"
    assert Libgit2Store(repo).get_file_history(repo, note, None, 1) == expected


@pytest.mark.parametrize("name", ["café.md", "tab\tname.md", "line\nname.md"])
@pytest.mark.parametrize("per_commit", [False, True])
def test_literal_diff_paths_match_cli(isolated_repo, name, per_commit):
    repo = isolated_repo
    note = repo / name
    note.write_text("first\n")
    _git(repo, "add", "--", name)
    _git(repo, "commit", "-qm", "first")
    before = _git(repo, "rev-parse", "HEAD").strip()
    note.write_text("second\n")
    _git(repo, "add", "--", name)
    _git(repo, "commit", "-qm", "second")
    native = Libgit2Store(repo)
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    if not name.isascii():
        with pytest.raises(
            InvalidRequestError, match=r"(?i)non.ascii.*GitWriteStrategy"
        ):
            native.get_file_diff(repo, note, before, per_commit)
        return
    assert native.get_file_diff(repo, note, before, per_commit) == cli.get_file_diff(
        repo, note, before, per_commit
    )


@pytest.mark.parametrize("phase", ["open", "write", "revision"])
def test_split_index_refusal_preserves_state(isolated_repo, phase):
    repo = isolated_repo
    store = Libgit2Store(repo)
    _git(repo, "update-index", "--split-index")
    before_head = _git(repo, "rev-parse", "HEAD")
    index = repo / ".git/index"
    before_index = index.read_bytes()
    note = repo / "new.md"
    note.write_text("new\n")
    try:
        with pytest.raises(
            ConfigurationError, match=r"(?i)split.index.*GitWriteStrategy"
        ):
            if phase == "open":
                Libgit2Store(repo)
            elif phase == "write":
                store(note, "", "write")
            else:
                store.get_file_at_ref(
                    RevisionQuery(repo, repo / "unrelated.md", before_head.strip(), 0)
                )
    finally:
        assert _git(repo, "rev-parse", "HEAD") == before_head
        assert index.read_bytes() == before_index
        assert note.read_text() == "new\n"


def test_split_index_breadcrumb_remains_best_effort(isolated_repo):
    store = Libgit2Store(isolated_repo)
    _git(isolated_repo, "update-index", "--split-index")
    assert (
        store.committed_revision(isolated_repo, isolated_repo / "unrelated.md") is None
    )


@pytest.mark.parametrize("operation", ["write", "rename"])
@pytest.mark.parametrize("backend", ["cli", "native"])
def test_case_variant_write_has_no_duplicate_tree_entries(
    isolated_repo, operation, backend
):
    repo = isolated_repo
    original, variant = repo / "Note.md", repo / "note.md"
    original.write_text("old\n")
    _git(repo, "add", "Note.md")
    _git(repo, "commit", "-qm", "case seed")
    if not variant.exists():
        pytest.skip("requires a case-insensitive filesystem")
    store = (
        GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
        if backend == "cli"
        else Libgit2Store(repo)
    )
    if operation == "rename":
        original.rename(variant)
    else:
        variant.write_text("new\n")
    before_head = _git(repo, "rev-parse", "HEAD")
    before_index = (repo / ".git/index").read_bytes()
    before_content = variant.read_bytes()
    if backend == "native":
        with pytest.raises(ConfigurationError, match=r"case-variant.*GitWriteStrategy"):
            store(
                variant,
                "",
                operation,
                old_path=original if operation == "rename" else None,
            )
        assert _git(repo, "rev-parse", "HEAD") == before_head
        assert (repo / ".git/index").read_bytes() == before_index
        assert variant.read_bytes() == before_content
    else:
        store(
            variant, "", operation, old_path=original if operation == "rename" else None
        )
    names = _git(repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    assert len({name.casefold() for name in names}) == len(names), names


def test_autocrlf_changed_after_open_matches_cli_blob(isolated_repo):
    repo = isolated_repo
    store = Libgit2Store(repo)
    warmup = repo / "warmup.md"
    warmup.write_text("warmup\n")
    store(warmup, "", "write")
    _git(repo, "config", "core.autocrlf", "true")
    note = repo / "crlf.md"
    note.write_bytes(b"one\r\ntwo\r\n")
    expected = _git(repo, "hash-object", "--path=crlf.md", str(note)).strip()
    store(note, "", "write")
    assert _git(repo, "rev-parse", "HEAD:crlf.md").strip() == expected


@pytest.mark.parametrize("operation", ["delete", "rename"])
def test_staged_new_path_is_removed_from_index(isolated_repo, operation):
    repo = isolated_repo
    old, new = repo / "staged-new.md", repo / "renamed-new.md"
    old.write_text("staged new\n")
    _git(repo, "add", "--", old.name)
    store = Libgit2Store(repo)
    if operation == "delete":
        old.unlink()
        store(old, "", "delete")
    else:
        old.rename(new)
        store(new, "", "rename", old_path=old)
    paths = _git(repo, "ls-files", "-z").split("\0")
    assert old.name not in paths
    assert _git(repo, "show", ":unrelated.md") == "staged\n"
    if operation == "rename":
        assert _git(repo, "show", "HEAD:renamed-new.md") == "staged new\n"


@pytest.mark.parametrize("scope", ["vault", "directory"])
def test_scoped_exact_rename_paths_match_cli(isolated_repo, scope):
    repo = isolated_repo
    directory = repo / "folder"
    directory.mkdir()
    (directory / "old.md").write_text("unchanged contents\n")
    _git(repo, "add", "--", "folder/old.md")
    _git(repo, "commit", "-qm", "before rename")
    _git(repo, "mv", "folder/old.md", "folder/new.md")
    _git(repo, "commit", "-qm", "rename")
    native = Libgit2Store(repo)
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    path = directory if scope == "directory" else None
    actual = native.get_file_history(repo, path, None, 1, is_dir=path is not None)
    expected = cli.get_file_history(repo, path, None, 1, is_dir=path is not None)
    assert expected[0].paths_changed == ["folder/new.md"]
    assert actual == expected


def test_precompose_unicode_write_does_not_duplicate_index(isolated_repo):
    import unicodedata

    repo = isolated_repo
    _git(repo, "config", "core.precomposeunicode", "true")
    nfc = "café.md"
    nfd = unicodedata.normalize("NFD", nfc)
    decomposed, composed = repo / nfd, repo / nfc
    store = Libgit2Store(repo)
    decomposed.write_text("first\n")
    if not composed.exists():
        pytest.skip("requires a filesystem that aliases Unicode compositions")
    store(decomposed, "", "write")
    composed.write_text("second\n")
    store(composed, "", "edit")
    paths = _git(repo, "-c", "core.quotepath=false", "ls-files", "-z").split("\0")
    matches = [name for name in paths if unicodedata.normalize("NFC", name) == nfc]
    assert matches == [nfc]
    assert _git(repo, "show", f"HEAD:{nfc}") == "second\n"


def test_lock_contention_direct_native_and_cli(isolated_repo):
    repo = isolated_repo
    native = Libgit2Store(repo)
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    note = repo / "new.md"
    note.write_text("new\n")
    before_head = _git(repo, "rev-parse", "HEAD")
    before_index = (repo / ".git/index").read_bytes()
    (repo / ".git/index.lock").write_text("held")
    with pytest.raises(FileExistsError):
        native(note, "", "write")
    assert cli(note, "", "write") is None
    assert _git(repo, "rev-parse", "HEAD") == before_head
    assert (repo / ".git/index").read_bytes() == before_index
    assert (repo / ".git/index.lock").read_text() == "held"


def test_raw_commit_message_matches_cli_newline(isolated_repo):
    repo = isolated_repo
    note = repo / "native.md"
    note.write_text("note\n")
    Libgit2Store(repo)(note, "", "write")
    native = _git(repo, "cat-file", "commit", "HEAD").split("\n\n", 1)[1]
    _git(repo, "commit", "--allow-empty", "-qm", "write: native.md")
    expected = _git(repo, "cat-file", "commit", "HEAD").split("\n\n", 1)[1]
    assert expected == "write: native.md\n"
    assert native == expected


@pytest.mark.parametrize("phase", ["open", "write"])
def test_required_missing_filter_refused(isolated_repo, phase):
    """A required missing driver must not silently publish unfiltered bytes."""
    import subprocess

    repo = isolated_repo
    (repo / ".gitattributes").write_text("*.md filter=missing\n")
    store = Libgit2Store(repo) if phase == "write" else None
    _git(repo, "config", "filter.missing.required", "true")
    note = repo / "secret.md"
    note.write_text("synthetic secret\n")
    before = _git(repo, "rev-parse", "HEAD")
    index = _git(repo, "ls-files", "--stage")
    result = subprocess.run(
        ["git", "-C", str(repo), "add", "--", "secret.md"],
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    with pytest.raises(ConfigurationError, match=r"filter.*GitWriteStrategy"):
        if store is None:
            Libgit2Store(repo)
        else:
            store(note, "", "write")
    assert _git(repo, "rev-parse", "HEAD") == before
    assert _git(repo, "ls-files", "--stage") == index


def test_required_filter_effective_false_override(isolated_repo):
    """Only the effective required setting controls a missing driver."""
    repo = isolated_repo
    (repo / ".gitattributes").write_text("*.md filter=missing\n")
    config = repo / ".git/config"
    config.write_text(
        config.read_text() + '[filter "missing"]\n required = true\n required = false\n'
    )
    note = repo / "note.md"
    note.write_text("allowed\n")
    Libgit2Store(repo)(note, "", "write")
    assert _git(repo, "show", "HEAD:note.md") == "allowed\n"


@pytest.mark.parametrize("scope", ["file", "directory", "vault"])
def test_since_traversal_with_clock_skew_matches_cli(isolated_repo, monkeypatch, scope):
    """A child older than the cutoff stops Git's normal history walk."""
    repo = isolated_repo
    directory = repo / "folder"
    directory.mkdir()
    note = directory / "note.md"
    for date, content in [
        ("2025-01-01T00:00:00+0000", "parent\n"),
        ("2020-01-01T00:00:00+0000", "child\n"),
    ]:
        monkeypatch.setenv("GIT_AUTHOR_DATE", date)
        monkeypatch.setenv("GIT_COMMITTER_DATE", date)
        note.write_text(content)
        _git(repo, "add", "folder/note.md")
        _git(repo, "commit", "-qm", content.strip(), "--", "folder/note.md")
    native = Libgit2Store(repo)
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    path = {"file": note, "directory": directory, "vault": None}[scope]
    args = (repo, path, "2023-01-01T00:00:00+00:00", 10)
    expected = cli.get_file_history(*args, is_dir=scope == "directory")
    assert expected == []
    assert native.get_file_history(*args, is_dir=scope == "directory") == expected


def test_noop_untracked_deletion_clears_health(isolated_repo):
    """Deleting the last untracked note clears startup's stale warning."""
    repo = isolated_repo
    _git(repo, "reset", "--hard", "-q", "HEAD")
    note = repo / "untracked.md"
    note.write_text("new\n")
    store = Libgit2Store(repo)
    assert store.sync_health().reason == "uncommitted_changes"
    before = _git(repo, "rev-parse", "HEAD")
    note.unlink()
    store(note, "", "delete")
    assert store.sync_health() is None
    assert _git(repo, "rev-parse", "HEAD") == before


def test_required_filter_effective_true_override(isolated_repo):
    """The last required value wins when an earlier value is false."""
    repo = isolated_repo
    (repo / ".gitattributes").write_text("*.md filter=missing\n")
    config = repo / ".git/config"
    config.write_text(
        config.read_text() + '[filter "missing"]\n required = false\n required = true\n'
    )
    with pytest.raises(ConfigurationError, match=r"filter.*GitWriteStrategy"):
        Libgit2Store(repo)


def test_vault_noop_deletion_recovers_health(isolated_repo):
    """Vault deletion clears a stale warning and a later write still commits."""
    from markdown_vault_mcp.vault import Vault, VaultSettings

    repo = isolated_repo
    _git(repo, "reset", "--hard", "-q", "HEAD")
    note = repo / "untracked.md"
    note.write_text("new\n")
    store = Libgit2Store(repo)
    vault = Vault(
        source_dir=repo,
        settings=VaultSettings(read_only=False),
        git_strategy=store,
        on_write=store,
    )
    try:
        assert store.sync_health().reason == "uncommitted_changes"
        vault.writer.delete("untracked.md")
        assert vault._write_callback.drain()
        assert store.sync_health() is None
        vault.writer.write("recovered.md", "recovered\n")
        assert vault._write_callback.drain()
        assert _git(repo, "show", "HEAD:recovered.md") == "recovered\n"
        assert store.sync_health() is None
    finally:
        vault.close()
        store.close()


def test_valueless_required_filter_refused(isolated_repo):
    """Git treats a valueless required setting as true."""
    import subprocess

    repo = isolated_repo
    (repo / ".gitattributes").write_text("*.md filter=missing\n")
    config = repo / ".git/config"
    config.write_text(config.read_text() + '[filter "missing"]\n required\n')
    note = repo / "secret.md"
    note.write_text("synthetic secret\n")
    result = subprocess.run(
        ["git", "-C", str(repo), "add", "--", "secret.md"],
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    with pytest.raises(ConfigurationError, match=r"filter.*GitWriteStrategy"):
        Libgit2Store(repo)


@pytest.mark.parametrize("kind", ["clean", "process"])
@pytest.mark.parametrize("command", ["false", "0", "no", "off", "umask"])
def test_nonempty_filter_command_refused(isolated_repo, kind, command):
    """Boolean-looking strings remain external filter commands."""
    repo = isolated_repo
    (repo / ".gitattributes").write_text("*.md filter=external\n")
    _git(repo, "config", f"filter.external.{kind}", command)
    with pytest.raises(ConfigurationError, match=r"filter.*GitWriteStrategy"):
        Libgit2Store(repo)


def test_ambiguous_scoped_exact_renames_match_cli(isolated_repo):
    """Two identical deletions and one addition must not lose a path."""
    repo = isolated_repo
    folder = repo / "folder"
    folder.mkdir()
    for name in ("a.md", "b.md"):
        (folder / name).write_text("identical\n")
    _git(repo, "add", "folder")
    _git(repo, "commit", "-qm", "old", "--", "folder")
    (folder / "a.md").unlink()
    (folder / "b.md").unlink()
    (folder / "c.md").write_text("identical\n")
    _git(repo, "add", "-A", "folder")
    _git(repo, "commit", "-qm", "move", "--", "folder")
    native = Libgit2Store(repo)
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    expected = cli.get_file_history(repo, folder, None, 1, is_dir=True)
    assert len(expected[0].paths_changed) == 2
    try:
        actual = native.get_file_history(repo, folder, None, 1, is_dir=True)
    except InvalidRequestError as exc:
        assert "GitWriteStrategy" in str(exc)
    else:
        assert actual == expected


@pytest.mark.parametrize("direction", ["in", "out"])
@pytest.mark.parametrize("scope", ["directory", "vault"])
def test_scoped_exact_rename_across_boundary_matches_cli(
    isolated_repo, direction, scope
):
    """A scope boundary retains whichever rename endpoint it contains."""
    repo = isolated_repo
    folder = repo / "folder"
    folder.mkdir()
    inside, outside = folder / "inside.md", repo / "outside.md"
    old, new = (outside, inside) if direction == "in" else (inside, outside)
    old.write_text("unchanged\n")
    _git(repo, "add", str(old.relative_to(repo)))
    _git(repo, "commit", "-qm", "before")
    _git(repo, "mv", str(old.relative_to(repo)), str(new.relative_to(repo)))
    _git(repo, "commit", "-qm", "move")
    native = Libgit2Store(repo)
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    path = folder if scope == "directory" else None
    assert native.get_file_history(repo, path, None, 1, is_dir=path is not None) == (
        cli.get_file_history(repo, path, None, 1, is_dir=path is not None)
    )


@pytest.mark.parametrize("scope", ["vault", "directory"])
def test_scoped_edited_rename_matches_cli_or_refuses(isolated_repo, scope):
    """Edited renames cannot silently list both endpoints in scoped history."""
    repo = isolated_repo
    folder = repo / "folder"
    folder.mkdir()
    old, new = folder / "old.md", folder / "new.md"
    content = "".join(f"line {number}\n" for number in range(50))
    old.write_text(content)
    _git(repo, "add", "folder/old.md")
    _git(repo, "commit", "-qm", "before")
    _git(repo, "mv", "folder/old.md", "folder/new.md")
    new.write_text(content + "edited\n")
    _git(repo, "add", "folder/new.md")
    _git(repo, "commit", "-qm", "rename and edit")
    native = Libgit2Store(repo)
    cli = GitWriteStrategy(repo_path=repo, enable_push=False, git_lfs=False)
    path = folder if scope == "directory" else None
    expected = cli.get_file_history(repo, path, None, 1, is_dir=path is not None)
    assert expected[0].paths_changed == ["folder/new.md"]
    try:
        actual = native.get_file_history(repo, path, None, 1, is_dir=path is not None)
    except InvalidRequestError as exc:
        assert "GitWriteStrategy" in str(exc)
    else:
        assert actual == expected
