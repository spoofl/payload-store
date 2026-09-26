from __future__ import annotations

import os

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_DB_PATH = os.environ.get("PAYLOAD_STORE_DB", "payload_store.db")

MAX_BODY_BYTES = 64 * 1024 * 1024

MAX_SEGMENT_LEN = 256
FORBIDDEN_SEGMENT_BYTES = frozenset(b'/\\"$')
