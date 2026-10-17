# Ishaara

Backend that turns a YouTube lecture into spoken board descriptions. See `docs/ishaara_backend_implementation_plan.md`.

## Setup

```bash
uv sync
cp .env.example .env   # fill in secrets
```

## Run

```bash
uv run uvicorn ishaara.main:create_app --factory --reload
curl localhost:8000/healthz
```

## Storage backends

`DATABASE_BACKEND` selects where videos, history and quotas are stored:

- `memory` (default): nothing persisted; fine for local work and tests.
- `firestore`: needs `GCP_PROJECT_ID` and Application Default Credentials.
- `supabase`: needs `SUPABASE_DB_URL` (use the **session pooler** string, which works over
  IPv4) and the tables from `supabase/schema.sql`, run once in the Supabase SQL editor.

Production refuses `memory`.

## Test

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run pytest -q
```

Repository contract tests run against every backend you configure and skip the rest:

```bash
uv run pytest -q                                   # memory always; others skipped
SUPABASE_TEST_DB_URL=... uv run pytest -q -k supabase   # throwaway schema, dropped afterwards
FIRESTORE_EMULATOR_HOST=localhost:8080 uv run pytest -q -k firestore
```
