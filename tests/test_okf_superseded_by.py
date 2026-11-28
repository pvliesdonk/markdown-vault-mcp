"""A deprecated concept's annotation names its replacement (#2421).

OKF 0.3 §5.4: "Consumers that filter results to current knowledge SHOULD
follow it, and give the replacement concept." The consumer is the MCP
client; the server resolves the path field so the client can follow it
(account holder, 28 November 2026: "1 and not 2").
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp._cli_read import read_payload
from markdown_vault_mcp._read_payloads import annotate_hits
from markdown_vault_mcp.okf import derive_annotation, resolve_path_field
from markdown_vault_mcp.vault import Vault, VaultSettings

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("gross-margin.md", "metrics/gross-margin.md"),
        ("./gross-margin.md", "metrics/gross-margin.md"),
        ("../tables/sales.md", "tables/sales.md"),
        ("/tables/sales.md", "tables/sales.md"),
        ("my%20metric.md", "metrics/my metric.md"),
        ("gross-margin.md#definition", "metrics/gross-margin.md"),
        ("https://example.com/m.md", None),
        ("../../outside.md", None),
        ("", None),
        (None, None),
        (["a.md"], None),
    ],
)
def test_resolve_path_field(value: object, expected: str | None) -> None:
    # OKF 0.3 §6: a scheme is an absolute URL, `/` resolves from the bundle
    # root, anything else from the file's directory; outside the root is
    # external.
    assert resolve_path_field(value, "metrics/old.md") == expected


def test_only_a_deprecated_note_carries_superseded_by() -> None:
    meta = {"type": "Metric", "superseded_by": "new.md"}
    assert "superseded_by" not in derive_annotation(meta, path="m/old.md")
    deprecated = {**meta, "status": "deprecated"}
    assert derive_annotation(deprecated, path="m/old.md")["superseded_by"] == "m/new.md"
    assert derive_annotation({"status": "deprecated"}, path="m/old.md") == {
        **derive_annotation({"status": "deprecated"}),
        "superseded_by": None,
    }
    # Without a path there is nothing to resolve from.
    assert "superseded_by" not in derive_annotation(deprecated)


def test_hits_carry_the_resolved_replacement() -> None:
    hits = [
        {
            "path": "metrics/old.md",
            "frontmatter": {"status": "deprecated", "superseded_by": "new.md"},
        }
    ]
    annotate_hits(hits, derive_annotation)
    assert hits[0]["okf"]["superseded_by"] == "metrics/new.md"


@pytest.fixture
def bundle(tmp_path: Path) -> Iterator[Vault]:
    root = tmp_path / "vault"
    (root / "metrics").mkdir(parents=True)
    (root / "index.md").write_text('---\nokf_version: "0.3"\n---\n# B\n')
    (root / "metrics" / "old.md").write_text(
        "---\ntype: Metric\nstatus: deprecated\nsuperseded_by: gross-margin.md\n"
        "---\n# Old\n"
    )
    (root / "metrics" / "gross-margin.md").write_text("---\ntype: Metric\n---\n# G\n")
    col = Vault(source_dir=root, settings=VaultSettings())
    try:
        col.index.build_index()
        yield col
    finally:
        col.close()


def test_read_gives_the_replacement_path(bundle: Vault) -> None:
    okf = read_payload(bundle, "metrics/old.md")["okf"]
    assert okf["superseded_by"] == "metrics/gross-margin.md"
    assert read_payload(bundle, okf["superseded_by"])["path"] == (
        "metrics/gross-margin.md"
    )
