from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ._util import now_ms


def document_etag(body: str) -> str:
    return f'"{hashlib.sha256(body.encode("utf-8")).hexdigest()}"'


class PreconditionFailed(Exception):
    """The document changed since the caller read it."""


def check_precondition(body: str | None, if_match: str | None, if_none_match: str | None) -> None:
    if if_none_match == "*" and body is not None:
        raise PreconditionFailed
    if if_match is not None and (body is None or if_match not in {"*", document_etag(body)}):
        raise PreconditionFailed


class DocumentStore:
    def __init__(self, db_path: str) -> None:
        path = Path(db_path).expanduser().resolve()
        self.db_path = str(path)
        self._db_uri = path.as_uri()
        with self._connect(create=True) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    db            TEXT NOT NULL,
                    coll          TEXT NOT NULL,
                    doc_id        TEXT NOT NULL,
                    body          TEXT NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    PRIMARY KEY (db, coll, doc_id)
                )
                """
            )
        self.check_ready()

    @contextmanager
    def _connect(self, *, create: bool = False) -> Iterator[sqlite3.Connection]:
        # Only initialization may create a database. A missing file at runtime
        # must not silently become an empty replacement database.
        mode = "rwc" if create else "rw"
        conn = sqlite3.connect(f"{self._db_uri}?mode={mode}", uri=True, timeout=5.0)
        try:
            with conn:
                conn.execute("PRAGMA busy_timeout=5000")
                yield conn
        finally:
            # Connection.__exit__ commits or rolls back, but does not close.
            conn.close()

    def check_ready(self) -> None:
        """Check the document schema and read/write access without changing data."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("SELECT db, coll, doc_id, body, updated_at_ms FROM documents LIMIT 0")
            # BEGIN IMMEDIATE alone can succeed on a read-only WAL database.
            conn.execute("UPDATE documents SET updated_at_ms=updated_at_ms WHERE 0")

    def get(self, database: str, collection: str, doc_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT body FROM documents WHERE db=? AND coll=? AND doc_id=?",
                (database, collection, doc_id),
            ).fetchone()
        return row[0] if row else None

    def put(
        self, database: str, collection: str, doc_id: str, body: str,
        if_match: str | None = None, if_none_match: str | None = None,
    ) -> int:
        updated = now_ms()
        with self._connect() as conn:
            # The read and write must share the writer lock across server threads.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT body FROM documents WHERE db=? AND coll=? AND doc_id=?",
                (database, collection, doc_id),
            ).fetchone()
            check_precondition(row[0] if row else None, if_match, if_none_match)
            conn.execute(
                """
                INSERT INTO documents (db, coll, doc_id, body, updated_at_ms)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (db, coll, doc_id)
                DO UPDATE SET body=excluded.body, updated_at_ms=excluded.updated_at_ms
                """,
                (database, collection, doc_id, body, updated),
            )
        return updated

    def delete(
        self, database: str, collection: str, doc_id: str,
        if_match: str | None = None, if_none_match: str | None = None,
    ) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT body FROM documents WHERE db=? AND coll=? AND doc_id=?",
                (database, collection, doc_id),
            ).fetchone()
            check_precondition(row[0] if row else None, if_match, if_none_match)
            cur = conn.execute(
                "DELETE FROM documents WHERE db=? AND coll=? AND doc_id=?",
                (database, collection, doc_id),
            )
            return cur.rowcount > 0
