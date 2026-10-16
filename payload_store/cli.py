from __future__ import annotations

import argparse
import logging
import os
import signal
import sqlite3
import sys
import threading
from pathlib import Path

from ._util import is_loopback
from .config import DEFAULT_DB_PATH, DEFAULT_HOST, DEFAULT_PORT
from .server import make_server
from .store import DocumentStore

log = logging.getLogger("payload_store")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="payload-store",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"bind address (default {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"bind port (default {DEFAULT_PORT})")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite file path (default ./payload_store.db)")
    parser.add_argument(
        "--token",
        default=os.environ.get("PAYLOAD_STORE_TOKEN"),
        help="optional bearer token required on document routes (env PAYLOAD_STORE_TOKEN)",
    )
    parser.add_argument(
        "--allow-external",
        action="store_true",
        help="permit binding a non-loopback address (off by default)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="show HTTP request logs (default: startup, shutdown, and server errors only)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    if not is_loopback(args.host) and not args.allow_external:
        log.error(
            "Refusing to bind non-loopback address %s without --allow-external.",
            args.host,
        )
        return 2

    try:
        args.db = str(Path(args.db).expanduser().resolve())
        store = DocumentStore(args.db)
    except (OSError, sqlite3.Error, ValueError) as error:
        log.error(
            "Cannot open database %r: %s. Ensure the parent directory exists and "
            "the server user can read/write the database and its journal files, "
            "and write to the directory.",
            args.db, error,
        )
        return 1
    try:
        server = make_server(args.host, args.port, store, args.token)
    except OSError as error:
        log.error("Cannot listen on %s:%s: %s", args.host, args.port, error)
        return 1

    stop = threading.Event()

    def shutdown(_signum, _frame) -> None:
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    host, port = server.server_address[:2]
    log.info(
        "Listening on http://%s:%s | auth: %s | Ctrl+C to stop",
        host,
        port,
        "on" if args.token else "off",
    )
    log.info("Database: %s", store.db_path)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    log.info("Stopped.")
    return 0
