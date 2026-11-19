"""Build an attributed proposal from selected note diffs."""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

from markdown_vault_mcp._okf_convention import canonical_path
from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.okf import is_okf_reserved_file

if TYPE_CHECKING:
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.vault import Vault


class ProposalBackendError(Exception):
    """The model could not produce a usable proposal."""


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
        if any(unicodedata.category(c) in {"Cc", "Zl", "Zp"} for c in path):
            raise InvalidRequestError(
                "Pass paths without control or line separator characters to okf_propose_log_entry."
            )
        vault._doc_mgr._validate_path(path)
        target = canonical_path(path, vault.source_dir)
        if is_okf_reserved_file(path) or is_okf_reserved_file(target):
            raise InvalidRequestError(
                "Pass concept notes, not reserved files, in paths to okf_propose_log_entry."
            )
        if vault._doc_mgr._read_note(target, capped=False) is None:
            raise InvalidRequestError(
                f"Note {target!r} was not found; choose an existing path for okf_propose_log_entry."
            )
        if target not in selected:
            selected.append(target)
    return selected


def _selected_diffs(vault: Vault, selected: list[str], since: str | None) -> str:
    """Collect the selected diffs within the configured input budget."""
    diffs: list[str] = []
    for path in selected:
        start = since
        if start is None:
            history = vault.reader.get_history(path, limit=2)
            if len(history) < 2:
                raise InvalidRequestError(
                    f"Note {path!r} has fewer than two commits; pass since for okf_propose_log_entry."
                )
            start = history[1].sha
        if re.fullmatch(r"[0-9a-f]{4,64}", start):
            diff = vault.reader.get_diff(path, since_sha=start)
        else:
            diff = vault.reader.get_diff(path, since_timestamp=start)
        if isinstance(diff, str) and diff.strip():
            if re.search(r"\n\[diff truncated: \d+ bytes omitted\]$", diff):
                raise InvalidRequestError(
                    "A selected diff is too large; choose a later since for okf_propose_log_entry."
                )
            diffs.append(diff)
    if not diffs:
        raise InvalidRequestError(
            "No committed changes were found; choose a different since or paths for okf_propose_log_entry."
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
        since: Starting commit SHA or timestamp, or None for the latest changes.

    Returns:
        Suggested text, producer identifier and diff basis.
    """
    selected = _selected_paths(vault, paths)
    content = _selected_diffs(vault, selected, since)
    if vault._summarizer is None:
        raise RuntimeError("The summarization backend is unavailable.")
    try:
        text = vault._summarizer.summarize(
            "Write a concise factual explanation of the editorial change shown in these diffs. Output only one line, at most 500 characters.",
            content,
        ).strip()
    except RuntimeError as exc:
        raise ProposalBackendError from exc
    if (
        not text
        or len(text) > 500
        or any(unicodedata.category(c) in {"Cc", "Zl", "Zp"} for c in text)
    ):
        raise ProposalBackendError
    return {
        "text": text,
        "producer": f"{vault._summarizer.provider_name}/{config.summarize.openai_model}",
        "basis": "diff",
    }
