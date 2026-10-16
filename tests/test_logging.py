from __future__ import annotations

import logging
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from payload_store.cli import main, parse_args
from payload_store.server import Handler
from tests.test_payload_store import DOC_PATH, ServerFixture


class RequestLoggingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = ServerFixture()
        self.addCleanup(self.fx.close)

    def test_routine_polling_and_expected_conflicts_are_silent_by_default(self) -> None:
        with self.assertNoLogs("payload_store.server", level="INFO"):
            self.assertEqual(self.fx.request("GET", DOC_PATH)[0], 404)
            self.assertEqual(self.fx.request("PUT", DOC_PATH, b'{}')[0], 200)
            for _ in range(10):
                for path in ("/health", "/ready", DOC_PATH):
                    self.assertEqual(self.fx.request("GET", path)[0], 200)
                    self.assertEqual(self.fx.request("HEAD", path)[0], 200)
            self.assertEqual(self.fx.response("PUT", DOC_PATH, b'{}', headers={"If-None-Match": "*"})[0], 412)
            self.assertEqual(self.fx.request("DELETE", DOC_PATH)[0], 200)

    def test_verbose_logging_includes_request_status(self) -> None:
        self.assertFalse(parse_args([]).verbose)
        for flag in ("-v", "--verbose"):
            self.assertTrue(parse_args([flag]).verbose)
        with self.assertLogs("payload_store.server", level="DEBUG") as logs:
            self.assertEqual(self.fx.request("GET", "/health")[0], 200)
            self.assertEqual(self.fx.request("GET", DOC_PATH)[0], 404)
        self.assertEqual(len(logs.records), 2)
        self.assertIn('"GET /health HTTP/1.1" 200', logs.output[0])
        self.assertIn(f'"GET {DOC_PATH} HTTP/1.1" 404', logs.output[1])

    def test_database_and_protocol_errors_remain_visible_without_duplicate_access_lines(self) -> None:
        with (
            patch.object(self.fx.store, "get", side_effect=sqlite3.OperationalError("database is locked")),
            self.assertLogs("payload_store.server", level="INFO") as logs,
        ):
            self.assertEqual(self.fx.request("GET", DOC_PATH)[0], 503)
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(logs.records[0].levelno, logging.ERROR)
        self.assertIn("database is locked", logs.output[0])
        self.assertIn(self.fx.store.db_path, logs.output[0])
        with self.assertLogs("payload_store.server", level="INFO") as logs:
            self.assertEqual(self.fx.request("POST", "/health")[0], 501)
        self.assertEqual(len(logs.records), 1)
        self.assertIn("Unsupported method", logs.output[0])

    def test_request_text_cannot_clear_the_terminal_or_insert_log_lines(self) -> None:
        handler = Handler.__new__(Handler)
        handler.client_address = ("127.0.0.1", 12345)
        with self.assertLogs("payload_store.server", level="DEBUG") as logs:
            handler.log_message("%s", "GET /\x1b[2J\nnext\rline")
            handler.log_error("%s", "Bad request: \x1b[2J\nnext\rline")
        for record in logs.records:
            self.assertNotIn("\x1b", record.getMessage())
            self.assertNotIn("\n", record.getMessage())
            self.assertNotIn("\r", record.getMessage())
            self.assertIn(r"\x1b[2J\x0anext\x0dline", record.getMessage())


class StartupLoggingTest(unittest.TestCase):
    def test_bind_failure_is_an_actionable_error_without_a_traceback(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("payload_store.cli.logging.basicConfig"),
            patch("payload_store.cli.make_server", side_effect=OSError("Address already in use")),
            self.assertLogs("payload_store", level="ERROR") as logs,
        ):
            self.assertEqual(main(["--db", str(Path(tmp) / "test.db"), "--port", "8790"]), 1)
        self.assertEqual(len(logs.records), 1)
        self.assertIn("Cannot listen on 127.0.0.1:8790", logs.output[0])
        self.assertIn("Address already in use", logs.output[0])


if __name__ == "__main__":
    unittest.main()
