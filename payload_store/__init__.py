from __future__ import annotations

from .config import DEFAULT_DB_PATH, DEFAULT_HOST, DEFAULT_PORT, MAX_BODY_BYTES
from .server import Handler, make_server
from .store import DocumentStore

__version__ = "1.0.0"

__all__ = [
    "DEFAULT_DB_PATH",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "MAX_BODY_BYTES",
    "DocumentStore",
    "Handler",
    "make_server",
]
