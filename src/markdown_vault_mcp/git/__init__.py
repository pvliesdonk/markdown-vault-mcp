"""Git integration package.

What the git command line actually does where this package relies on it
(refusal messages and their locale status, ``--porcelain``, askpass, URL
forms, pathspec magic, ``log -z`` framing, identity) is recorded with sources
and dates in ``docs/design/reference/git-cli.md``. Consult it before matching
a new stderr string or adding a git invocation; a behaviour the reference
does not settle is research first, not a guess.

``markdown_vault_mcp.git`` was historically a single 2721-LOC module. It is now a
package; this ``__init__`` preserves the public and test-relied-upon import
surface so existing ``from markdown_vault_mcp.git import X`` imports keep
resolving.

``import subprocess`` is intentional and load-bearing: tests patch
``markdown_vault_mcp.git.subprocess.run`` (and ``from markdown_vault_mcp.git import
subprocess``). Because ``markdown_vault_mcp.git.subprocess`` is the stdlib
``subprocess`` module object (a ``sys.modules`` singleton), patching ``.run`` on it
applies globally, so calls from any submodule that does ``import subprocess`` are
still intercepted. Keeping the attribute here preserves those patch targets.

Symbols that a submodule looks up via its own module globals (e.g.
``_stage_and_commit`` in ``strategy``; ``_push`` in ``push_scheduler`` since
#893; ``frontmatter`` in ``conflict``) are patched by tests at their real
home -- ``markdown_vault_mcp.git.<submodule>.<name>`` -- not via this package
namespace.  "Patch where the name is used."

This package deliberately imports nothing from ``fastmcp`` (#1160): request
identity is resolved at the MCP tool edge into a
:class:`~markdown_vault_mcp._identity.Principal` and passed in, so the git
layer stays usable from non-MCP drivers. A guard test asserts the absence.
"""

from __future__ import annotations

import subprocess  # noqa: F401 -- preserves the `markdown_vault_mcp.git.subprocess` patch target

from markdown_vault_mcp.git._run import (
    _find_git_root,  # noqa: F401 -- re-exported for the historic import surface
)
from markdown_vault_mcp.git.bootstrap import RepoBootstrap
from markdown_vault_mcp.git.health import SyncHealth, SyncHealthTracker
from markdown_vault_mcp.git.interfaces import (
    HistorySource,
    RevisionReader,
    Syncer,
    SyncHealthReporter,
    VersionedStore,
    Versioner,
)
from markdown_vault_mcp.git.push_scheduler import PushScheduler
from markdown_vault_mcp.git.strategy import (  # noqa: F401 -- re-exported for the historic import surface
    GitWriteStrategy,
    _stage_and_commit,
    git_write_strategy,
)
from markdown_vault_mcp.git.types import (
    PULL_REASON_CONFLICT_RESOLUTION_FAILED,
    PULL_REASON_CONFLICTS_RESOLVED_WITH_SIBLINGS,
    PULL_REASON_FETCH_FAILED,
    PULL_REASON_NO_REMOTE,
    PULL_REASON_NON_FAST_FORWARD_WITH_CONFLICTS,
    PULL_REASON_REBASED,
    PUSH_REASON_DRY_RUN_UNSUPPORTED,
    PUSH_REASON_NO_REMOTE,
    PUSH_REASON_NON_FAST_FORWARD,
    PUSH_REASON_PUSH_FAILED,
    REMOTE_STATE_UNSYNCED,
    PullResult,
    PushResult,
    RevisionQuery,
)

__all__ = [
    "PULL_REASON_CONFLICTS_RESOLVED_WITH_SIBLINGS",
    "PULL_REASON_CONFLICT_RESOLUTION_FAILED",
    "PULL_REASON_FETCH_FAILED",
    "PULL_REASON_NON_FAST_FORWARD_WITH_CONFLICTS",
    "PULL_REASON_NO_REMOTE",
    "PULL_REASON_REBASED",
    "PUSH_REASON_DRY_RUN_UNSUPPORTED",
    "PUSH_REASON_NON_FAST_FORWARD",
    "PUSH_REASON_NO_REMOTE",
    "PUSH_REASON_PUSH_FAILED",
    "REMOTE_STATE_UNSYNCED",
    "GitWriteStrategy",
    "HistorySource",
    "PullResult",
    "PushResult",
    "PushScheduler",
    "RepoBootstrap",
    "RevisionQuery",
    "RevisionReader",
    "SyncHealth",
    "SyncHealthReporter",
    "SyncHealthTracker",
    "Syncer",
    "VersionedStore",
    "Versioner",
    "git_write_strategy",
]
