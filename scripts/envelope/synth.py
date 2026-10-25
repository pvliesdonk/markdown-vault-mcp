"""A synthetic vault and a stand-in embedding provider for the harness (#1895).

Nothing here reads a private note or vector. The vault is generated from a
seed, so two runs with the same seed index the same bytes; the provider turns
each text into a fixed unit vector, so a run makes no network calls and its
figures do not depend on a model's speed.
"""

from __future__ import annotations

import hashlib
import itertools
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from markdown_vault_mcp.providers import EmbeddingProvider

if TYPE_CHECKING:
    from pathlib import Path

DEFAULT_DIMENSION = 1024
"""The deployed vectors' width: #1501 measured 1,024-d normalized FLOAT32."""

_SYLLABLES = ("ka", "lo", "mi", "ne", "ru", "sa", "to", "vi", "zo", "pe")
VOCABULARY: tuple[str, ...] = tuple(
    "".join(parts) for parts in itertools.product(_SYLLABLES, repeat=3)
)
"""The words notes are written in: 1,000 pronounceable non-words."""

QUERIES: tuple[str, ...] = tuple(
    " ".join(VOCABULARY[(i * 37 + k * 211) % len(VOCABULARY)] for k in range(3))
    for i in range(50)
)
"""The fixed search queries, each three vocabulary words."""

SECTIONS_PER_NOTE = 6
_LINES_PER_SECTION = 5
_WORDS_PER_LINE = 12
_FOLDERS = 20
_TAGS = tuple(f"tag-{i:02d}" for i in range(30))
_STATUSES = ("draft", "active", "done")

FILTER_FOLDER = "folder-00"
"""Every tenth note lives here; filtered search restricts to it."""

CHUNKS_PER_NOTE = SECTIONS_PER_NOTE + 1
"""Chunks the indexer makes of one generated note: the title's preamble and
one per section. A test indexes generated notes and checks it."""


class StandInProvider(EmbeddingProvider):
    """A deterministic embedding provider that counts what it embeds.

    Each text's vector is seeded from its SHA-256, drawn from a normal
    distribution and normalized, in float32 like the deployed vectors.

    Attributes:
        count: The texts embedded so far, documents and queries alike.
        calls: The embedding calls made so far.
        texts: The texts themselves, kept only when asked for, since holding
            them grows the measured process.
    """

    def __init__(
        self, dim: int = DEFAULT_DIMENSION, *, keep_texts: bool = False
    ) -> None:
        """Make vectors *dim* wide.

        Args:
            dim: The vector width.
            keep_texts: Whether to keep each text in :attr:`texts`.
        """
        self._dim = dim
        self._keep_texts = keep_texts
        self.count = 0
        self.calls = 0
        self.texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return each text's fixed unit vector, and count the texts.

        Args:
            texts: The texts to embed.

        Returns:
            One vector per text.
        """
        self.count += len(texts)
        self.calls += 1
        if self._keep_texts:
            self.texts.extend(texts)
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "little")
        vector = np.random.default_rng(seed).standard_normal(
            self._dim, dtype=np.float32
        )
        vector /= np.linalg.norm(vector)
        values: list[float] = vector.tolist()
        return values

    @property
    def dimension(self) -> int:
        """The vector width."""
        return self._dim

    @property
    def provider_name(self) -> str:
        """The provider's name in the store's identity."""
        return "envelope-stand-in"

    @property
    def model_name(self) -> str:
        """The model's name in the store's identity."""
        return f"sha256-normal-{self._dim}"

    @property
    def context_length(self) -> int | None:
        """No context limit: every text is embedded whole."""
        return None


@dataclass(frozen=True)
class VaultSpec:
    """What :func:`generate_vault` wrote.

    Attributes:
        paths: Each note's vault-relative path, in generation order.
        filter_folder: The folder filtered search restricts to.
        chunks_per_note: Chunks the indexer makes of each note.
    """

    paths: tuple[str, ...]
    filter_folder: str = FILTER_FOLDER
    chunks_per_note: int = CHUNKS_PER_NOTE


def notes_for_chunks(chunks: int) -> int:
    """Return the note count that indexes to about *chunks* chunks.

    Args:
        chunks: The chunk count to reach.

    Returns:
        The nearest whole number of notes, at least one.
    """
    return max(1, round(chunks / CHUNKS_PER_NOTE))


def _folder(index: int) -> str:
    if index % 10 == 0:
        return FILTER_FOLDER
    return f"folder-{1 + index % (_FOLDERS - 1):02d}"


def _line(rng: random.Random) -> str:
    return " ".join(rng.choice(VOCABULARY) for _ in range(_WORDS_PER_LINE))


def _section(rng: random.Random, number: int) -> str:
    lines = "\n".join(_line(rng) for _ in range(_LINES_PER_SECTION))
    return f"## Section {number}\n\n{lines}\n"


def _note(rng: random.Random, index: int) -> str:
    tags = ", ".join(sorted(rng.sample(_TAGS, 2)))
    sections = "\n".join(_section(rng, n) for n in range(1, SECTIONS_PER_NOTE + 1))
    return (
        f"---\ntags: [{tags}]\nstatus: {rng.choice(_STATUSES)}\n---\n"
        f"# Note {index}\n\n{_line(rng)}\n\n{sections}"
    )


def generate_vault(root: Path, *, notes: int, seed: int) -> VaultSpec:
    """Write *notes* synthetic notes under *root*.

    Args:
        root: The vault folder; created when missing.
        notes: How many notes to write.
        seed: Seeds the text, tags and statuses.

    Returns:
        The paths written and the facts the harness measures with.
    """
    rng = random.Random(seed)
    paths = []
    for index in range(notes):
        path = f"{_folder(index)}/note-{index:06d}.md"
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_note(rng, index), encoding="utf-8")
        paths.append(path)
    return VaultSpec(paths=tuple(paths))


def split_note(text: str) -> tuple[dict[str, object], str]:
    """Split a generated note into its frontmatter and body.

    Args:
        text: A note :func:`generate_vault` wrote.

    Returns:
        The frontmatter as a dict and the markdown body.
    """
    _, header, body = text.split("---\n", 2)
    fields = dict(line.split(": ", 1) for line in header.splitlines())
    frontmatter: dict[str, object] = {
        "tags": fields["tags"].strip("[]").split(", "),
        "status": fields["status"],
    }
    return frontmatter, body


def edited_note(text: str, *, edit: int) -> tuple[dict[str, object], str]:
    """Return a generated note with one section rewritten.

    The section and its new words follow from *edit*, so the same edit of the
    same note always produces the same text, and the chunk count is unchanged.

    Args:
        text: A note :func:`generate_vault` wrote.
        edit: The edit's number in the run.

    Returns:
        The note's frontmatter and its new body, ready for a write.
    """
    frontmatter, body = split_note(text)
    head, *sections = body.split("\n## ")
    target = edit % len(sections)
    rng = random.Random(f"edit-{edit}")
    sections[target] = _section(rng, target + 1).removeprefix("## ")
    return frontmatter, "\n## ".join([head, *sections])
