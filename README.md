# payload-store

A tiny, dependency-free JSON document store for payload libraries and shared
workflow definitions and skill bundles, served over loopback HTTP and persisted in SQLite.

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

## Shared skills

Skills use the same server, bearer token, and database, in
`payload-library/skills/definitions` (or the configured database). The existing
JSON document API and conditional-write support handle this library; no database
migration or new server endpoint is needed.

```json
{
  "schema": "skillLibrary/1",
  "version": 1,
  "definitions": [
    {
      "id": "0123456789abcdef0123456789abcdef",
      "revision": 1,
      "content": "---\nname: evidence-review\ndescription: Review evidence\n---\nRead references/guide.md.",
      "files": [
        {"path": "references/guide.md", "contentBase64": "UmV2aWV3IHRoZSBldmlkZW5jZS4K", "executable": false}
      ]
    }
  ]
}
```

`content` is the complete UTF-8 `SKILL.md`, including YAML metadata; metadata is
validated by the app rather than duplicated in other fields. Supporting files
use relative paths, base64 content, and an executable flag. The app accepts up to
200 skills, 60,000 characters per Markdown document, and 256 supporting files
with at most 8 MiB of decoded resources per skill. The existing 64 MiB document
limit also applies. Duplicate names, unsafe paths, symlinks, and unsupported
schemas are rejected by the app.

Create and edit skills from the app's Markdown editor. Valid edits autosave after
600 ms of inactivity. Saves use the document ETag and a per-skill validator;
stale edits retain the draft instead of overwriting another client's changes.
Copying a workspace skill folder includes its supporting files; a flat Markdown
file is copied on its own. Workspace overrides and packaged defaults remain
local. Editing a packaged default creates a shared custom copy.

The app caches validated library snapshots for offline use, scoped to the server
and database. Shared edits require a reachable store. Agent runs keep their
fetched revisions and install resources in their selected runner's filesystem;
refreshing or deleting shared definitions does not change active snapshots.
Empty libraries stay empty. Runtime traffic continues through the app's existing
outbound identity guard, including resource inspection before base64 encoding.

## Development

```bash
make test        # run the suite (real server on an ephemeral loopback port)
make run         # start the server locally
make install     # pip install -e ".[dev]"  (ruff + mypy)
make lint        # ruff check .
make typecheck   # mypy payload_store
```
