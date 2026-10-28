"""Another process owning a keyword index, as a running server would (#1890)."""

from __future__ import annotations

import select
import subprocess
import sys
import textwrap
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parents[2])

# Opens the index as its owner, optionally after indexing one note, and holds
# it until a line arrives on stdin.
_HOLDER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from markdown_vault_mcp.stores.sqlite_keyword import SqliteKeywordBackend
    from markdown_vault_mcp.types import Chunk, ParsedNote
    index = SqliteKeywordBackend().open(Path(sys.argv[1]))
    if sys.argv[2] == "note":
        index.upsert_note(
            ParsedNote(
                path="alpha.md",
                frontmatter={},
                title="Alpha",
                chunks=[Chunk(heading=None, heading_level=0, content="Dragons.", start_line=0)],
                content_hash="h",
                modified_at=0.0,
            )
        )
    print("held", flush=True)
    sys.stdin.readline()
    index.close()
    """
)


class Holder:
    """Another process owning the keyword index at *db* until :meth:`stop`.

    Args:
        db: The index file.
        note: Index one note before holding, so the file has rows.
        timeout: Seconds to wait for the process to report it holds the index.
    """

    def __init__(self, db: Path, *, note: bool = False, timeout: float = 30) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-c", _HOLDER, str(db), "note" if note else "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=_ROOT,
        )
        assert self.process.stdout is not None
        ready, _, _ = select.select([self.process.stdout], [], [], timeout)
        line = self.process.stdout.readline().strip() if ready else ""
        if line != "held":
            self.process.kill()
            _, err = self.process.communicate(timeout=30)
            raise AssertionError(f"the holder did not start: {err}")

    @property
    def pid(self) -> int:
        return self.process.pid

    def stop(self) -> None:
        """Close the index and wait for the process to exit."""
        if self.process.poll() is None:
            assert self.process.stdin is not None
            self.process.stdin.write("\n")
            self.process.stdin.flush()
        self.process.wait(timeout=30)
