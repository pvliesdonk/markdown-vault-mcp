"""Offline discovery over the live native command tree.

Typer API assumptions and upgrade pins: docs/design/reference/typer-command-discovery.md.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

import typer

from markdown_vault_mcp._cli_output import terminal_text

_STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "in",
        "on",
        "at",
        "by",
        "with",
        "for",
        "all",
        "one",
        "want",
        "do",
        "how",
        "i",
        "my",
        "please",
    ]
)
_SYNONYMS = (
    "find search lookup",
    "list show ls",
    "note document",
    "directory folder",
    "frontmatter metadata",
    "backlink inbound",
    "outlink outbound outgoing",
    "outline toc",
)
# The headings task ranked list above toc without this observed vocabulary.
_KEYWORDS: dict[tuple[str, ...], str] = {("toc",): "headings"}
_SKIP = {
    "cli",
    "serve",
    "help",
    "completion",
    "_completion",
    "--install-completion",
    "--show-completion",
}


def _stem(word: str) -> str:
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 5 and word.endswith("ed"):
        return word[:-2]
    if len(word) > 4 and word.endswith(("ches", "shes", "xes", "sses")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us")):
        return word[:-1]
    return word


def _tokens(value: str) -> list[str]:
    return [
        _stem(word)
        for word in re.findall(r"[^\W_]+", value.lower())
        if word not in _STOP
    ]


@dataclass(frozen=True)
class _Entry:
    command: str
    summary: str
    usage: str
    fields: tuple[tuple[float, Counter[str]], ...]

    def result(self) -> dict[str, str]:
        """Return the public discovery record."""
        return {"command": self.command, "summary": self.summary, "usage": self.usage}


def _entry(command: Any, ctx: Any, path: tuple[str, ...]) -> _Entry:
    summary = " ".join(
        (command.short_help or command.help or "").split("\n\n", 1)[0].split()
    )
    flags = " ".join(
        " ".join(
            [
                *getattr(param, "opts", []),
                *getattr(param, "secondary_opts", []),
                getattr(param, "help", None) or "",
            ]
        )
        for param in command.params
        if not getattr(param, "hidden", False)
    )
    keywords = (
        " ".join(getattr(command, "keywords", ()) or ()) + " " + _KEYWORDS.get(path, "")
    )
    fields = (
        (4.0, Counter(_tokens(path[-1]))),
        (3.0, Counter(_tokens(" ".join(path[:-1])))),
        (3.0, Counter(_tokens(keywords))),
        (2.0, Counter(_tokens(summary))),
        (1.0, Counter(_tokens(flags))),
    )
    usage = " ".join([ctx.command_path, *command.collect_usage_pieces(ctx)])
    return _Entry(ctx.command_path, summary, usage, fields)


def _walk(ctx: Any, path: tuple[str, ...] = ()) -> list[_Entry]:
    entries: list[_Entry] = []
    for name, command in getattr(ctx.command, "commands", {}).items():
        child_path = (*path, name)
        if (
            name in _SKIP
            or child_path == ("okf", "verify")
            or command.hidden
            or command.deprecated
        ):
            continue
        child = command.context_class(command, info_name=name, parent=ctx)
        is_group = hasattr(command, "commands")
        runnable = not is_group or (
            command.invoke_without_command and not command.no_args_is_help
        )
        if command.callback is not None and runnable:
            entries.append(_entry(command, child, child_path))
        entries.extend(_walk(child, child_path))
    return entries


def _catalogue(root: Any) -> list[_Entry]:
    ctx = root.context_class(root, info_name=root.name)
    return _walk(ctx)


def _alternatives(term: str) -> set[str]:
    for group in _SYNONYMS:
        words = set(_tokens(group))
        if term in words:
            return words - {term}
    return set()


def _match(term: str, word: str, alternatives: set[str]) -> float:
    if term == word:
        return 1.0
    if word in alternatives or (len(term) >= 3 and word.startswith(term)):
        return 0.5
    return 0.0


def _score(entry: _Entry, term: str, idf: float) -> float:
    alternatives = _alternatives(term)
    score = 0.0
    for weight, counts in entry.fields:
        length = sum(counts.values())
        best = max(
            (
                _match(term, word, alternatives)
                * count
                * 2.2
                / (count + 1.2 * (0.75 + 0.25 * length / 8))
                for word, count in counts.items()
            ),
            default=0.0,
        )
        score += weight * best * idf
    return score


def _discover(root: Any, query: str, limit: int) -> list[dict[str, str]]:
    entries = _catalogue(root)
    terms = set(_tokens(query))
    frequencies = Counter(
        word
        for entry in entries
        for word in {word for _, counts in entry.fields for word in counts}
    )
    idfs = {
        term: math.log(
            1 + (len(entries) - frequencies[term] + 0.5) / (frequencies[term] + 0.5)
        )
        for term in terms
    }
    ranked = [
        (sum(_score(entry, term, idfs[term]) for term in sorted(terms)), entry)
        for entry in entries
    ]
    ranked.sort(key=lambda pair: (-pair[0], pair[1].command))
    return [entry.result() for score, entry in ranked if score > 0][:limit]


def register_search_commands(app: typer.Typer) -> None:
    """Register offline task discovery and its root-help pointer.

    Args:
        app: Application whose live compiled command tree is searched.
    """
    group = typer.Typer(
        help="Discover commands by describing a task, offline.", no_args_is_help=True
    )

    @group.command("search")
    def search(
        ctx: typer.Context,
        words: list[str] = typer.Argument(  # noqa: B008 -- Typer consumes parameter metadata
            ..., help="Describe the task in words."
        ),
        limit: int = typer.Option(5, min=1, max=20, help="Maximum suggested commands."),
        json_output: bool = typer.Option(
            False, "--json", help="Output query and results as JSON."
        ),
    ) -> None:
        """Find runnable commands by task without opening a vault or connection."""
        query = " ".join(words).strip()
        if not query:
            raise typer.BadParameter("Describe a nonblank task.", param_hint="words")
        results = _discover(ctx.find_root().command, query, limit)
        if json_output:
            typer.echo(
                json.dumps({"query": query, "results": results}, ensure_ascii=False)
            )
            return
        for row in results:
            typer.echo(terminal_text(f"{row['command']}  {row['summary']}"))
        first = results[0]["command"] if results else str(ctx.find_root().command.name)
        typer.echo(terminal_text(f"Run '{first} --help' for flags and examples."))

    app.add_typer(group, name="cli")
    pointer = 'Discover commands: markdown-vault-mcp cli search "count matching notes"'
    app.info.help = f"{pointer}\n\n{app.info.help or ''}"
