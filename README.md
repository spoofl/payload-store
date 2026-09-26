# payload-store

A tiny, dependency-free JSON document store served over loopback HTTP — a
stdlib-only replacement for the payload-library feature's MongoDB backend
(MongoDB Atlas is refused by the app's egress guard; loopback has no external
destination to vet).

## Requirements

Python 3.11+ standard library. Nothing to install to run it.

## Run

```bash
python3 -m payload_store                              # 127.0.0.1:8787, ./payload_store.db
python3 -m payload_store --port 8790 --db /var/lib/payloads.db
PAYLOAD_STORE_TOKEN=s3cret python3 -m payload_store   # require a bearer token
```

`pip install .` also provides a `payload-store` console command.

Flags: `--host` (`127.0.0.1`), `--port` (`8787`), `--db` (`./payload_store.db`),
`--token` (or `PAYLOAD_STORE_TOKEN`), `--allow-external` (a non-loopback bind is
refused without it).

## HTTP contract

| Method | Path | Meaning |
| --- | --- | --- |
| `GET` | `/health` | liveness; no auth |
| `GET` | `/v1/documents/{database}/{collection}/{id}` | return the stored JSON object, or `404` if absent |
| `PUT` | `/v1/documents/{database}/{collection}/{id}` | replace-with-upsert; body must be a JSON object |
| `DELETE` | `/v1/documents/{database}/{collection}/{id}` | remove the document |

With a token configured, every route except `/health` requires
`Authorization: Bearer <token>`. Document bodies are round-tripped unchanged.

## Development

```bash
make test        # run the suite (real server on an ephemeral loopback port)
make run         # start the server locally
make install     # pip install -e ".[dev]"  (ruff + mypy)
make lint        # ruff check .
make typecheck   # mypy payload_store
```
