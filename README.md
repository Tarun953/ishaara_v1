# Ishaara

A backend that makes recorded lectures usable for blind students. Paste a YouTube lecture link and
it finds the moments where something appears on the board or screen **that the teacher did not
explain aloud**, writes a short spoken description of each one, and voices it. A frontend plays the
video, pauses at each moment, plays the description and resumes. Students can also ask questions
about the lecture and hear the answer, and download accessible notes.

How it works, in short: Gemini watches the public YouTube video directly (nothing is downloaded),
lists every visual event and the transcript, a second Gemini call judges which events the speech
does not already cover, and those become cues. Details and the reasoning behind every decision are in
[`docs/DECISIONS.md`](docs/DECISIONS.md); the original plan is
[`docs/ishaara_backend_implementation_plan.md`](docs/ishaara_backend_implementation_plan.md).

## Documentation

| | |
| --- | --- |
| [`docs/API.md`](docs/API.md) | Every endpoint, its use cases, request and response examples, error codes |
| [`docs/postman/`](docs/postman/README.md) | A Postman collection and environment |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | Deploying to Cloud Run, pre-processing demo videos, operating the service |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Deviations from the plan, findings, and known open problems |

Interactive API docs are served at `/docs` while the app runs.

## Quick start

Requirements: Python 3.12 and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env     # shared keys: fill in GEMINI_API_KEY and YOUTUBE_API_KEY
AUTH_BYPASS_UID=dev-user uv run uvicorn ishaara.main:create_app --factory --reload
curl localhost:8000/healthz
```

With just that, nothing needs a cloud account: videos are kept in memory and audio is written to
`.local_storage/` and served at `/media`. `AUTH_BYPASS_UID` makes every request act as that user with
no Firebase token. It works only with `ENVIRONMENT=local` and is refused in production.

To develop against real services, add `.env.local` (see below).

## Configuration

Settings are layered, later sources winning: the shared **`.env`**, then **`.env.<name>`** for the
environment chosen by `ISHAARA_ENV` (default `local`), then real environment variables.

| File | Holds | Used when |
| --- | --- | --- |
| `.env` | Keys, model names, limits shared by every environment | Always |
| `.env.local` | Local development: Supabase database and storage, `AUTH_BYPASS_UID` | `ISHAARA_ENV` unset or `local` |
| `.env.prod` | Production: Firestore, Cloud Storage, buckets, CORS origins. **No secrets** | `ISHAARA_ENV=prod` (the `process` command, `scripts/deploy.sh`) |

Copy `.env.local.example` and `.env.prod.example` to start; `.env.example` lists every setting with its
default. All `.env*` files except the `*.example` templates are git-ignored and excluded from the Docker
image. A missing file is skipped, so a single `.env` still works. The deployed service never reads
these files: `scripts/deploy.sh` passes plain settings as Cloud Run variables and secrets by name from
Secret Manager. On start the app logs which environment, database and storage it is using.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ENVIRONMENT` | `local` | `local` or `production`; production refuses the in-memory database, local file storage and `AUTH_BYPASS_UID` |
| `GEMINI_API_KEY` | | Gemini API key (secret) |
| `YOUTUBE_API_KEY` | | YouTube Data API v3 key (secret) |
| `GCP_PROJECT_ID` | | Google Cloud / Firebase project; used to verify Firebase ID tokens |
| `AUDIO_BUCKET` | | Public bucket for narration and answer audio (GCS or Supabase) |
| `TRANSCRIPT_BUCKET` | | Private bucket for transcript JSON |
| `ANALYSIS_MODEL` | `gemini-3.8-flash` | Video analysis, gap judging and visual question answering |
| `TEXT_MODEL` | `gemini-3.5-flash-lite` | Narration writing and text question answering |
| `TTS_MODEL` | `gemini-3.8-flash-lite-tts` | Speech |
| `TTS_VOICE` | `Kore` | Speech voice |
| `PIPELINE_VERSION` | `2` | Part of the cache key; bump it whenever prompts change so old results are not reused |
| `MAX_VIDEO_MINUTES` | `45` | Longest accepted video |
| `CLIP_WINDOW_SECONDS` | `600` | Length of each analysis window |
| `CLIP_OVERLAP_SECONDS` | `10` | Overlap between windows |
| `ANALYSIS_CONCURRENCY` | `3` | Windows analysed in parallel per video |
| `TTS_CONCURRENCY` | `4` | Speech calls in parallel per video |
| `MIN_CONFIDENCE` | `0.5` | Events below this confidence are dropped |
| `MIN_CUE_GAP_SECONDS` | `6` | Closer events merge into one cue |
| `DAILY_VIDEO_LIMIT` | `10` | New videos per user per day (UTC) |
| `DAILY_QUESTION_LIMIT` | `50` | Questions per user per day (UTC) |
| `MAX_REQUEST_BODY_BYTES` | `16384` | Larger request bodies are rejected with 413 |
| `ALLOWED_ORIGINS` | `http://localhost:5173` | Comma-separated CORS origins |
| `AUTH_BYPASS_UID` | | Local development only: act as this user without a token |
| `DATABASE_BACKEND` | `memory` | `memory`, `firestore` or `supabase` |
| `SUPABASE_DB_URL` | | Supabase **session-pooler** connection string (secret) |
| `STORAGE_BACKEND` | `local` | `local`, `gcs` or `supabase` |
| `LOCAL_STORAGE_DIR` | `.local_storage` | Where `local` storage writes files |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Origin used to build `local` audio URLs |
| `SUPABASE_URL` | | Supabase project URL (for Supabase Storage) |
| `SUPABASE_SERVICE_KEY` | | Supabase **secret** key, never the publishable one (secret) |
| `LOG_LEVEL` | `INFO` | Log level |

`SUPABASE_TEST_DB_URL` is read only by the test suite.

### Storage backends

- **Database** (`DATABASE_BACKEND`): `memory` keeps nothing and is for local work. `supabase` uses
  Postgres through the session pooler and needs the tables in `supabase/schema.sql`. `firestore` needs
  Application Default Credentials.
- **Audio and transcripts** (`STORAGE_BACKEND`): `local` writes files and serves them at `/media`.
  `supabase` and `gcs` need a public audio bucket and a private transcript bucket.

Every adapter has now been run against real services: Supabase (database and storage), Firestore, and
Google Cloud Storage.

## Using the API

Every `/v1` route needs `Authorization: Bearer <Firebase ID token>` (or the local bypass above).
The full reference is [`docs/API.md`](docs/API.md); the whole flow looks like this:

```bash
BASE=http://localhost:8000
AUTH="Authorization: Bearer $FIREBASE_ID_TOKEN"     # omit when using AUTH_BYPASS_UID

# 1. submit a lecture (202 = processing, 200 = already ready)
curl -X POST $BASE/v1/videos -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"youtube_url": "https://youtu.be/v8kBd64_WAc", "narration_language": "en"}'

# 2. poll every ~3 seconds until status is "ready" or "failed"
curl $BASE/v1/videos/v8kBd64_WAc_en_v2 -H "$AUTH"

# 3. read the result
curl $BASE/v1/videos/v8kBd64_WAc_en_v2/cues -H "$AUTH"                  # cues (JSON)
curl $BASE/v1/videos/v8kBd64_WAc_en_v2/cues.vtt -H "$AUTH"              # WebVTT description track
curl "$BASE/v1/videos/v8kBd64_WAc_en_v2/notes?format=html" -H "$AUTH"   # accessible notes

# 4. ask a question (optionally "at_seconds" for a moment in the video)
curl -X POST $BASE/v1/videos/v8kBd64_WAc_en_v2/questions -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"question": "What is drawn on the board right now?", "at_seconds": 25}'

# your history
curl $BASE/v1/me/videos -H "$AUTH"
```

## Command line

```bash
uv run ishaara analyze <youtube_url> --language en --max-minutes 3 --duration-seconds 180
ISHAARA_ENV=prod uv run ishaara process <youtube_url> [<youtube_url> ...] --languages en hi
uv run ishaara deploy-env        # prints what scripts/deploy.sh will send to Cloud Run (no secret values)
scripts/deploy.sh                # deploy to Cloud Run (DRY_RUN=1 prints the command instead)
```

- `analyze` prints the merged cues as JSON, for prompt tuning. It needs no database. `--save-fixtures`
  writes the raw Gemini responses to `tests/fixtures/`, and `--from-fixtures` reuses saved pass 1
  output and re-runs only the cheap gap-judging step.
- `process` fully processes videos into the database and storage of the chosen environment, so a demo
  never waits. Use `ISHAARA_ENV=prod` to fill the production backends. See [`docs/DEPLOY.md`](docs/DEPLOY.md).

## Development

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run pytest -q
```

The test suite uses fakes for Gemini, YouTube and storage and makes no network calls, except the
repository contract tests, which run against every backend you configure and skip the rest:

```bash
uv run pytest -q -k supabase     # needs SUPABASE_TEST_DB_URL in .env.local; uses a throwaway schema
FIRESTORE_EMULATOR_HOST=localhost:8080 uv run pytest -q -k firestore       # an emulator
FIRESTORE_TEST_PROJECT=<project-id> uv run pytest -q -k firestore           # a real project: throwaway
                                                                           # contract_test_* collections, deleted after
```

### Docker

```bash
docker build -t ishaara-api .
docker run --rm -p 8080:8080 ishaara-api                       # local mode, in-memory
docker run --rm -e ENVIRONMENT=production ishaara-api          # exits: production refuses unsafe defaults
scripts/smoke_test.sh http://localhost:8080
```

(`.env*` files are not copied into the image; settings reach the container as environment variables.)

### Layout

```text
src/ishaara/
  main.py, settings.py, errors.py, middleware.py, container.py   app factory, config, wiring
  api/            routes: health, videos (+ cues, vtt, notes), questions; auth dependencies
  models/         domain models, API bodies, Gemini response schemas
  pipeline/       pure logic (URL parsing, windows, merging, prompts) and the processing pipeline
  clients/        Gemini, YouTube, database and storage adapters, each behind a small port
  services/       video submission, background jobs, questions, notes
  renderers/      WebVTT and notes (Markdown / HTML), pure functions
supabase/schema.sql   database schema for the Supabase backend
scripts/smoke_test.sh deploy smoke test
tests/                unit, API and repository contract tests
```

## Known limitations

- Gemini's speech service can stop responding (rate limits); videos then finish with text-only cues
  and questions return text without audio. See `docs/DECISIONS.md`.
- Which events become cues varies between runs of the same video.
- The Cloud Run deploy has been prepared (project, service account, buckets, secrets) but not yet run.
