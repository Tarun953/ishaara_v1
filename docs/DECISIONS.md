# Decisions

Deviations from `ishaara_backend_implementation_plan.md`, one line each.

- M0: The project lives at the repo root instead of an `ishaara-backend/` subfolder.
- M0: Secrets and resource names (`GEMINI_API_KEY`, buckets, etc.) default to empty strings so `/healthz` and tests run without them.
- M0: The Dockerfile also copies `README.md` because `pyproject.toml` declares it as the package readme.
