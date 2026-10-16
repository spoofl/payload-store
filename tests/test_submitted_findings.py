from __future__ import annotations

import json
import unittest
from concurrent.futures import ThreadPoolExecutor

from payload_store import DocumentStore

from tests.test_payload_store import (
    DOC_PATH,
    LIBRARY,
    SKILL_PATH,
    SKILLS,
    WORKFLOW_PATH,
    WORKFLOWS,
    ServerFixture,
)

PATH = "/v1/documents/payload-library/findings/submitted"


def ledger(record_id: str = "1" * 32) -> dict:
    return {"schema": "submittedFindings/1", "version": 1, "findings": [{
        "id": record_id, "revision": 1, "submittedAt": 1,
        "reportUrl": f"https://reports.example.test/{record_id}", "notes": "Submitted: 你好",
        "sourceKeys": [record_id * 2], "fingerprints": [record_id[::-1] * 2],
        "finding": {
            "id": "local-one", "title": "Account data exposed", "category": "Access control",
            "severity": "high", "status": "verified", "description": "Another account's data returned.",
            "proofOfConcept": "GET /account HTTP/1.1\r\nHost: api.example.test\r\n\r\n",
            "reproduction": "Request another account.", "verification": "Private data returned.",
            "recommendation": "Check ownership.", "source": "manual", "confidence": 0.9,
            "cvssVersion": "", "cvssVector": "", "cvssScore": None, "cvssMetricEvidence": [],
            "createdAt": 1, "updatedAt": 1,
            "data": json.dumps({"targets": [{"method": "GET", "host": "api.example.test", "path": "/account"}]}),
        },
    }]}


class SubmittedFindingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = ServerFixture(token="secret")
        self.addCleanup(self.fx.close)

    def test_shared_ledger_is_authenticated_persistent_and_independent(self) -> None:
        body = json.dumps(ledger()).encode()
        for method in ("GET", "PUT", "DELETE", "HEAD"):
            self.assertEqual(self.fx.response(method, PATH, body if method == "PUT" else None)[0], 401)
        for path, value in ((DOC_PATH, LIBRARY), (WORKFLOW_PATH, WORKFLOWS), (SKILL_PATH, SKILLS)):
            self.assertEqual(self.fx.request("PUT", path, json.dumps(value).encode(), token="secret")[0], 200)
        status, _, headers = self.fx.response("PUT", PATH, body, token="secret", headers={"If-None-Match": "*"})
        self.assertEqual(status, 200)
        reopened = DocumentStore(self.fx.store.db_path)
        self.assertEqual(json.loads(reopened.get("payload-library", "findings", "submitted")), ledger())
        status, contents, read_headers = self.fx.response("GET", PATH, token="secret")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(contents), ledger())
        self.assertEqual(headers["ETag"], read_headers["ETag"])
        for path, value in ((DOC_PATH, LIBRARY), (WORKFLOW_PATH, WORKFLOWS), (SKILL_PATH, SKILLS)):
            self.assertEqual(json.loads(self.fx.request("GET", path, token="secret")[1]), value)

    def test_concurrent_clients_must_merge_submissions_and_stale_removals_fail(self) -> None:
        empty = {"schema": "submittedFindings/1", "version": 1, "findings": []}
        _, _, headers = self.fx.response("PUT", PATH, json.dumps(empty).encode(), token="secret")
        initial_etag = headers["ETag"]
        values = [ledger("a" * 32), ledger("b" * 32)]

        def write(value: dict):
            return self.fx.response("PUT", PATH, json.dumps(value).encode(), token="secret",
                                    headers={"If-Match": initial_etag})[0]

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, values))
        self.assertEqual(sorted(results), [200, 412])
        _, body, headers = self.fx.response("GET", PATH, token="secret")
        current = json.loads(body)
        loser = values[results.index(412)]
        current["findings"].extend(loser["findings"])
        self.assertEqual(self.fx.response("PUT", PATH, json.dumps(current).encode(), token="secret",
                                         headers={"If-Match": headers["ETag"]})[0], 200)
        self.assertEqual(self.fx.response("DELETE", PATH, token="secret",
                                         headers={"If-Match": headers["ETag"]})[0], 412)
        self.assertEqual(len(json.loads(self.fx.request("GET", PATH, token="secret")[1])["findings"]), 2)
        self.assertEqual(self.fx.response("PUT", PATH, json.dumps(empty).encode(), token="secret",
                                         headers={"If-None-Match": "*"})[0], 412)

    def test_cleared_ledger_stays_empty_after_restart(self) -> None:
        _, _, headers = self.fx.response("PUT", PATH, json.dumps(ledger()).encode(), token="secret")
        empty = {"schema": "submittedFindings/1", "version": 1, "findings": []}
        self.assertEqual(self.fx.response("PUT", PATH, json.dumps(empty).encode(), token="secret",
                                         headers={"If-Match": headers["ETag"]})[0], 200)
        reopened = DocumentStore(self.fx.store.db_path)
        self.assertEqual(json.loads(reopened.get("payload-library", "findings", "submitted")), empty)


if __name__ == "__main__":
    unittest.main()
