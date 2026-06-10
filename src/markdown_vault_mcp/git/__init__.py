"""Git integration package.

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

Symbols that strategy.py looks up via its own module globals (``_stage_and_commit``,
``_push``, ``_get_access_token``, ``frontmatter``) are patched by tests at their
real home -- ``markdown_vault_mcp.git.strategy.<name>`` -- not via this package
namespace. "Patch where the name is used."
"""

from __future__ import annotations

import subprocess  # noqa: F401 -- preserves the `markdown_vault_mcp.git.subprocess` patch target

from markdown_vault_mcp.git._run import (
    _find_git_root,  # noqa: F401 -- re-exported for the historic import surface
)
from markdown_vault_mcp.git.strategy import (  # noqa: F401 -- re-exported for the historic import surface
    GitWriteStrategy,
    _extract_claim,
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
    PullResult,
    PushResult,
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
    "GitWriteStrategy",
    "PullResult",
    "PushResult",
    "git_write_strategy",
]
