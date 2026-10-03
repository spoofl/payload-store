#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
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
        status, data, _ = self.response(method, path, body, token)
        return status, data

    def response(
        self, method: str, path: str, body: bytes | None = None,
        token: str | None = None, headers: dict | None = None,
    ):
        headers = dict(headers or {})
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.read(), error.headers

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
WORKFLOW_PATH = "/v1/documents/payload-library/workflows/definitions"
WORKFLOWS = {
    "schema": "workflowLibrary/1",
    "version": 1,
    "imports": [],
    "definitions": [{
        "id": "review", "revision": 1, "name": "Evidence review", "description": "Unicode: 你好",
        "modelMode": "profiles",
        "steps": [
            {"id": "inspect", "title": "Inspect", "instructions": "Inspect the saved evidence."},
            {"id": "report", "title": "Report", "instructions": "Write a report.", "dependsOn": ["inspect"]},
        ],
    }],
}


class DocumentStoreHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = ServerFixture()
        self.addCleanup(self.fx.close)

    def test_health_ok(self) -> None:
        status, body = self.fx.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "ok", "capabilities": ["conditionalWrites"]})

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

    def test_workflows_and_payloads_are_independent_and_persist(self) -> None:
        self.fx.request("PUT", DOC_PATH, json.dumps(LIBRARY).encode())
        self.fx.request("PUT", WORKFLOW_PATH, json.dumps(WORKFLOWS).encode())
        status, body, headers = self.fx.response("GET", WORKFLOW_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), WORKFLOWS)
        self.assertEqual(headers["Cache-Control"], "no-store")
        reopened = payload_store.DocumentStore(self.fx.server.RequestHandlerClass.store.db_path)
        self.assertEqual(json.loads(reopened.get("payload-library", "workflows", "definitions")), WORKFLOWS)
        empty = dict(WORKFLOWS, definitions=[])
        self.fx.request("PUT", WORKFLOW_PATH, json.dumps(empty).encode())
        self.assertEqual(json.loads(self.fx.request("GET", WORKFLOW_PATH)[1]), empty)
        self.assertEqual(json.loads(self.fx.request("GET", DOC_PATH)[1]), LIBRARY)
        self.assertEqual(self.fx.request("GET", WORKFLOW_PATH.replace("payload-library", "other"))[0], 404)

    def test_conditional_creation_never_replaces_an_existing_library(self) -> None:
        headers = {"If-None-Match": "*"}
        status, _, created = self.fx.response(
            "PUT", WORKFLOW_PATH, json.dumps(WORKFLOWS).encode(), headers=headers
        )
        self.assertEqual(status, 200)
        self.assertRegex(created["ETag"], r'^"[0-9a-f]{64}"$')
        status, _, _ = self.fx.response("PUT", WORKFLOW_PATH, b'{}', headers=headers)
        self.assertEqual(status, 412)
        status, body, read = self.fx.response("GET", WORKFLOW_PATH)
        self.assertEqual(json.loads(body), WORKFLOWS)
        self.assertEqual(read["ETag"], created["ETag"])
        status, body, head = self.fx.response("HEAD", WORKFLOW_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(head["ETag"], read["ETag"])

    def test_stale_updates_and_deletes_are_rejected(self) -> None:
        self.fx.request("PUT", WORKFLOW_PATH, json.dumps(WORKFLOWS).encode())
        _, _, first = self.fx.response("GET", WORKFLOW_PATH)
        headers = {"If-Match": first["ETag"]}
        updated = dict(WORKFLOWS, definitions=[])
        status, _, second = self.fx.response(
            "PUT", WORKFLOW_PATH, json.dumps(updated).encode(), headers=headers
        )
        self.assertEqual(status, 200)
        self.assertNotEqual(first["ETag"], second["ETag"])
        self.assertEqual(self.fx.response("PUT", WORKFLOW_PATH, b'{}', headers=headers)[0], 412)
        self.assertEqual(self.fx.response("DELETE", WORKFLOW_PATH, headers=headers)[0], 412)
        self.assertEqual(json.loads(self.fx.request("GET", WORKFLOW_PATH)[1]), updated)
        self.assertEqual(
            self.fx.response("DELETE", WORKFLOW_PATH, headers={"If-Match": second["ETag"]})[0], 200
        )
        self.assertEqual(self.fx.response("PUT", WORKFLOW_PATH, b'{}', headers=headers)[0], 412)
        self.assertEqual(self.fx.request("GET", WORKFLOW_PATH)[0], 404)

    def test_concurrent_writers_have_one_winner(self) -> None:
        self.fx.request("PUT", WORKFLOW_PATH, json.dumps(WORKFLOWS).encode())
        _, _, response = self.fx.response("GET", WORKFLOW_PATH)
        barrier = threading.Barrier(2)

        def write(index):
            barrier.wait()
            body = json.dumps(dict(WORKFLOWS, imports=[str(index)])).encode()
            return self.fx.response("PUT", WORKFLOW_PATH, body, headers={"If-Match": response["ETag"]})[0]

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(write, [1, 2])), [200, 412])

    def test_unsupported_preconditions_fail_closed(self) -> None:
        for headers in [
            {"If-Match": 'W/"weak"'}, {"If-Match": "invalid"},
            {"If-None-Match": '"etag"'}, {"If-Match": "*", "If-None-Match": "*"},
        ]:
            with self.subTest(headers=headers):
                self.assertEqual(self.fx.response("PUT", WORKFLOW_PATH, b'{}', headers=headers)[0], 400)
        self.assertEqual(self.fx.request("GET", WORKFLOW_PATH)[0], 404)


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

    def test_workflow_reads_and_writes_require_the_same_token(self) -> None:
        body = json.dumps(WORKFLOWS).encode()
        self.assertEqual(self.fx.request("PUT", WORKFLOW_PATH, body)[0], 401)
        self.assertEqual(self.fx.request("PUT", WORKFLOW_PATH, body, token="s3cret")[0], 200)
        self.assertEqual(self.fx.request("GET", WORKFLOW_PATH)[0], 401)
        self.assertEqual(self.fx.request("DELETE", WORKFLOW_PATH)[0], 401)
        self.assertEqual(json.loads(self.fx.request("GET", WORKFLOW_PATH, token="s3cret")[1]), WORKFLOWS)


if __name__ == "__main__":
    unittest.main()
