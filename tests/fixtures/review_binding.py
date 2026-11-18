def review_args(path: str) -> dict[str, object]:
    """Build verify arguments for the current fixture vault's whole note."""
    import os
    from pathlib import Path

    from markdown_vault_mcp._okf_review import concept_snapshot

    text = (Path(os.environ["MARKDOWN_VAULT_MCP_SOURCE_DIR"]) / path).read_text(
        encoding="utf-8"
    )
    return {"path": path, "reviewed_snapshot": concept_snapshot(text)}
