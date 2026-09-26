#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import payload_store


class ServerFixture:
    def __init__(self, token: str | None = None) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = str(Path(self._tmp.name) / "test.db")
        store = payload_store.DocumentStore(db_path)
        self.server = payload_store.make_server("127.0.0.1", 0, store, token)
        self.host, self.port = self.server.server_address[:2]
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base(self) -> str:
        return f"http://{self.host}:{self.port}"

    def request(self, method: str, path: str, body: bytes | None = None, token: str | None = None):
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.read()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._tmp.cleanup()


DOC_PATH = "/v1/documents/payload-library/payloads/customPayloads"
LIBRARY = {
    "_id": "customPayloads",
    "schema": "payloadLibrary/1",
    "version": 3,
    "updatedAtMs": 1,
    "library": {"version": 3, "collections": [{"name": "xss", "items": ["<svg onload=alert(1)>"]}]},
}


class DocumentStoreHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = ServerFixture()
        self.addCleanup(self.fx.close)

    def test_health_ok(self) -> None:
        status, body = self.fx.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "ok"})

    def test_missing_document_is_404(self) -> None:
        status, _ = self.fx.request("GET", DOC_PATH)
        self.assertEqual(status, 404)

    def test_upsert_then_read_roundtrip(self) -> None:
        status, body = self.fx.request("PUT", DOC_PATH, json.dumps(LIBRARY).encode())
        self.assertEqual(status, 200)
        self.assertIn("updatedAtMs", json.loads(body))

        status, body = self.fx.request("GET", DOC_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), LIBRARY)

    def test_upsert_replaces_previous_body(self) -> None:
        self.fx.request("PUT", DOC_PATH, json.dumps(LIBRARY).encode())
        updated = dict(LIBRARY, version=4)
        self.fx.request("PUT", DOC_PATH, json.dumps(updated).encode())
        status, body = self.fx.request("GET", DOC_PATH)
        self.assertEqual(json.loads(body)["version"], 4)

    def test_delete(self) -> None:
        self.fx.request("PUT", DOC_PATH, json.dumps(LIBRARY).encode())
        status, _ = self.fx.request("DELETE", DOC_PATH)
        self.assertEqual(status, 200)
        status, _ = self.fx.request("GET", DOC_PATH)
        self.assertEqual(status, 404)

    def test_non_object_body_rejected(self) -> None:
        status, _ = self.fx.request("PUT", DOC_PATH, b"[1,2,3]")
        self.assertEqual(status, 400)

    def test_invalid_json_rejected(self) -> None:
        status, _ = self.fx.request("PUT", DOC_PATH, b"{not json")
        self.assertEqual(status, 400)

    def test_unsafe_path_segment_rejected(self) -> None:
        status, _ = self.fx.request("GET", "/v1/documents/db/coll/%24bad")
        self.assertEqual(status, 404)


class AuthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = ServerFixture(token="s3cret")
        self.addCleanup(self.fx.close)

    def test_health_needs_no_token(self) -> None:
        status, _ = self.fx.request("GET", "/health")
        self.assertEqual(status, 200)

    def test_document_requires_token(self) -> None:
        status, _ = self.fx.request("GET", DOC_PATH)
        self.assertEqual(status, 401)

    def test_wrong_token_rejected(self) -> None:
        status, _ = self.fx.request("GET", DOC_PATH, token="nope")
        self.assertEqual(status, 401)

    def test_correct_token_accepted(self) -> None:
        status, _ = self.fx.request("GET", DOC_PATH, token="s3cret")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
