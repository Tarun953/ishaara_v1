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

## Test

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run pytest -q
```
