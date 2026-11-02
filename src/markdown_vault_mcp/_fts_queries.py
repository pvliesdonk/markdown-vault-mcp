"""SQLite-owned exhaustive query predicates and bounded projections."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING

from markdown_vault_mcp.exceptions import InvalidRequestError
from markdown_vault_mcp.query_types import (
    NoteAggregate,
    NoteBucket,
    NoteCount,
    NoteGrouping,
    NoteQuery,
    NoteQueryItem,
    NoteQueryPage,
    NoteSection,
)
from markdown_vault_mcp.utils import normalize_folder

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

_SQL_MAX = 2**63 - 1


def register_functions(conn: sqlite3.Connection) -> None:
    """Register code-owned Unicode matching on a newly acquired connection.

    Args:
        conn: Thread-owned SQLite connection, before its first active cursor.
    """
    conn.create_function("query_casefold", 1, str.casefold, deterministic=True)


@contextmanager
def _read_view(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("SAVEPOINT note_query_read")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO note_query_read")
        raise
    finally:
        conn.execute("RELEASE note_query_read")


def _match(conn: sqlite3.Connection, spec: NoteQuery) -> str:
    query = spec.query.strip()
    try:
        # Validate BEFORE wrapping: invalid raw parentheses can balance the
        # scope wrapper and otherwise widen a body-only predicate.
        conn.execute(
            "SELECT rowid FROM notes_fts WHERE notes_fts MATCH ? LIMIT 1", (query,)
        ).fetchall()
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if not any(
            s in message
            for s in (
                "fts5: syntax error",
                "unterminated string",
                "no such column:",
                "fts5: malformed match",
                "unknown special query:",
            )
        ):
            raise
        query = " ".join('"' + word.replace('"', '""') + '"' for word in query.split())
    columns = {
        "metadata": "{path title heading summary}",
        "body": "{content}",
        "all": "{path title folder heading content summary}",
    }[spec.scope]
    return f"{columns}: ({query})"


class QueryExecutor:
    """Execute all projections over one validated, index-owned population."""

    def __init__(
        self, conn: sqlite3.Connection, fields: Sequence[str], spec: NoteQuery
    ) -> None:
        """Bind one validated request to its thread-owned storage connection.

        Args:
            conn: Keyword index connection.
            fields: Configured indexed frontmatter names.
            spec: Validated query request.
        """
        self.conn, self.spec = conn, spec
        stored = conn.execute(
            "SELECT value FROM meta WHERE key='indexed_frontmatter_fields'"
        ).fetchone()
        self.fields = (
            set(stored[0].split(",") if stored[0] else ()) if stored else set(fields)
        )

    def _field(self, key: str) -> None:
        if key in ("status", "stale", "trust_tier"):
            raise InvalidRequestError(
                f"Computed OKF field {key!r} is unsupported for exact queries; use ranked search"
            )
        if key not in self.fields:
            raise InvalidRequestError(
                f"Field {key!r} is not configured for indexed frontmatter queries"
            )

    def _conditions(self) -> tuple[str, list[object]]:
        spec = self.spec
        clauses = ["1"]
        params: list[object] = []
        if spec.path is not None:
            clauses.append("d.path = ?")
            params.append(spec.path)
        folder = normalize_folder(spec.folder)
        if folder is not None:
            clauses.append("(d.folder = ? OR (? != '' AND instr(d.folder, ?) = 1))")
            params.extend([folder, folder, folder + "/"])
        if spec.pattern is not None:
            patterns = {
                "glob": "d.path GLOB ?",
                "prefix": "instr(query_casefold(d.path), ?) = 1",
                "substring": "instr(query_casefold(d.path), ?) > 0",
            }
            clauses.append(patterns[spec.pattern_mode])
            params.append(
                spec.pattern if spec.pattern_mode == "glob" else spec.pattern.casefold()
            )
        for key, value in spec.filters.items():
            self._field(key)
            clauses.append(
                "EXISTS (SELECT 1 FROM document_tags t WHERE t.document_id=d.id AND t.tag_key=? AND t.tag_value=?)"
            )
            params.extend([key, value])
        for bound, operator in [(spec._lower, ">="), (spec._upper, "<")]:
            if bound is not None:
                clauses.append(f"d.modified_at {operator} ?")
                params.append(bound)
        return " AND ".join(clauses), params

    def _population(self) -> tuple[str, list[object]]:
        conditions, params = self._conditions()
        hit_cte, join, score = "", "", "NULL"
        if self.spec.query.strip():
            hit_cte = """hits AS MATERIALIZED (
                SELECT f.rowid, d.id AS document_id, -f.rank AS score FROM notes_fts f
                JOIN documents_live d ON d.path=f.path
                WHERE notes_fts MATCH ?),"""
            params.insert(0, _match(self.conn, self.spec))
            join, score = "JOIN hits h ON h.document_id=d.id", "MAX(h.score)"
        return (
            f"""WITH {hit_cte} population AS (
            SELECT d.id, d.path, d.folder, d.modified_at, {score} AS score
            FROM documents_live d {join} WHERE {conditions} GROUP BY d.id)""",  # noqa: S608, RUF100 - Bound values; S608 is enabled by the structural gate.
            params,
        )

    def _total(self, sql: str, params: list[object]) -> int:
        return int(
            self.conn.execute(
                sql + " SELECT COUNT(*) FROM population",  # noqa: S608, RUF100 - Only code-owned SQL; S608 is gate-only.
                params,
            ).fetchone()[0]
        )

    def count(self) -> NoteCount:
        """Count the exhaustive population without materializing excerpts.

        Returns:
            Exact distinct live-note total.
        """
        with _read_view(self.conn):
            sql, params = self._population()
            return NoteCount(self._total(sql, params))

    def _order(self) -> str:
        return {
            "path": "p.path",
            "folded_path": "query_casefold(p.path), p.path",
            "auto": "p.score DESC, p.path"
            if self.spec.query.strip()
            else "p.modified_at DESC, p.path",
        }[self.spec.order]

    def page(self) -> NoteQueryPage:
        """Read a bounded page and its exact total from one consistent view.

        Returns:
            Requested page, exact total and remaining-output indicator.
        """
        spec = self.spec
        with _read_view(self.conn):
            sql, params = self._population()
            total = self._total(sql, params)
            rows = self.conn.execute(
                sql  # noqa: S608, RUF100 - Code-owned SQL; S608 is gate-only.
                + f""" SELECT d.*, p.score FROM population p
                JOIN documents_live d ON d.id=p.id ORDER BY {self._order()} LIMIT ? OFFSET ?""",  # noqa: S608, RUF100 - Enum-owned order; S608 is gate-only.
                [*params, spec.limit, min(spec.offset, _SQL_MAX)],
            ).fetchall()
            sections = (
                self._sections(sql, params) if rows and spec.scope != "metadata" else {}
            )
            items: list[NoteQueryItem] = []
            for row in rows:
                item: NoteQueryItem = {
                    "path": row["path"],
                    "title": row["title"],
                    "folder": row["folder"],
                    "frontmatter": json.loads(row["frontmatter_json"] or "{}"),
                    "modified_at": row["modified_at"],
                    "score": row["score"],
                }
                if spec.scope != "metadata":
                    item["sections"] = sections.get(row["id"], [])
                items.append(item)
            return NoteQueryPage(
                items, total, spec.offset, spec.limit, spec.offset + len(items) < total
            )

    def _sections(self, sql: str, params: list[object]) -> dict[int, list[NoteSection]]:
        from markdown_vault_mcp.managers._ranking import compute_snippet_window

        spec = self.spec
        selected = f""", selected AS (
            SELECT p.id FROM population p ORDER BY {self._order()} LIMIT ? OFFSET ?),
            section_rows AS (
                SELECT s.*, row_number() OVER (
                    PARTITION BY s.document_id ORDER BY s.id) AS ordinal
                FROM sections s JOIN selected p ON p.id=s.document_id)"""  # noqa: S608, RUF100 - Code-owned ordering; S608 is gate-only.
        # Map physical insertion ordinals, retaining identical chunks rather
        # than joining their heading/content and multiplying duplicate rows.
        mapping, join, score = "", "", "0.0"
        if spec.query.strip():
            mapping = """, fts_rows AS (
                SELECT m.*, row_number() OVER (
                    PARTITION BY m.document_id ORDER BY m.fts_rowid) AS ordinal
                FROM notes_fts_rowid_map m JOIN selected p ON p.id=m.document_id)"""
            join = """JOIN fts_rows m USING (document_id, ordinal)
                JOIN hits h ON h.rowid=m.fts_rowid"""
            score = "h.score"
        ranked = f""", ranked AS (
            SELECT s.document_id, s.heading, s.content, {score} AS score,
                row_number() OVER (PARTITION BY s.document_id
                    ORDER BY {score} DESC, s.start_line, s.id) AS section_number
            FROM section_rows s {join})
            SELECT document_id, heading, content, score FROM ranked
            WHERE section_number <= ? ORDER BY document_id, section_number"""  # noqa: S608, RUF100 - Code-owned fragments; S608 is gate-only.
        rows = self.conn.execute(
            sql + selected + mapping + ranked,
            [
                *params,
                spec.limit,
                min(spec.offset, _SQL_MAX),
                min(spec.chunks_per_file, _SQL_MAX),
            ],
        ).fetchall()
        result: dict[int, list[NoteSection]] = {}
        for row in rows:
            result.setdefault(row["document_id"], []).append(
                NoteSection(
                    heading=row["heading"],
                    content=compute_snippet_window(
                        row["content"], spec.query, snippet_words=spec.snippet_words
                    ),
                    score=row["score"],
                )
            )
        return result

    def aggregate(self, grouping: NoteGrouping) -> NoteAggregate:
        """Group the exhaustive population, bounding only output buckets.

        Args:
            grouping: Folder, tag-value or UTC month grouping request.

        Returns:
            Exact total, bounded buckets, missing-value count and truncation.
        """
        with _read_view(self.conn):
            sql, params = self._population()
            total = self._total(sql, params)
            group_params: list[object] = []
            if grouping.by == "tag":
                self._field(grouping.field)
                values = """SELECT DISTINCT p.id, t.tag_value AS key FROM population p
                    LEFT JOIN document_tags t ON t.document_id=p.id AND t.tag_key=?"""
                group_params.append(grouping.field)
            else:
                key = {
                    "folder": "folder",
                    "month": "strftime('%Y-%m', CAST(modified_at AS INTEGER) - (modified_at < CAST(modified_at AS INTEGER)), 'unixepoch')",
                }[grouping.by]
                values = f"SELECT id, {key} AS key FROM population"  # noqa: S608, RUF100 - Code-owned key; S608 is gate-only.
            grouped = sql + f", bucket_values AS ({values})"
            bindings = [*params, *group_params]
            missing = int(
                self.conn.execute(
                    grouped + " SELECT COUNT(*) FROM bucket_values WHERE key IS NULL",  # noqa: S608, RUF100 - Code-owned SQL; S608 is gate-only.
                    bindings,
                ).fetchone()[0]
            )
            rows = self.conn.execute(
                grouped  # noqa: S608, RUF100 - Code-owned SQL; S608 is gate-only.
                + """ SELECT key, COUNT(*) AS count FROM bucket_values
                GROUP BY key ORDER BY count DESC, query_casefold(COALESCE(key, '')), key LIMIT ?""",
                [*bindings, grouping.limit + 1],
            ).fetchall()
            buckets = [
                NoteBucket(key=r["key"], count=r["count"])
                for r in rows[: grouping.limit]
            ]
            return NoteAggregate(total, buckets, missing, len(rows) > grouping.limit)
