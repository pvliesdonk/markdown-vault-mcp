"""Entry shim for the markdown-vault-mcp .mcpb bundle.

This file is only executed when the host uses the ``uv run src/server.py``
code path (``server.type: "uv"`` + ``entry_point``).  The primary launch path
is ``mcp_config.command: "uvx"`` which fetches markdown-vault-mcp directly
from PyPI and bypasses this shim entirely.

The shim appends ``serve`` to ``sys.argv`` (unless already present) because
``cli.main()`` delegates to argparse and requires a subcommand.  Existing
argv entries (e.g. ``-v`` for verbose logging) are preserved so the bundle
remains debuggable when invoked directly.
"""

import sys

from markdown_vault_mcp.cli import main

if "serve" not in sys.argv[1:]:
    sys.argv.append("serve")
main()
