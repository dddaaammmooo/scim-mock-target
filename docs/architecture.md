# Architecture & development

## Stack

- **Python 3.12**, **FastAPI** and **uvicorn**
- **SQLite** through the standard-library `sqlite3` module, with no ORM
- A vanilla JavaScript single-page UI with no build step and no CDN assets
- One container with a named volume for the database

## Code layout

```
app/
  main.py      app factory; middleware for auth, fault injection and request logging; static UI
  routes.py    SCIM endpoints, mounted under both /t/{tenant}/scim/v2 and /scim/v2/enterprises/{tenant}
  scim.py      validation, rendering (meta, ETag, groups back-references), CRUD, discovery documents
  filters.py   SCIM filter tokenizer, parser and evaluator (eq ne co sw ew gt ge lt le pr, and/or/not, value paths)
  patch.py     PatchOp engine (simple, sub-attribute, value-filter and URN paths; members add/remove/replace)
  store.py     SQLite store: resources, request log (capped at 5000), tenants, fault settings
  admin.py     /api endpoints for the UI and automated tests
  errors.py    SCIM error type and response helper
  static/      index.html, app.css, app.js
tests/
  conftest.py       TestClient with a temporary DATA_DIR
  test_scim.py      end-to-end behaviour: auth, CRUD, filters, uniqueness, PUT/PATCH, groups, log, faults, summary
  test_filters.py   filter tokenizer / parser / evaluator, every operator and error
  test_patch.py     PatchOp engine: every path form, operation and error class
  test_api.py       routing, middleware, fault precedence, log truncation, admin API, store
  ui/               Playwright browser tests against a real uvicorn server (marker: ui)
scripts/
  screenshots.py    regenerates docs/images from a running instance
```

## Request pipeline

```
request ─▶ is it a SCIM path? ── no ──▶ admin API / UI
                │ yes
                ▼
          bearer token check ── fail ──▶ 401 (logged; tenant not created)
                ▼
          fault injection: latency → 429 every Nth → random 500 → 503 if read-only
                ▼
          route handler (scim.py + filters.py / patch.py) ─▶ store.py (SQLite)
                ▼
          request log entry (bodies truncated: request 16 KB, response 4 KB)
```

## Storage

Each resource is stored as a JSON document keyed by `(tenant, type, id)`. Indexed columns for
`userName`, `externalId` and `displayName` support the uniqueness checks, which are
case-insensitive where SCIM requires. `meta.version` increments on every write. A lock
serialises writes, because FastAPI runs synchronous handlers in a thread pool.

## Running locally

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt          # bin/ on macOS/Linux
DATA_DIR=./data .venv/Scripts/python -m uvicorn app.main:app --port 8100 --reload
```

## Tests

```bash
.venv/Scripts/python -m pytest                       # backend: 100% line + branch coverage enforced
.venv/Scripts/python -m playwright install chromium  # once, for the UI tests
.venv/Scripts/python -m pytest -m ui --no-cov        # browser tests
PLAYWRIGHT_CHANNEL=msedge .venv/Scripts/python -m pytest -m ui --no-cov   # use installed Edge/Chrome instead
```

`pytest.ini` runs the backend tests by default (`-m "not ui"`) with `--cov-branch` and
`fail_under = 100` from `.coveragerc`. The UI tests start the real app with uvicorn on a free
port with a temporary `DATA_DIR`, seed a tenant through the SCIM API, and fail on any JavaScript
error. CI (`.github/workflows/tests.yml`) runs both, plus a Docker build and health check.

## Known limitations

- `If-Match` / ETag preconditions aren't enforced. ETags are returned for information only.
- There is no `/Bulk` endpoint, no sorting, and no `/Me`.
- A single token is shared by all tenants.
- The admin API is unauthenticated. This is deliberate, because it's a local test tool.
