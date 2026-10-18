# Ishaara

A backend that makes recorded lectures usable for blind students. Paste a YouTube lecture link and
it finds the moments where something appears on the board or screen **that the teacher did not
explain aloud**, writes a short spoken description of each one, and voices it. A frontend plays the
video, pauses at each moment, plays the description and resumes. Students can also ask questions
about the lecture and hear the answer, and download accessible notes.

Frontend Repo: https://github.com/cyberlord-007/ishaara_ui_v1

## Status

| | |
| --- | --- |
| Deployed | Cloud Run, `asia-south1`: `https://ishaara-api-183744304168.asia-south1.run.app` (Firestore + Cloud Storage). Interactive docs at `/docs`. |
| Built | Everything in the plan: URL and token validation, video analysis, jobs and caching, narration and speech, cues / WebVTT / notes, questions, hardening, demo pre-processing, deploy tooling. |
| Verified for real | Every database and storage adapter (Supabase, Firestore, Cloud Storage), the Docker image, graceful shutdown, the public checks on the live service, and the Postman collection through Newman on a local server. |
| Tested on the live service | Two real lectures end to end with a temporary Firebase user (since deleted): submit, poll, cues, WebVTT, notes, text and board questions, cache hits, history. Both videos are still stored. |
| Not verified yet | Whether the 0-cue result for "Time and Work" matches the video, speech reliability beyond the free-tier limit, and the Postman collection inside the Postman app (see [Known limitations](#known-limitations-and-roadmap)). |
| Not built | The OpenRouter text backend, and request-level observability (milestone M8). |

## How it works

Gemini watches the public YouTube video directly, so nothing is downloaded. Two Gemini passes turn
a video into cues; everything else is bookkeeping around them.

```text
POST /v1/videos ──► validate link + YouTube metadata (public, embeddable, not live, <= 45 min)
                    │            cache hit for this video + language? ──► 200 ready, instantly
                    ▼
        background job (in-process, with a lease heartbeat so it survives restarts)
  1  Resolve    1%   re-check the video
  2  Analyze  5-70%  10-minute windows with 10 s overlap, 3 in parallel:
                       pass 1 (video)  transcript + every visual event, each with the words spoken nearby
                       pass 2 (text)   judge each event: fully / partly / not explained by the speech
  3  Merge      75%  absolute timestamps, drop overlap duplicates, keep the unexplained events
                     above a confidence floor, merge events closer than 6 s into one cue
  4  Write   75-80%  one call writes a spoken line per cue (at most ~30 words, English or Hindi)
  5  Voice   80-98%  text-to-speech per cue (4 in parallel, retried); a cue that keeps failing stays
                     silent and the job still succeeds
  6  Save       99%  transcript to private storage
                     ──► ready (100%): cues, audio, transcript stored under one key
```

- A **cue** is a moment where the player should pause: when, what is on screen, what a listener
  would miss, the spoken line, and the audio URL. Content the teacher already says aloud produces no cue.
- Results are cached under `{youtube_id}_{language}_v{PIPELINE_VERSION}` and shared by every user, so a
  popular lecture is analysed once. A ready result costs nothing and uses no daily quota.
- **Questions** use the transcript and cues within two minutes of the student's position, and look at
  the 30-second clip around that moment only when the text is not enough. The answer is spoken in the
  language of the question.
- If the instance stops mid-job, the job's lease expires and the next request restarts it.

The reasoning behind every decision, and every deviation from the original plan
([`docs/ishaara_backend_implementation_plan.md`](docs/ishaara_backend_implementation_plan.md)), is in
[`docs/DECISIONS.md`](docs/DECISIONS.md).

## Endpoints

All `/v1` routes need `Authorization: Bearer <Firebase ID token>`. Full reference with examples and
error codes: [`docs/API.md`](docs/API.md).

| Route | Purpose |
| --- | --- |
| `GET /health` (also `/healthz`) | Liveness. Use `/health` on Cloud Run, which reserves `/healthz`. |
| `POST /v1/videos` | Submit a lecture (`202` processing, `200` already ready) |
| `GET /v1/videos/{video_key}` | Status, stage and progress, for polling |
| `GET /v1/videos/{video_key}/cues` | The cues, for the player |
| `GET /v1/videos/{video_key}/cues.vtt` | WebVTT description track |
| `GET /v1/videos/{video_key}/notes?format=markdown\|html` | Accessible notes |
| `POST /v1/videos/{video_key}/questions` | Ask a question, get a written and spoken answer |
| `GET /v1/me/videos` | The user's submission history |

Errors are always RFC 9457 `application/problem+json`.

## Documentation

| | |
| --- | --- |
| [`docs/API.md`](docs/API.md) | Every endpoint, its use cases, request and response examples, error codes |
| [`docs/postman/`](docs/postman/README.md) | Postman collection (25 requests, tests, examples, automatic polling) and local / Cloud Run environments |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | Deploying to Cloud Run, pre-processing demo videos, operating the service, troubleshooting |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Deviations from the plan, findings, and open problems |

## Tech stack

| | |
| --- | --- |
| Language, packaging | Python 3.12, [`uv`](https://docs.astral.sh/uv/) with a committed lock file |
| Web | FastAPI, Uvicorn, Pydantic v2, `pydantic-settings` |
| AI | Google Gemini through `google-genai` (video analysis, narration, speech, questions) |
| Identity | Firebase Auth ID tokens, verified with `firebase-admin` |
| Data | Firestore or Supabase Postgres (`psycopg`), behind one port; in-memory for tests |
| Files | Google Cloud Storage or Supabase Storage, or local disk for development |
| Video metadata | YouTube Data API v3 over `httpx` |
| Reliability | `tenacity` retries, hard deadlines on Gemini calls, `structlog` JSON logs |
| Rendering | `markdown-it-py` for the HTML notes |
| Quality | `ruff`, `mypy --strict`, `pytest` (+ `pytest-asyncio`, `respx`) |
| Deploy | Docker, Cloud Build, Cloud Run, Secret Manager |

## Quick start

You need Python 3.12 and `uv`. To run the real pipeline you also need:

| What | For | Where to get it |
| --- | --- | --- |
| Gemini API key | analysis, narration, speech, questions | Google AI Studio, or an API key restricted to the Gemini API in a billed Google Cloud project |
| YouTube Data API v3 key | checking that a video is public, embeddable and short enough | Google Cloud console: enable the API, create a key, restrict it to that API |
| A Firebase project | verifying ID tokens in production | Firebase console; its project id is `GCP_PROJECT_ID` |

```bash
uv sync
cp .env.example .env     # shared keys: fill in GEMINI_API_KEY and YOUTUBE_API_KEY
AUTH_BYPASS_UID=dev-user uv run uvicorn ishaara.main:create_app --factory --reload
curl localhost:8000/health
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
| `TTS_REQUESTS_PER_MINUTE` | `0` | Cap on speech requests per minute for the whole process; 0 = unlimited. Gemini's free tier allows 3, so production sets 3 |
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
  Postgres through the session pooler (the direct host is IPv6-only) and needs the tables in
  `supabase/schema.sql`. `firestore` needs Application Default Credentials.
- **Audio and transcripts** (`STORAGE_BACKEND`): `local` writes files and serves them at `/media`.
  `supabase` and `gcs` need a public audio bucket and a private transcript bucket.

Every adapter has been run against real services: Supabase (database and storage), Firestore, and
Google Cloud Storage.

## Using the API

Every `/v1` route needs `Authorization: Bearer <Firebase ID token>` (or the local bypass above). The
whole flow looks like this:

```bash
BASE=http://localhost:8000      # or https://ishaara-api-183744304168.asia-south1.run.app
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

A Firebase ID token comes from the frontend (`await firebase.auth().currentUser.getIdToken()`) and
expires after an hour.

### Frontend integration

1. Sign the user in with Firebase Auth; send the ID token as a bearer token.
2. `POST /v1/videos`; on `202` poll the status every 3 seconds and show `stage` and `progress_percent`.
3. When `ready`, load the cues and play the YouTube video with the IFrame player. At each
   `cue.start_seconds`: `player.pauseVideo()`, play `cue.audio_url`, and on the audio's `ended` event
   `player.playVideo()`. If a cue has no `audio_url`, show or read `narration_text` instead.
4. Offer the notes (`/notes?format=html`) as a reading alternative.
5. The WebVTT track needs the token, and a `<track src>` cannot send one: fetch it with the token and give
   the `<track>` a blob URL (snippet in [`docs/API.md`](docs/API.md)).

## Command line

```bash
uv run ishaara analyze <youtube_url> --language en --max-minutes 3 --duration-seconds 180
ISHAARA_ENV=prod uv run ishaara process <youtube_url> [<youtube_url> ...] --languages en hi
uv run ishaara deploy-env        # prints what scripts/deploy.sh will send to Cloud Run (no secret values)
scripts/deploy.sh                # deploy to Cloud Run (DRY_RUN=1 prints the command instead)
scripts/smoke_test.sh <base-url> [firebase-id-token]
```

- `analyze` prints the merged cues as JSON, for prompt tuning. It needs no database. `--save-fixtures`
  writes the raw Gemini responses to `tests/fixtures/`, and `--from-fixtures` reuses saved pass 1
  output and re-runs only the cheap gap-judging step.
- `process` fully processes videos into the database and storage of the chosen environment, so a demo
  never waits. Use `ISHAARA_ENV=prod` to fill the production backends. It reports `ready`, `cached` or
  `failed` per video and language and exits non-zero on any failure.
- `smoke_test.sh` checks `/health`, that `/v1` demands a token, that oversized bodies are rejected, and,
  with a token, that a submission is accepted.

## Deployment

The service runs on Cloud Run with CPU always allocated (background jobs need it) and at least one
instance. [`docs/DEPLOY.md`](docs/DEPLOY.md) has the one-time setup (APIs, service account, buckets,
secrets), the deploy, pre-processing demo videos, operating and rolling back, and a troubleshooting
table.

```bash
DRY_RUN=1 scripts/deploy.sh      # show the exact gcloud command; changes nothing
scripts/deploy.sh                # build in Cloud Build and deploy; settings come from .env + .env.prod
```

The app refuses to start in production with the in-memory database, local file storage or an auth
bypass, so a mistaken configuration fails the deploy instead of silently losing data.

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

Guard tests keep the documentation honest: they fail if a route is missing from `docs/API.md` or the
Postman collection, if a setting is missing from `.env.example` or this README, or if an env file stops
being git-ignored.

### Docker

```bash
docker build -t ishaara-api .
docker run --rm -p 8080:8080 ishaara-api                       # local mode, in-memory
docker run --rm -e ENVIRONMENT=production ishaara-api          # exits: production refuses unsafe defaults
scripts/smoke_test.sh http://localhost:8080
```

(`.env*` files are not copied into the image; settings reach the container as environment variables.)

### Conventions

- One commit per milestone, `feat(mN): <summary>`; other changes use `fix:`, `docs:` or `test:`.
- Code is fully typed (`mypy --strict`), formatted with `ruff`, and uses Google-style docstrings. Pure
  logic (parsing, merging, rendering) lives in plain functions with no I/O; every external system sits
  behind a small port with an in-memory or fake implementation for tests.
- Changing a route: update `docs/API.md` and the Postman collection. Changing a setting: update
  `.env.example` and the table above. Departing from the plan or finding something surprising: add a
  line to `docs/DECISIONS.md`.
- Changing any prompt: bump `PIPELINE_VERSION` so old cached results are not reused.

### Layout

```text
src/ishaara/
  main.py, settings.py, errors.py, middleware.py, container.py   app factory, config, wiring
  cli.py, preprocess.py, deploy_config.py                        command line, demo prep, deploy arguments
  api/            routes: health, videos (+ cues, vtt, notes), questions; auth dependencies
  models/         domain models, API bodies, Gemini response schemas
  pipeline/       pure logic (URL parsing, windows, merging, prompts) and the processing pipeline
  clients/        Gemini, YouTube, database and storage adapters, each behind a small port
  services/       video submission, background jobs, questions, notes
  renderers/      WebVTT and notes (Markdown / HTML), pure functions
supabase/schema.sql   database schema for the Supabase backend
scripts/              deploy.sh, smoke_test.sh
docs/                 API.md, DEPLOY.md, DECISIONS.md, postman/, the original plan
tests/                unit, API and repository contract tests; fixtures (real Gemini responses, golden files)
.env.example, .env.local.example, .env.prod.example          settings reference and per-environment templates
Dockerfile
```

## Security and privacy

- Secrets live only in git-ignored `.env*` files locally and in Secret Manager in production; the deploy
  tooling never prints a secret value and the Cloud Run service receives secrets by name.
- Every `/v1` route requires a verified Firebase ID token. The local bypass is refused in production.
- Request bodies are limited to 16 KB, URLs and keys are validated, `/v1` responses are `no-store`, and
  all text from YouTube and Gemini is escaped before it reaches the notes.
- **Narration and answer audio is publicly readable** (a browser `<audio>` element cannot send a
  token), under unguessable or video-specific paths. The audio describes public YouTube videos.
  Transcripts are private.
- Rotate any key that has been shared or pasted anywhere; production reads keys from Secret Manager, so
  rotating is a new secret version and a redeploy.

## Costs and limits

- Measured on a 9-minute lecture: about 55,000 input and 14,000 output Gemini tokens in total, and
  roughly two to four minutes from submission to `ready`. A ready video is free to re-request.
- Per user per day: 10 new videos and 50 questions. Longest video: 45 minutes. Gemini's free tier allows
  about 8 hours of YouTube video per day across all users.
- The always-on Cloud Run instance bills continuously once deployed; budget alerts only email you.
  `docs/DEPLOY.md` shows how to scale to zero or delete the service between demos.

## Known limitations and roadmap

- **Speech is rate-limited on the free tier.** The text-to-speech model allows **3 requests per minute**
  on a free-tier key (Gemini says so in its 429 response), so a video with many cues takes minutes to
  voice. The app throttles itself with `TTS_REQUESTS_PER_MINUTE` and honours Gemini's "retry in N
  seconds"; a cue that still fails stays silent (text-only), and a question whose audio is not ready
  within 25 seconds returns the written answer with `answer_audio_url: null`. A billed key has higher
  limits (not measured).
- **Cues vary between runs** of the same video: the same lecture produced 7 cues once and 5 another time,
  because Gemini's description of a slide can differ. In a small experiment pass 2 (judging) was stable and pass 1 (what Gemini sees and writes down) was the variable part; more runs are needed to be sure.
- **Board notes can appear up to about 30 seconds late** in the notes' reading order.
- **Visual questions are slow** (about a minute), because Gemini analyses a video clip.
- **A text question can come back without audio** when speech is still throttled (the 25-second wait
  expired in the live test). The written answer is always returned.
- **Gemini API billing is separate from Google Cloud billing.** An AI Studio key uses prepaid credits;
  when they run out Gemini answers 402 "prepayment credits are depleted" even if the Cloud project is billed.
- **Not yet done:** the Postman collection has not been run inside the Postman app, and a 0-cue result
  (live test: "Time and Work") has not been checked against the video.
- **Not built:** a hosted open-source text backend (OpenRouter) for answers, request-level observability
  (trace ids, per-stage metrics), and moving jobs to Cloud Tasks so the service can scale to zero.

## License

MIT, see [`LICENSE`](LICENSE).
