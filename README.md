# payload-store

A tiny, dependency-free JSON document store for payload libraries and shared
workflow definitions, served over loopback HTTP and persisted in SQLite.

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

`GET` and `HEAD` include a strong `ETag`. To create only when absent, send
`If-None-Match: *` on `PUT`. To replace or delete the version you read, send its
ETag in `If-Match`. The check and mutation run in one SQLite transaction; a
stale precondition returns `412` without changing the document. Unconditional
writes remain supported for existing payload clients. Only a single store ETag
or `*` is accepted for `If-Match`, and only `*` for `If-None-Match`; unsupported
or combined preconditions return `400`. `/health` advertises
`"capabilities": ["conditionalWrites"]`.

## Shared workflows

The app uses the same server, bearer token, and database for both libraries.
Payloads default to `payload-library/payloads/customPayloads`; workflows use
`payload-library/workflows/definitions` (or the configured database). Workflow
editing, duplication, deletion, and new runs read the shared definitions. Run
history, schedules, investigation data, credentials, and execution state stay
in the app's local workspace databases.

The workflow document is a JSON object:

```json
{
  "schema": "workflowLibrary/1",
  "version": 1,
  "definitions": [
    {
      "id": "evidence-review",
      "revision": 1,
      "name": "Evidence review",
      "description": "Review existing evidence and write a report.",
      "steps": [
        {"id": "review", "title": "Review", "instructions": "Review the saved evidence."}
      ]
    }
  ],
  "imports": []
}
```

The app validates the workflow schema and uses conditional writes to preserve
concurrent edits. On first access it migrates existing local definitions,
preserving conflicting local versions as separate imported workflows. Migration
receipts in `imports` prevent repeated imports; preserve them when editing the
document directly. An empty `definitions` array stays empty. The original local
definitions remain as a backup. Clients refuse older servers without conditional
write support instead of risking a lost update. Restart the server after updating
its code; existing databases need no migration.

## Development

```bash
make test        # run the suite (real server on an ephemeral loopback port)
make run         # start the server locally
make install     # pip install -e ".[dev]"  (ruff + mypy)
make lint        # ruff check .
make typecheck   # mypy payload_store
```
