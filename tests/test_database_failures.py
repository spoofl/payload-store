from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from payload_store.cli import main
from payload_store.store import DocumentStore, PreconditionFailed
from tests.test_payload_store import DOC_PATH, ServerFixture


class DatabaseFailureHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = ServerFixture()
        self.addCleanup(self.fx.close)

    def test_readiness_checks_database_and_does_not_change_documents(self) -> None:
        self.fx.request("PUT", DOC_PATH, b'{"saved":true}')
        for method in ("GET", "HEAD"):
            with self.subTest(method=method):
                status, body, headers = self.fx.response(method, "/ready")
                self.assertEqual(status, 200)
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertEqual(body, b"" if method == "HEAD" else b'{"status": "ok"}')
        self.assertEqual(self.fx.request("GET", DOC_PATH), (200, b'{"saved":true}'))

    def test_database_directory_unavailable_then_restored(self) -> None:
        self.fx.request("PUT", DOC_PATH, b'{"saved":true}')
        database_dir = Path(self.fx.store.db_path).parent
        moved_dir = database_dir.with_name(database_dir.name + "-unavailable")
        database_dir.rename(moved_dir)
        try:
            for method, path, body in (
                ("GET", DOC_PATH, None), ("HEAD", DOC_PATH, None),
                ("PUT", DOC_PATH, b'{}'), ("DELETE", DOC_PATH, None),
                ("GET", "/ready", None), ("HEAD", "/ready", None),
            ):
                with self.subTest(method=method, path=path):
                    with self.assertLogs("payload_store.server", level="ERROR") as logs:
                        status, data, headers = self.fx.response(method, path, body)
                    self.assertEqual(status, 503)
                    self.assertEqual(headers["Content-Type"], "application/json")
                    self.assertEqual(headers["Cache-Control"], "no-store")
                    if method == "HEAD":
                        self.assertEqual(data, b"")
                    else:
                        self.assertEqual(json.loads(data), {"error": "database unavailable"})
                    self.assertIn(self.fx.store.db_path, logs.output[0])
                    self.assertIn("unable to open database file", logs.output[0])
            self.assertEqual(self.fx.request("GET", "/health")[0], 200)
        finally:
            moved_dir.rename(database_dir)
        self.assertEqual(self.fx.request("GET", "/ready")[0], 200)
        self.assertEqual(self.fx.request("GET", DOC_PATH), (200, b'{"saved":true}'))

    def test_missing_database_is_not_recreated_by_requests(self) -> None:
        database = Path(self.fx.store.db_path)
        database.unlink()
        with self.assertLogs("payload_store.server", level="ERROR"):
            self.assertEqual(self.fx.request("GET", DOC_PATH)[0], 503)
            self.assertEqual(self.fx.request("GET", "/ready")[0], 503)
        self.assertFalse(database.exists())

    def test_readiness_detects_missing_schema(self) -> None:
        with closing(sqlite3.connect(self.fx.store.db_path)) as conn, conn:
            conn.execute("DROP TABLE documents")
        with self.assertLogs("payload_store.server", level="ERROR"):
            self.assertEqual(self.fx.request("GET", "/ready")[0], 503)

    def test_database_errors_after_connect_return_503(self) -> None:
        # Connecting succeeds, but the database can no longer service queries.
        with closing(sqlite3.connect(self.fx.store.db_path)) as conn, conn:
            conn.execute("DROP TABLE documents")
        with self.assertLogs("payload_store.server", level="ERROR"):
            for method, body in (("GET", None), ("PUT", b'{}'), ("DELETE", None)):
                with self.subTest(method=method):
                    self.assertEqual(self.fx.request(method, DOC_PATH, body)[0], 503)

    @unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() != 0, "requires non-root POSIX permissions")
    def test_read_only_database_reports_unready_and_recovers(self) -> None:
        self.fx.request("PUT", DOC_PATH, b'{"saved":true}')
        database = Path(self.fx.store.db_path)
        original_mode = database.stat().st_mode
        database.chmod(0o400)
        try:
            with self.assertLogs("payload_store.server", level="ERROR"):
                self.assertEqual(self.fx.request("GET", "/ready")[0], 503)
                self.assertEqual(self.fx.request("PUT", DOC_PATH, b'{}')[0], 503)
                self.assertEqual(self.fx.request("DELETE", DOC_PATH)[0], 503)
        finally:
            # SQLite can create read-only WAL/SHM files while the database is
            # read-only; restoring access includes those files as well.
            for suffix in ("", "-wal", "-shm"):
                file = Path(f"{database}{suffix}")
                if file.exists():
                    file.chmod(original_mode)
        self.assertEqual(self.fx.request("GET", "/ready")[0], 200)
        self.assertEqual(self.fx.request("GET", DOC_PATH), (200, b'{"saved":true}'))
        self.assertEqual(self.fx.request("PUT", DOC_PATH, b'{}')[0], 200)


class DatabaseLifecycleTest(unittest.TestCase):
    def test_relative_database_path_survives_working_directory_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original_cwd = Path.cwd()
            try:
                os.chdir(tmp)
                # URI-special characters must still address the same file.
                store = DocumentStore("store #1?.db")
                store.put("db", "coll", "doc", '{"saved":true}')
                os.chdir(original_cwd)
                self.assertEqual(store.db_path, str(Path(tmp) / "store #1?.db"))
                self.assertEqual(store.get("db", "coll", "doc"), '{"saved":true}')
            finally:
                os.chdir(original_cwd)

    def test_connections_close_after_success_and_transaction_failure(self) -> None:
        connections: list[sqlite3.Connection] = []
        connect = sqlite3.connect

        def open_connection(*args, **kwargs):
            conn = connect(*args, **kwargs)
            connections.append(conn)
            return conn

        with tempfile.TemporaryDirectory() as tmp:
            with patch("payload_store.store.sqlite3.connect", side_effect=open_connection):
                store = DocumentStore(str(Path(tmp) / "test.db"))
                store.put("db", "coll", "doc", '{}')
                store.get("db", "coll", "doc")
                with self.assertRaises(PreconditionFailed):
                    store.put("db", "coll", "doc", '{}', if_none_match="*")
                self.assertTrue(store.delete("db", "coll", "doc"))
            try:
                for conn in connections:
                    with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed database"):
                        conn.execute("SELECT 1")
            finally:
                for conn in connections:
                    conn.close()

    def test_startup_reports_unusable_database_without_binding_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for database in (Path(tmp) / "missing" / "test.db", Path(tmp)):
                with self.subTest(database=database):
                    with (
                        patch("payload_store.cli.logging.basicConfig"),
                        patch("payload_store.cli.make_server") as make_server,
                    ):
                        with self.assertLogs("payload_store", level="ERROR") as logs:
                            self.assertEqual(main(["--db", str(database)]), 1)
                        make_server.assert_not_called()
                    self.assertIn(str(database), logs.output[0])
                    self.assertIn("parent directory", logs.output[0])

    @unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() != 0, "requires non-root POSIX permissions")
    def test_startup_rejects_read_only_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "test.db"
            DocumentStore(str(database))
            database.chmod(0o400)
            try:
                with (
                    patch("payload_store.cli.logging.basicConfig"),
                    patch("payload_store.cli.make_server") as make_server,
                    self.assertLogs("payload_store", level="ERROR") as logs,
                ):
                    self.assertEqual(main(["--db", str(database)]), 1)
                    make_server.assert_not_called()
                self.assertIn(str(database), logs.output[0])
                self.assertIn("readonly", logs.output[0])
            finally:
                database.chmod(0o600)


if __name__ == "__main__":
    unittest.main()
