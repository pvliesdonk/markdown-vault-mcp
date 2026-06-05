"""Cohesive facets over the Collection composition root (#576 / #604).

Each facet is a thin view holding references to the collaborators it needs
(managers + coordinator), grouping the formerly-flat ``Collection`` surface
into ``reader`` / ``writer`` / ``graph`` / ``index``. The single ``Collection``
root owns one shared core and constructs the facets once, exposing them via
accessors of the same names (see the facet architecture in ``docs/design.md``).
"""

from markdown_vault_mcp.facets.graph import GraphFacet
from markdown_vault_mcp.facets.index import IndexFacet
from markdown_vault_mcp.facets.reader import ReaderFacet
from markdown_vault_mcp.facets.writer import WriterFacet

__all__ = ["GraphFacet", "IndexFacet", "ReaderFacet", "WriterFacet"]
