"""Domain helpers backing the vault MCP Apps UI (#905).

Holds the vault-specific logic that fills ``_server_apps.register_apps``'s
sentinel blocks, kept out of ``_server_apps.py`` so that file conforms to the
template skeleton (only its sentinel blocks and imports diverge):

- :func:`_compute_claude_app_domain` and :data:`_CDN_RESOURCE_DOMAINS`
  configure the app-shell resource's ``AppConfig`` (sandbox ``domain`` + CSP)
  in the ``DOMAIN-APP-RESOURCE`` block.
- :func:`_graph_view_payload` serializes a :class:`GraphView` into the SPA
  graph-tool wire shape for the ``DOMAIN-APP-TOOLS`` graph app-tools.
"""

from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.config import _ENV_PREFIX

if TYPE_CHECKING:
    from markdown_vault_mcp.types import GraphView

# All SPA dependencies are vendored inline (see scripts/vendor_spa.py).
# No external CDN domains needed at runtime.
_CDN_RESOURCE_DOMAINS: list[str] = []


def _compute_claude_app_domain() -> str | None:
    """Auto-compute Claude's MCP Apps sandbox domain from BASE_URL.

    Claude requires ``{sha256_prefix}.claudemcpcontent.com`` where the hash
    is derived from the full MCP endpoint URL the client connects to.

    Returns:
        The computed domain string, or ``None`` when ``BASE_URL`` is not set
        (e.g. stdio transport or local development).
    """
    base_url = os.environ.get(f"{_ENV_PREFIX}_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        return None
    http_path = os.environ.get(f"{_ENV_PREFIX}_HTTP_PATH", "/mcp").strip() or "/mcp"
    if not http_path.startswith("/"):
        http_path = f"/{http_path}"
    if len(http_path) > 1:
        http_path = http_path.rstrip("/")
    mcp_url = f"{base_url}{http_path}"
    hash_prefix = hashlib.sha256(mcp_url.encode()).hexdigest()[:32]
    return f"{hash_prefix}.claudemcpcontent.com"


def _graph_view_payload(view: GraphView, *, include_truncated: bool) -> dict[str, Any]:
    """Serialize a :class:`GraphView` into the SPA graph-tool wire shape.

    Edge endpoints map to ``from``/``to`` (vis-network's field names;
    ``from`` is a Python keyword, so the dataclass uses source/target).
    """
    payload: dict[str, Any] = {
        "nodes": [
            {
                "id": n.id,
                "label": n.label,
                "group": n.group,
                "folder": n.folder,
                "backlink_count": n.backlink_count,
                # OKF type (phase 2, #961): present only on an active OKF
                # bundle for notes that declare one, so non-OKF payloads
                # keep their exact prior shape.
                **({"note_type": n.note_type} if n.note_type is not None else {}),
            }
            for n in view.nodes
        ],
        "edges": [
            {"from": e.source, "to": e.target, "type": e.link_type} for e in view.edges
        ],
    }
    if include_truncated:
        payload["truncated"] = view.truncated
    return payload
