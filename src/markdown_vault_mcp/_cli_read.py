"""Stable command dispatch seam for local and remote read adapters."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp._cli_remote import RemoteReply, run_read

if TYPE_CHECKING:
    from markdown_vault_mcp.cli import _BuiltVault
    from markdown_vault_mcp.config import ProjectConfig
    from markdown_vault_mcp.vault import Vault

__all__ = ["RemoteReply", "run_read"]

_GRAPH_TOOLS = frozenset(
    {
        "get_backlinks",
        "get_outlinks",
        "get_broken_links",
        "get_orphan_notes",
        "get_most_linked",
        "get_connection_path",
    }
)


def local_read(tool: str, arguments: dict[str, Any], source_dir: str | None) -> Any:
    """Read local facets without rebuilding shared indexes or embedding notes.

    Args:
        tool: Native reader or graph operation selected by a command.
        arguments: Native operation arguments, without the remote drain flag.
        source_dir: Optional local source override.

    Returns:
        JSON-compatible native payload. Shared-index freshness remains unknown.
    """
    from pydantic import TypeAdapter
    from pydantic_core import PydanticSerializationError

    from markdown_vault_mcp._read_payloads import ReadPayloadError
    from markdown_vault_mcp.cli import _require_index_file
    from markdown_vault_mcp.config import ProjectConfig

    config = ProjectConfig.from_env()
    if tool in _GRAPH_TOOLS or tool == "search_paths":
        from dataclasses import replace

        # Graph/path queries need no vector identity or model, even with a sidecar.
        config = replace(config, embedding_provider=None, embeddings_path=None)
        if tool == "search_paths":
            config = replace(
                config,
                summarize_provider=None,
                summarize_openai_api_key=None,
                summarize_openai_base_url=None,
            )
    configured = config.indexing.index_path
    in_memory = configured is None or str(configured) == ":memory:"
    if configured is not None and not in_memory:
        _require_index_file(configured)
    built = _build_reader_vault(config, source_dir, in_memory)
    try:
        needs_index = tool != "read" or (
            arguments.get("section") is not None and arguments.get("revision") is None
        )
        if needs_index:
            if in_memory:
                built.vault.index.build_index()
            else:
                built.vault.index.use_existing_index()
        native = {
            key: value
            for key, value in arguments.items()
            if key != "wait_for_pending_writes"
        }
        data = _reader_payload(built.vault, tool, native)
        # Match MCP conversion without classifying native backend faults as input.
        try:
            return TypeAdapter(Any).dump_python(data, mode="json")
        except (UnicodeError, PydanticSerializationError):
            raise ReadPayloadError("Read payload is not JSON-compatible.", 1) from None
    finally:
        built.close()


def _build_reader_vault(
    config: ProjectConfig, source_dir: str | None, in_memory: bool
) -> _BuiltVault:
    from dataclasses import replace
    from pathlib import Path

    from markdown_vault_mcp.cli import _BuiltVault, _vectors_kept
    from markdown_vault_mcp.config_sections._assembly import (
        source_dir_problem,
        to_vault_instances,
        to_vault_settings,
    )
    from markdown_vault_mcp.exceptions import ConfigurationError
    from markdown_vault_mcp.vault import Vault

    # A read uses the existing checkout: never bootstrap or sync a managed clone.
    config = replace(
        config,
        source_dir=Path(source_dir) if source_dir is not None else config.source_dir,
        git_repo_url=None,
        git_token=None,
    )
    problem = source_dir_problem(config)
    if problem is not None:
        raise ConfigurationError(problem[1])
    # Native reads resolve note paths, so their root must use the same identity.
    config = replace(config, source_dir=config.source_dir.resolve())
    if not _vectors_kept(config, None):
        config = replace(config, embedding_provider=None)
    instances = to_vault_instances(config)
    try:
        settings = to_vault_settings(config, instances=instances)
        settings = replace(
            settings,
            owns_index_files=False,
            state_path=None if in_memory else settings.state_path,
        )
        return _BuiltVault(
            Vault(
                source_dir=config.source_dir,
                settings=settings,
                embedding_provider=instances.embedding_provider,
                summarizer=instances.summarizer,
                git_strategy=instances.git_strategy,
            ),
            instances,
        )
    except BaseException:
        instances.close()
        raise


def _reader_payload(vault: Vault, tool: str, arguments: dict[str, Any]) -> Any:
    from markdown_vault_mcp.utils.serialization import toc_payload

    if tool == "read":
        return read_payload(vault, **arguments)
    if tool == "search_paths":
        return _path_payload(vault, arguments)
    facet = vault.graph if tool in _GRAPH_TOOLS else vault.reader
    result = getattr(facet, tool)(**arguments)
    if tool == "get_connection_path":
        return {
            "found": result is not None,
            "path": result if result is not None else [],
            "hops": len(result) - 1 if result is not None else -1,
        }
    if tool == "get_toc":
        return toc_payload(result)
    if isinstance(result, list):
        return [
            asdict(item) if is_dataclass(item) and not isinstance(item, type) else item
            for item in result
        ]
    data = (
        asdict(result)
        if is_dataclass(result) and not isinstance(result, type)
        else result
    )
    if tool == "stats":
        okf = vault.reader.okf_stats()
        if okf is not None:
            data["okf"] = okf
    if tool == "get_context":
        _context_annotations(vault, data, arguments["path"])
    return data


def _path_payload(vault: Vault, arguments: dict[str, Any]) -> dict[str, Any]:
    from markdown_vault_mcp._read_payloads import ReadPayloadError, annotate_hits
    from markdown_vault_mcp.okf import derive_annotation
    from markdown_vault_mcp.query_types import PathQuery

    try:
        request = PathQuery(**arguments)
    except ValueError as exc:
        raise ReadPayloadError(str(exc)) from exc
    data = asdict(vault.reader.search_paths(request))
    annotate_hits(
        data["items"], derive_annotation if vault.okf.state().active else None
    )
    return data


def _context_annotations(vault: Vault, data: dict[str, Any], path: str) -> None:
    from markdown_vault_mcp.okf import derive_annotation

    try:
        conventions = [asdict(entry) for entry in vault.conventions.for_path(path)]
    except ValueError:
        # Native convention enrichment is optional; lookup refusals omit it.
        conventions = []
    if conventions:
        data["conventions"] = conventions
    if vault.okf.state().active:
        data["okf"] = derive_annotation(data["frontmatter"])


def read_payload(
    vault: Vault, path: str, *, section: str | None = None, revision: str | None = None
) -> dict[str, Any]:
    """Read through the native note and attachment response policies.

    Args:
        vault: Vault whose reader and configured limits apply.
        path: Vault-relative note or allowed attachment path.
        section: Optional native section heading.
        revision: Optional historical note revision.

    Returns:
        Native structured payload, with live whole-note OKF sources when active.

    Raises:
        InvalidRequestError: The native read or attachment policy refuses the request.
        DocumentNotFoundError: No live note exists at the requested path.
    """
    from dataclasses import asdict

    from markdown_vault_mcp.exceptions import DocumentNotFoundError
    from markdown_vault_mcp.okf import derive_annotation
    from markdown_vault_mcp.utils import is_note

    if revision is not None:
        return asdict(vault.reader.read_revision(path, revision, section=section))
    if not is_note(path):
        return _attachment_payload(vault, path)
    note = vault.reader.read(path, section=section)
    if note is None:
        raise DocumentNotFoundError.note(path)
    data = asdict(note)
    if section is None and vault.okf.state().active:
        data["okf"] = derive_annotation(note.frontmatter, include_sources=True)
    return data


def _attachment_payload(vault: Vault, path: str) -> dict[str, Any]:
    from dataclasses import asdict

    from markdown_vault_mcp.exceptions import InvalidRequestError

    cap_mb = vault.max_attachment_size_mb
    if cap_mb > 0:
        size = vault.reader.attachment_size(path)
        limit = int(cap_mb * 1024 * 1024)
        if size > limit:
            raise InvalidRequestError(
                f"Attachment {path!r} is {size:,} bytes, over the "
                f"{limit:,}-byte limit this server returns in a read. Fetch "
                "it with create_download_link if that tool is available."
            )
    return asdict(vault.reader.read_attachment(path))
