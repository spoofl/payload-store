from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from http.client import HTTPConnection
from pathlib import Path

from payload_store.store import DocumentStore

LAUNCHER = Path(__file__).resolve().parents[1] / "run.sh"


class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="payload launcher ")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.env = {**os.environ, "PY": sys.executable}
        for name in ("PAYLOAD_STORE_DB", "PAYLOAD_STORE_TOKEN", "PYTHONPATH"):
            self.env.pop(name, None)

    def test_help_and_invalid_arguments_do_not_create_files(self) -> None:
        for args, status, message in (
            (["--help"], 0, "--db"),
            (["--port", "invalid"], 2, "invalid int value"),
        ):
            with self.subTest(args=args):
                result = subprocess.run(
                    [str(LAUNCHER), *args], cwd=self.directory, env=self.env,
                    capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertIn(message, result.stdout + result.stderr)
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_foreground_launch_preserves_data_and_handles_sigterm(self) -> None:
        log_path = self.directory / "server.log"
        db_path = self.directory / "payload library.db"
        token = "launcher-test-token"
        with log_path.open("w") as output:
            process = subprocess.Popen(
                [str(LAUNCHER), "--port", "0", "--db", db_path.name],
                cwd=self.directory, env={**self.env, "PAYLOAD_STORE_TOKEN": token},
                stdout=output, stderr=output,
            )
        self.addCleanup(self.stop, process)

        deadline = time.monotonic() + 10
        while True:
            logs = log_path.read_text()
            match = re.search(r"Listening on http://127\.0\.0\.1:(\d+)", logs)
            if match:
                break
            if process.poll() is not None or time.monotonic() >= deadline:
                self.fail(f"Launcher did not start a foreground server: {logs}")
            time.sleep(0.02)

        with closing(HTTPConnection("127.0.0.1", int(match[1]), timeout=5)) as connection:
            connection.request("GET", "/ready")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()

            path = "/v1/documents/test/payloads/item"
            connection.request("GET", path)
            response = connection.getresponse()
            self.assertEqual(response.status, 401)
            response.read()

            connection.request(
                "PUT", path, b'{"saved":true}',
                {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()

        process.terminate()
        self.assertEqual(process.wait(timeout=10), 0, log_path.read_text())
        self.assertTrue(db_path.is_file())
        stored = DocumentStore(str(db_path)).get("test", "payloads", "item")
        self.assertIsNotNone(stored)
        self.assertEqual(json.loads(stored), {"saved": True})
        logs = log_path.read_text()
        self.assertIn("Stopped.", logs)
        self.assertNotIn(token, logs)

    @staticmethod
    def stop(process: subprocess.Popen) -> None:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
