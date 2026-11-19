"""Build an attributed proposal from selected note diffs."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from difflib import unified_diff
from typing import TYPE_CHECKING

from markdown_vault_mcp._okf_convention import canonical_path
from markdown_vault_mcp.exceptions import InvalidRequestError, SummarizeTimeoutError
from markdown_vault_mcp.git.query import _DIFF_MAX_BYTES
from markdown_vault_mcp.managers.git_query import _SHA_RE
from markdown_vault_mcp.okf import is_okf_reserved_file
from markdown_vault_mcp.utils.fs import is_regular_file

if TYPE_CHECKING:
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.types import CommitDiff, HistoryEntry
    from markdown_vault_mcp.vault import Vault


class ProposalBackendError(Exception):
    """The model could not produce a usable proposal."""


def _has_control(text: str) -> bool:
    return any(unicodedata.category(c) in {"Cc", "Zl", "Zp"} for c in text)


def _selected_paths(vault: Vault, paths: list[str]) -> list[str]:
    if not paths:
        raise InvalidRequestError(
            "Pass at least one note in paths to okf_propose_log_entry."
        )
    if len(paths) > vault._summarize_max_notes:
        raise InvalidRequestError(
            "Too many paths; call okf_propose_log_entry with fewer notes."
        )
    selected: list[str] = []
    for path in paths:
        if _has_control(path):
            raise InvalidRequestError(
                "Pass paths without control or line separator characters to okf_propose_log_entry."
            )
        resolved = vault._doc_mgr._validate_path(path)
        target = canonical_path(path, vault.source_dir)
        if is_okf_reserved_file(path) or is_okf_reserved_file(target):
            raise InvalidRequestError(
                "Pass concept notes, not reserved files, in paths to okf_propose_log_entry."
            )
        if not is_regular_file(resolved):
            raise InvalidRequestError(
                f"Note {target!r} was not found; choose an existing path for okf_propose_log_entry."
            )
        if target not in selected:
            selected.append(target)
    return selected


def _validate_since(since: str | None) -> None:
    """Reject dates and revisions that the proposal contract does not support."""
    if since is None or re.fullmatch(_SHA_RE, since):
        return
    try:
        if not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}(?:[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[Zz]|[+-]\d{2}:\d{2})?)?",
            since,
        ):
            raise ValueError
        datetime.fromisoformat(since.replace("z", "Z"))
    except ValueError:
        raise InvalidRequestError(
            "Invalid since; pass a commit SHA or an ISO 8601 date or timestamp to okf_propose_log_entry."
        ) from None


def _committed_selection(
    vault: Vault, selected: list[str], since: str | None
) -> dict[str, list[HistoryEntry]]:
    """Check that every selected working note matches its latest committed version."""
    histories: dict[str, list[HistoryEntry]] = {}
    for path in selected:
        history = vault.reader.get_history(path, limit=2)
        if not history:
            raise InvalidRequestError(
                f"Note {path!r} has no committed history; commit it in a git-backed vault before calling okf_propose_log_entry."
            )
        if vault._git_query_mgr.committed_revision(path) != history[0].sha:
            raise InvalidRequestError(
                f"Cannot confirm {path!r} matches its latest commit; commit any uncommitted edits and retry okf_propose_log_entry."
            )
        histories[path] = history
    if since is None and len({history[0].sha for history in histories.values()}) > 1:
        raise InvalidRequestError(
            f"Notes {selected!r} changed in a different commit; select notes changed together, or pass since to okf_propose_log_entry."
        )
    return histories


def _creation_diff(vault: Vault, path: str, sha: str) -> str:
    snapshot = vault.reader.read_revision(path, sha).content
    return "".join(
        unified_diff(
            [],
            snapshot.splitlines(keepends=True),
            fromfile="/dev/null",
            tofile=f"b/{path}",
        )
    )


def _selected_diffs(vault: Vault, selected: list[str], since: str | None) -> str:
    """Collect the selected diffs within the configured input budget."""
    histories = _committed_selection(vault, selected, since)
    before_history = (
        since is not None
        and not re.fullmatch(_SHA_RE, since)
        and not vault.reader.get_history(until=since, limit=1)
    )
    diffs: list[str] = []
    for path in selected:
        diff: str | list[CommitDiff]
        start = since
        history = histories[path]
        if before_history or (start is None and len(history) == 1):
            diff = _creation_diff(vault, path, history[0].sha)
        else:
            if start is None:
                start = history[1].sha
            if re.fullmatch(_SHA_RE, start):
                diff = vault.reader.get_diff(path, since_sha=start)
            else:
                diff = vault.reader.get_diff(path, since_timestamp=start)
        if isinstance(diff, str) and diff.strip():
            if len(diff.encode()) > _DIFF_MAX_BYTES:
                raise InvalidRequestError(
                    "A selected diff is too large; choose a later since for okf_propose_log_entry."
                )
            diffs.append(diff)
    if not diffs:
        raise InvalidRequestError(
            "No committed content changes were found; choose a different since or paths for okf_propose_log_entry."
        )
    content = "\n\n".join(diffs)
    if len(content) > vault._summarize_max_input_chars:
        raise InvalidRequestError(
            "The selected diffs exceed the input limit; use fewer paths or a later since in okf_propose_log_entry."
        )
    return content


def propose_log_entry(
    vault: Vault, config: ProjectConfig, paths: list[str], since: str | None
) -> dict[str, str]:
    """Propose text from selected committed note diffs without writing to the vault.

    Args:
        vault: Vault providing validated note and Git access.
        config: Summarizer model configuration.
        paths: Selected note paths.
        since: Starting commit SHA or ISO 8601 date or timestamp, or None for
            the latest commit shared by the selected notes.

    Returns:
        Suggested text, producer identifier and diff basis.
    """
    _validate_since(since)
    selected = _selected_paths(vault, paths)
    content = _selected_diffs(vault, selected, since)
    if vault._summarizer is None:
        raise ProposalBackendError(
            "The configured summarization backend is unavailable. Ask the server operator to restore it, then retry or write the explanation yourself."
        )
    try:
        text = vault._summarizer.summarize(
            "Write a concise factual explanation of the editorial change shown in these diffs. Output only one line, at most 500 characters.",
            content,
        ).strip()
    except SummarizeTimeoutError:
        raise
    except RuntimeError as exc:
        raise ProposalBackendError(
            "The model could not produce a proposal. Retry later or write the explanation yourself."
        ) from exc
    if not text or len(text) > 500 or _has_control(text):
        raise ProposalBackendError(
            "The model could not produce a usable single-line proposal of at most 500 characters. Write the explanation yourself or retry with a narrower selection."
        )
    return {
        "text": text,
        "producer": f"{vault._summarizer.provider_name}/{config.summarize.openai_model}",
        "basis": "diff",
    }
