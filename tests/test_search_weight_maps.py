"""Search weight maps reject ambiguous and degenerate configurations."""

from pathlib import Path

import pytest

from markdown_vault_mcp.config import ProjectConfig
from markdown_vault_mcp.config_sections._helpers import parse_weight_map
from markdown_vault_mcp.config_sections.search import SearchConfig
from markdown_vault_mcp.exceptions import ConfigurationError


@pytest.mark.parametrize(
    "weights,accepted",
    [
        ({"Projects": 2.0, "Projects/": 3.0}, False),
        ({"Projects": 2.0, " Projects": 3.0}, False),
        ([("Projects", 2.0), ("Projects", 3.0)], False),
        ({"Projects": 2.0, "Projects/": 2.0}, True),
    ],
)
def test_normalised_folder_collisions(weights: object, accepted: bool) -> None:
    if accepted:
        assert SearchConfig(folder_weights=weights).folder_weights == (
            ("Projects", 2.0),
        )  # type: ignore[arg-type]
        return
    with pytest.raises(ConfigurationError, match=r"folder_weights.*Projects"):
        SearchConfig(folder_weights=weights)  # type: ignore[arg-type]


def test_literal_duplicate_env_entries() -> None:
    with pytest.raises(ConfigurationError, match=r"X.*'a'.*different weights"):
        parse_weight_map("a:1,a:2", "X")
    assert parse_weight_map("a:2,a:2", "X") == {"a": 2}
    assert parse_weight_map("a:1,b:2", "X") == {"a": 1, "b": 2}


@pytest.mark.parametrize(
    "name,value,message",
    [
        (
            "FOLDER_WEIGHTS",
            "Projects:2,Projects/:3",
            r"folder_weights.*'Projects'",
        ),
        (
            "FTS_WEIGHTS",
            "path:0,title:0,folder:0,heading:0,content:0,summary:0",
            r"fts_weights: at least one column must be > 0",
        ),
    ],
)
def test_environment_rejects_invalid_weights(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, value: str, message: str
) -> None:
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
    monkeypatch.setenv("MARKDOWN_VAULT_MCP_" + name, value)
    with pytest.raises(ConfigurationError, match=message):
        ProjectConfig.from_env()


def test_all_fts_columns_zero_is_rejected() -> None:
    columns = ("path", "title", "folder", "heading", "content", "summary")
    with pytest.raises(
        ConfigurationError, match="fts_weights: at least one column must be > 0"
    ):
        SearchConfig(fts_weights=dict.fromkeys(columns, 0.0))
    assert (
        SearchConfig(
            fts_weights={**dict.fromkeys(columns, 0.0), "title": 1}
        ).fts_weight_map
        is not None
    )
    assert SearchConfig(fts_weights={}).fts_weights == ()


@pytest.mark.parametrize("weights", [{"a": 2.0}, [("a", 2.0)]])
def test_accessors_return_dicts(weights: object) -> None:
    config = SearchConfig(folder_weights=weights, fts_weights=[("title", 3.0)])  # type: ignore[arg-type]
    folder = config.folder_weight_map
    fts = config.fts_weight_map
    assert folder == {"a": 2.0} and fts == {"title": 3.0}
    assert SearchConfig().folder_weight_map is None
    assert SearchConfig().fts_weight_map is None
