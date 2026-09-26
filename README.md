# payload-store

A tiny, dependency-free JSON document store served over loopback HTTP. It
replaces the MongoDB backend of the payload-library feature.

## Why this exists

The payload library only needs to keep a handful of JSON documents keyed by
`(database, collection, id)` — load one, upsert one, ping. MongoDB was overkill,
and MongoDB Atlas is actively refused by the app's egress guard: `mongodb+srv://`
does SRV discovery and Atlas clusters are replica sets, both of which resolve
destination hostnames dynamically that the guard cannot vet before the socket is
opened, so they fail closed.

A loopback HTTP service sidesteps all of that: it is unambiguously the
operator's own machine, so there is no external destination to vet.

## Requirements

Python 3.11+ standard library. Nothing to install to run it.

## Run

From a checkout, no install needed:

```bash
python3 -m payload_store                 # 127.0.0.1:8787, ./payload_store.db
python3 -m payload_store --port 8790 --db /var/lib/payloads.db
PAYLOAD_STORE_TOKEN=s3cret python3 -m payload_store   # require a bearer token
```

Or install it to get a `payload-store` console command:

```bash
pip install .
payload-store --port 8790
```

Options: `--host` (default `127.0.0.1`), `--port` (default `8787`), `--db`
(default `./payload_store.db`), `--token` (or `PAYLOAD_STORE_TOKEN`),
`--allow-external` (permit a non-loopback bind; off by default and refused
otherwise).

## HTTP contract

| Method | Path | Meaning |
| --- | --- | --- |
| `GET` | `/health` | liveness; no auth required |
| `GET` | `/v1/documents/{database}/{collection}/{id}` | return the stored JSON object, or `404` if absent |
| `PUT` | `/v1/documents/{database}/{collection}/{id}` | replace-with-upsert; body is a JSON object |
| `DELETE` | `/v1/documents/{database}/{collection}/{id}` | remove the document |

When a token is configured, every route except `/health` requires
`Authorization: Bearer <token>`.

## How it maps to the app's four operations

| App operation | This store |
| --- | --- |
| ping / connection test | `GET /health` |
| load custom library | `GET .../{id}` → `404` means "seed from the bundled library and save it" |
| save custom library | `PUT .../{id}` with the full document |
| sync JSON seed | `PUT .../{id}` with the bundled seed |

The document body is whatever the app already writes for MongoDB, e.g.:

```json
{
  "_id": "customPayloads",
  "schema": "payloadLibrary/1",
  "version": 3,
  "updatedAtMs": 1790000000000,
  "library": { "version": 3, "collections": [ /* ... */ ] }
}
```

The store round-trips the JSON object unchanged. It never inspects or rewrites
the payload bytes, so payload text (including strings like `;sleep 5` or markup)
is preserved exactly.

## Storage & safety

- One SQLite file, one row per `(database, collection, id)`, WAL mode.
- Binds loopback only unless `--allow-external` is passed.
- Optional bearer token for defense in depth on a shared host.

## Layout

```
payload_store/        the package
  __init__.py         public API re-exports (DocumentStore, make_server, ...)
  __main__.py         `python -m payload_store` entry point
  config.py           constants and defaults
  _util.py            pure helpers (time, path-segment guard, loopback check)
  store.py            DocumentStore — the SQLite layer
  server.py           HTTP handler + server factory
  cli.py              argument parsing and the process entry point
tests/                unittest suite exercising the HTTP contract
pyproject.toml        packaging metadata, console script, ruff/mypy config
Makefile              common tasks (test, run, lint, ...)
```

## Development

```bash
make test             # python3 -m unittest discover -s tests -v
make run              # start the server locally
make install          # pip install -e ".[dev]"  (adds ruff + mypy)
make lint             # ruff check .
make typecheck        # mypy payload_store
```

The tests run a real server on an ephemeral loopback port and cover the HTTP
contract: health, seed-on-miss (404), upsert, read-back, delete, path-segment
rejection, and bearer-token enforcement.

## Integrating with the app

The app currently has a single MongoDB backend in
`src/app/commands/payloads.rs`. Pointing it here means adding an HTTP storage
backend whose base URL is a loopback address, sent through the app's shared
egress sender. That change is tracked separately from this server.
