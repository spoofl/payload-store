from __future__ import annotations

import hashlib
import sqlite3

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
        self.db_path = db_path
        with self._connect() as conn:
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

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

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
