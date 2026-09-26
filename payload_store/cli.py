from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading

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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not is_loopback(args.host) and not args.allow_external:
        log.error(
            "Refusing to bind non-loopback address %s without --allow-external.",
            args.host,
        )
        return 2

    store = DocumentStore(args.db)
    server = make_server(args.host, args.port, store, args.token)

    stop = threading.Event()

    def shutdown(_signum, _frame) -> None:
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    host, port = server.server_address[:2]
    log.info(
        "Document store listening on http://%s:%s (db=%s, auth=%s)",
        host,
        port,
        args.db,
        "on" if args.token else "off",
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
    log.info("Stopped.")
    return 0
