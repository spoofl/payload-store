from __future__ import annotations

import hmac
import json
import logging
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from ._util import segment_is_safe
from .config import MAX_BODY_BYTES
from .store import DocumentStore, PreconditionFailed, document_etag

log = logging.getLogger(__name__)


class Handler(BaseHTTPRequestHandler):
    server_version = "PayloadStore/1"
    protocol_version = "HTTP/1.1"

    store: DocumentStore
    auth_token: str | None

    def _send_json(self, status: HTTPStatus, payload: dict, etag: str | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send_body(status, body, etag)

    def _send_body(self, status: HTTPStatus, body: bytes, etag: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if etag is not None:
            self.send_header("ETag", etag)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _authorized(self) -> bool:
        if not self.auth_token:
            return True
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        return hmac.compare_digest(header[len(prefix) :], self.auth_token)

    def _route(self) -> tuple[str, list[str]]:
        path = urlsplit(self.path).path
        parts = [unquote(p) for p in path.split("/") if p != ""]
        return path, parts

    def _document_key(self, parts: list[str]) -> tuple[str, str, str] | None:
        if len(parts) != 5 or parts[0] != "v1" or parts[1] != "documents":
            return None
        database, collection, doc_id = parts[2], parts[3], parts[4]
        if not all(segment_is_safe(seg) for seg in (database, collection, doc_id)):
            return None
        return database, collection, doc_id

    def _preconditions(self) -> tuple[str | None, str | None]:
        if_match = self.headers.get("If-Match")
        if_none_match = self.headers.get("If-None-Match")
        if (
            len(self.headers.get_all("If-Match", [])) > 1
            or len(self.headers.get_all("If-None-Match", [])) > 1
            or (if_match is not None and not re.fullmatch(r'\*|"[0-9a-f]{64}"', if_match))
            or if_none_match not in {None, "*"}
            or (if_match is not None and if_none_match is not None)
        ):
            raise ValueError('Use one If-Match ETag (or *) or If-None-Match: *.')
        return if_match, if_none_match

    def _read_json_body(self) -> tuple[str | None, str | None]:
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            return None, "Content-Length is required."
        try:
            length = int(length_header)
        except ValueError:
            return None, "Content-Length is not a number."
        if length < 0:
            return None, "Content-Length is negative."
        if length > MAX_BODY_BYTES:
            return None, "Request body exceeds the size limit."
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            return None, f"Body is not valid JSON: {error}"
        if not isinstance(parsed, dict):
            return None, "Body must be a JSON object."
        return json.dumps(parsed, separators=(",", ":")), None

    def do_GET(self) -> None:
        _, parts = self._route()
        if parts == ["health"]:
            self._send_json(HTTPStatus.OK, {"status": "ok", "capabilities": ["conditionalWrites"]})
            return
        if not self._authorized():
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        key = self._document_key(parts)
        if key is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        body = self.store.get(*key)
        if body is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        self._send_body(HTTPStatus.OK, body.encode("utf-8"), document_etag(body))

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_PUT(self) -> None:
        if not self._authorized():
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        _, parts = self._route()
        key = self._document_key(parts)
        if key is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown route"})
            return
        body, error = self._read_json_body()
        if error is not None:
            self.close_connection = True
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": error})
            return
        assert body is not None
        try:
            if_match, if_none_match = self._preconditions()
            updated = self.store.put(*key, body, if_match=if_match, if_none_match=if_none_match)
        except ValueError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        except PreconditionFailed:
            self._send_json(HTTPStatus.PRECONDITION_FAILED, {"error": "document changed"})
            return
        self._send_json(HTTPStatus.OK, {"status": "ok", "updatedAtMs": updated}, document_etag(body))

    def do_DELETE(self) -> None:
        if not self._authorized():
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        _, parts = self._route()
        key = self._document_key(parts)
        if key is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown route"})
            return
        try:
            if_match, if_none_match = self._preconditions()
            removed = self.store.delete(*key, if_match=if_match, if_none_match=if_none_match)
        except ValueError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        except PreconditionFailed:
            self._send_json(HTTPStatus.PRECONDITION_FAILED, {"error": "document changed"})
            return
        status = HTTPStatus.OK if removed else HTTPStatus.NOT_FOUND
        self._send_json(status, {"status": "ok" if removed else "not found"})

    def log_message(self, fmt: str, *args) -> None:
        log.info("%s - %s", self.address_string(), fmt % args)


def make_server(
    host: str, port: int, store: DocumentStore, token: str | None
) -> ThreadingHTTPServer:
    handler = type(
        "BoundHandler",
        (Handler,),
        {"store": store, "auth_token": token},
    )
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server
