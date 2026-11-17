"""Search ranking + snippet-truncation knobs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from markdown_vault_mcp.exceptions import ConfigurationError
from markdown_vault_mcp.types import DEFAULT_SEARCH_MODES
from markdown_vault_mcp.types import FTS_COLUMNS as _FTS_COLUMNS

# Type accepted for the weight-map fields: a mapping (e.g. from
# parse_weight_map) or an already-frozen tuple of (key, weight) pairs.
_WeightMap = Mapping[str, float] | Sequence[tuple[str, float]]

# Modes accepted for default_mode, shared with the SearchManager constructor
# so the two boundaries cannot drift (#1205).
_SEARCH_MODES = frozenset(DEFAULT_SEARCH_MODES)


@dataclass(frozen=True)
class SearchConfig:
    """Ranking/snippet tuning for keyword/semantic/hybrid search.

    Weight fields hold sorted pairs after construction.
    """

    chunks_per_file: int = 2
    snippet_words: int = 200
    length_downweight_alpha: float = 0.25
    max_chunk_words: int = 400
    max_chunk_chars_override: int | None = None
    chunk_overlap_words: int = 40
    folder_weights: _WeightMap | None = None
    fts_weights: _WeightMap | None = None
    default_mode: str = "auto"

    def _freeze_weight_map(
        self, name: str, normalise_key: bool = False
    ) -> dict[str, float] | None:
        """Normalise a weight-map field into a plain dict for validation.

        Stores the field back as a sorted ``tuple[tuple[str, float], ...]``
        (frozen-dataclass hygiene, #639) and returns the dict view for the
        caller's semantic checks. Keys are stripped; with ``normalise_key``
        a trailing ``/`` is also stripped (folder-prefix canonical form).

        Raises:
            ConfigurationError: If a key is empty after normalisation or collides
                with another key carrying a different weight.
        """
        value = getattr(self, name)
        if value is None:
            return None
        items = value.items() if isinstance(value, Mapping) else value
        weights: dict[str, float] = {}
        raw_keys: dict[str, str] = {}
        for raw_key, raw_weight in items:
            key = raw_key.strip()
            if normalise_key:
                key = key.rstrip("/")
            if not key:
                raise ConfigurationError(f"{name} keys must be non-empty")
            weight = float(raw_weight)
            if key in weights and weights[key] != weight:
                raise ConfigurationError(
                    f"{name}: keys {raw_keys[key]!r} and {raw_key!r} both normalise "
                    f"to {key!r} with different weights ({weights[key]} vs {weight})"
                )
            weights[key] = weight
            raw_keys[key] = raw_key
        object.__setattr__(self, name, tuple(sorted(weights.items())))
        return weights

    def __post_init__(self) -> None:
        """Validate ranges on every construction path (#638).

        Raises:
            ConfigurationError: If any field is out of range.
        """
        folder_weights = self._freeze_weight_map("folder_weights", normalise_key=True)
        if folder_weights is not None:
            for key, weight in folder_weights.items():
                if weight <= 0:
                    raise ConfigurationError(
                        f"folder_weights[{key!r}] must be > 0, got {weight}"
                    )
        fts_weights = self._freeze_weight_map("fts_weights")
        if fts_weights is not None:
            for key, weight in fts_weights.items():
                if key not in _FTS_COLUMNS:
                    raise ConfigurationError(
                        f"fts_weights key {key!r} is not an FTS column; "
                        f"expected one of {', '.join(_FTS_COLUMNS)}"
                    )
                if weight < 0:
                    raise ConfigurationError(
                        f"fts_weights[{key!r}] must be >= 0, got {weight}"
                    )
            if all(fts_weights.get(col, 1.0) == 0 for col in _FTS_COLUMNS):
                raise ConfigurationError("fts_weights: at least one column must be > 0")
        if self.chunks_per_file < 1:
            raise ConfigurationError(
                f"chunks_per_file must be >= 1, got {self.chunks_per_file}"
            )
        if self.snippet_words < 0:
            raise ConfigurationError(
                f"snippet_words must be >= 0, got {self.snippet_words}"
            )
        if self.length_downweight_alpha < 0:
            raise ConfigurationError(
                "length_downweight_alpha must be >= 0, got "
                f"{self.length_downweight_alpha}"
            )
        if self.max_chunk_words < 1:
            raise ConfigurationError(
                f"max_chunk_words must be >= 1, got {self.max_chunk_words}"
            )
        if (
            self.max_chunk_chars_override is not None
            and self.max_chunk_chars_override < 1
            and self.max_chunk_chars_override != -1
        ):
            raise ConfigurationError(
                "max_chunk_chars must be >= 1, or -1 for unbounded "
                f"context-scaling; got {self.max_chunk_chars_override}"
            )
        if self.chunk_overlap_words < 0:
            raise ConfigurationError(
                f"chunk_overlap_words must be >= 0, got {self.chunk_overlap_words}"
            )
        if self.default_mode not in _SEARCH_MODES:
            raise ConfigurationError(
                "default_mode must be one of "
                f"{', '.join(sorted(_SEARCH_MODES))}; got {self.default_mode!r}"
            )

    @property
    def folder_weight_map(self) -> dict[str, float] | None:
        """Return a detached folder-weight mapping, or None when unset."""
        return dict(self.folder_weights) if self.folder_weights is not None else None

    @property
    def fts_weight_map(self) -> dict[str, float] | None:
        """Return a detached FTS-weight mapping, or None when unset."""
        return dict(self.fts_weights) if self.fts_weights is not None else None
