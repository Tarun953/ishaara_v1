# Ishaara Backend: Implementation Plan

Oct 17, 2026 · @Tarun Mishra

A Python FastAPI service on Cloud Run that turns a YouTube lecture into spoken board descriptions, built in under two days. Firebase Auth on the frontend; Gemini does transcription, pointing detection, board description and narration.

## Rules for the coding agent

Work milestone by milestone, in order. A milestone is done only when every acceptance check in it passes. Do not start the next one with failing checks.

**Working rules**

1. Read this whole document before writing code. Section names below are referenced from milestones.
2. Never invent Gemini parameters. If a call shape in this plan fails against the installed `google-genai`, check the linked Gemini docs, fix it, and note the change in `docs/DECISIONS.md`.
3. Keep all Gemini calls behind `GeminiGateway` (see Repository layout). Business logic never imports `google.genai` directly, so tests can use a fake.
4. Every external call (Gemini, YouTube Data API, Firestore, Cloud Storage) has a timeout and retries only on 429 and 5xx.
5. Secrets come from environment variables only. Never commit `.env`.
6. Commit after each milestone with message `feat(mN): <summary>`.

**Coding standards**

- Python 3.12, fully type-hinted. `mypy --strict` passes on `src/`.
- `ruff check` and `ruff format` pass with the config in M0.
- Google-style docstrings on every public module, class and function. Each docstring says what it does, its arguments, what it returns, and what it raises.
- Names say what things are: `youtube_video_id`, `clip_start_seconds`, `narration_language`. No `data`, `res`, `tmp` or single letters outside comprehensions.
- Functions stay under about 40 lines. Pure logic (parsing, merging, VTT rendering) lives in plain functions with no I/O.
- Pydantic v2 models for every request, response, Gemini schema and Firestore document.
- Async throughout the request path. Blocking SDK calls go through `asyncio.to_thread`.
- Log with `structlog` key-value events, never `print`.

**Definition of done for every milestone**

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run pytest -q
```

## Architecture

&#91;embedded content: Ishaara backend architecture · one service, five managed dependencies\]

The API answers fast and hands the slow work to an in-process job runner; the frontend polls status and then plays audio straight from Cloud Storage. No video ever touches the backend: Gemini reads it from YouTube.

## Tech stack

The key choice: Gemini reads public YouTube videos straight from their URL, so the backend never downloads video. That removes yt-dlp, ffmpeg and the YouTube bot-blocking that hits cloud IPs, which is the biggest risk for a YouTube-oriented backend on Cloud Run.

| Concern | Choice | Why |
| --- | --- | --- |
| Language, packaging | Python 3.12, `uv` (lockfile committed) | Fast installs, reproducible Docker builds |
| Web framework | FastAPI + Uvicorn | Async, Pydantic-native, OpenAPI docs for the frontend team for free |
| Settings | `pydantic-settings` | Typed env vars, one `Settings` object |
| Gemini SDK | `google-genai >= 2.25` (Interactions API) | Required version for the 3.8 TTS models; one client for video, text and speech |
| Video analysis | `gemini-3.8-flash`, static processing with clip offsets | Stable model; clipping gives exact per-chunk timestamps and lets chunks run in parallel |
| Narration writing, notes, Q&A text | `gemini-3.5-flash-lite` | Cheapest stable text model; these are short text-only calls |
| Speech | `gemini-3.8-flash-lite-tts` | Supports Hindi and English, returns WAV directly, same SDK |
| Video metadata | YouTube Data API v3 `videos.list` via `httpx` | 1 quota unit per call; gives duration, privacy, embeddable, live status |
| Auth | `firebase-admin` `verify_id_token` | Frontend already uses Firebase Auth; no extra identity provider |
| Database | Firestore (Native mode) | Free tier, same project as Firebase, no server to run |
| Audio files | Cloud Storage bucket with public read | The browser's audio element can't send auth headers; audio describes public videos |
| Background work | In-process `asyncio` tasks on Cloud Run with instance-based billing | Fastest to build; Cloud Tasks is the documented upgrade path |
| Retries | `tenacity` | Declarative backoff on 429 and 5xx |
| Logging | `structlog` JSON to stdout | Cloud Logging parses it automatically |
| Durations | `isodate` | Parses YouTube's ISO 8601 durations |
| Quality | `ruff`, `mypy`, `pytest`, `pytest-asyncio`, `respx` | Lint, types, tests, HTTP mocking |
| Deploy | Cloud Run, region `asia-south1` (Mumbai) | Closest region to Delhi users; free tier covers a hackathon |

Limits to design around: the YouTube URL input only accepts public videos, and the free tier allows 8 hours of YouTube video per day. Static mode samples 1 frame per second and puts a timestamp on every second, which is precise enough for pointing words.

## Repository layout

A `src/` layout with one package, `ishaara`. Routers stay thin; services hold the logic; clients wrap one external system each.

```text
ishaara-backend/
├── pyproject.toml            # deps, ruff, mypy, pytest config
├── uv.lock
├── Dockerfile
├── .env.example
├── docs/DECISIONS.md         # deviations from this plan, one line each
├── src/ishaara/
│   ├── main.py               # create_app(): routers, CORS, error handlers, lifespan
│   ├── settings.py           # Settings (pydantic-settings), get_settings()
│   ├── logging_config.py     # structlog JSON setup, Cloud Logging severity mapping
│   ├── errors.py             # IshaaraError hierarchy + problem+json handler
│   ├── api/
│   │   ├── dependencies.py   # get_current_user, get_video_service, ...
│   │   ├── health.py         # GET /healthz
│   │   ├── videos.py         # submit, status, cues, cues.vtt, notes, history
│   │   └── questions.py      # POST /videos/{video_key}/questions
│   ├── models/
│   │   ├── api_models.py     # request/response bodies
│   │   ├── domain.py         # VideoRecord, NarrationCue, TranscriptSegment, enums
│   │   └── gemini_schemas.py # ChunkAnalysis, NarrationBatch, QuestionAnswer
│   ├── clients/
│   │   ├── gemini_gateway.py # GeminiGateway protocol + GoogleGeminiGateway
│   │   ├── youtube_metadata.py # YouTubeMetadataClient (Data API v3)
│   │   ├── firestore_repository.py # VideoRepository, UsageRepository
│   │   └── audio_storage.py  # AudioStorage (Cloud Storage upload, public URL)
│   ├── pipeline/
│   │   ├── youtube_url.py    # parse_youtube_video_id() — pure
│   │   ├── chunking.py       # plan_clip_windows() — pure
│   │   ├── timestamps.py     # normalize_clip_timestamps() — pure
│   │   ├── cue_merging.py    # merge_pointing_moments() — pure
│   │   ├── prompts.py        # prompt templates as constants
│   │   └── orchestrator.py   # run_video_pipeline(): stages + progress
│   ├── services/
│   │   ├── video_service.py  # submit, cache lookup, idempotency, quotas
│   │   ├── job_runner.py     # in-process task registry, lease heartbeat
│   │   └── question_service.py
│   ├── renderers/
│   │   ├── webvtt.py         # render_description_track() — pure
│   │   └── notes.py          # render_notes_markdown/html() — pure
│   └── cli.py                # `uv run ishaara analyze <url>` for prompt iteration
└── tests/
    ├── conftest.py           # FakeGeminiGateway, in-memory repositories, test client
    ├── fixtures/             # recorded Gemini JSON responses
    ├── unit/                 # pure functions
    └── api/                  # endpoint tests with fakes
```

## Data models and Firestore schema

One processed video is one document, keyed by `video_key = f"{youtube_video_id}_{narration_language}_v{PIPELINE_VERSION}"`. That key doubles as the job id and the cache key: a second request for the same video and language returns the stored result. Bump `PIPELINE_VERSION` whenever prompts change, so stale results are not reused.

**Firestore collections**

| Path | Holds | Notes |
| --- | --- | --- |
| `videos/{video_key}` | `VideoRecord`: metadata, status, progress, cues | Cues stored inline; \~200 cues is about 100 KB, well under the 1 MiB document limit |
| `users/{uid}/daily_usage/{YYYY-MM-DD}` | `videos_submitted`, `questions_asked` counters | Enforces per-user daily limits with a transaction |
| `users/{uid}/submissions/{video_key}` | `submitted_at`, `title` | Powers the history endpoint |

The full transcript can exceed 1 MiB on long lectures, so it goes to Cloud Storage at `transcripts/{video_key}.json`; the record stores only its path.

**Domain models** (`models/domain.py`)

```python
class VideoStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class PipelineStage(StrEnum):
    RESOLVING = "resolving"  # YouTube metadata + validation
    ANALYZING = "analyzing"  # Gemini video analysis per clip
    WRITING = "writing"  # narration text
    VOICING = "voicing"  # TTS + upload
    SAVING = "saving"


class NarrationLanguage(StrEnum):
    ENGLISH = "en"
    HINDI = "hi"


class ReferentKind(StrEnum):
    EQUATION = "equation"
    DIAGRAM = "diagram"
    GRAPH = "graph"
    CODE = "code"
    TABLE = "table"
    TEXT = "text"
    PHYSICAL_OBJECT = "physical_object"


class TranscriptSegment(BaseModel):
    start_seconds: float
    end_seconds: float
    text: str


class NarrationCue(BaseModel):
    cue_id: str  # "cue-0001", stable sort order
    start_seconds: float  # when the player pauses
    spoken_phrase: str  # what the teacher said, e.g. "this term"
    referent_kind: ReferentKind
    board_description: str  # full description, used in notes
    narration_text: str  # short spoken version, <= 30 words
    audio_url: str | None = None
    audio_duration_seconds: float | None = None
    confidence: float  # 0.0 - 1.0 from analysis


class VideoRecord(BaseModel):
    video_key: str
    youtube_video_id: str
    narration_language: NarrationLanguage
    pipeline_version: int
    title: str
    channel_title: str
    duration_seconds: int
    status: VideoStatus
    stage: PipelineStage | None = None
    progress_percent: int = 0
    error_code: str | None = None
    error_message: str | None = None
    cues: list[NarrationCue] = []
    transcript_blob_path: str | None = None
    lease_expires_at: datetime | None = None  # heartbeat while processing
    model_versions: dict[str, str] = {}
    token_usage: dict[str, int] = {}
    created_by_uid: str
    created_at: datetime
    updated_at: datetime
```

**Gemini response schemas** (`models/gemini_schemas.py`), passed as `Model.model_json_schema()`. Every field carries a `Field(description=...)`, because Gemini reads descriptions as instructions.

```python
class PointingMoment(BaseModel):
    timestamp: str = Field(description="MM:SS where the pointing word is spoken.")
    spoken_phrase: str = Field(description="Exact words, e.g. 'this term', 'yahan'.")
    referent_kind: ReferentKind
    board_description: str = Field(
        description="What the phrase refers to on screen. Math written out in words."
    )
    changed_since_previous: str = Field(
        description="Only what is new or highlighted since the last moment."
    )
    confidence: float = Field(ge=0, le=1)


class ChunkAnalysis(BaseModel):
    transcript_segments: list[TranscriptSegmentRaw]  # timestamp strings, converted later
    pointing_moments: list[PointingMoment]


class NarrationLine(BaseModel):
    cue_id: str
    narration_text: str = Field(
        description="Spoken description, max 30 words, in the target language."
    )


class NarrationBatch(BaseModel):
    lines: list[NarrationLine]


class QuestionAnswer(BaseModel):
    answer_text: str
    referenced_timestamps: list[str]
    needs_visual_check: bool = Field(
        description="True if the context is not enough and the video must be looked at."
    )
```

## API contract

All routes live under `/v1` and require `Authorization: Bearer <Firebase ID token>`, except `/healthz`. The frontend submits a link, polls status every 3 seconds, then fetches cues and drives the YouTube IFrame player itself.

| Method and path | Request | Success response | Errors |
| --- | --- | --- | --- |
| `GET /healthz` | — | `200 {"status": "ok", "version": "..."}` | — |
| `POST /v1/videos` | `{"youtube_url": str, "narration_language": "en" \| "hi"}` | `202` new or in progress, `200` if already ready; body `VideoStatusResponse` | 400 `invalid_youtube_url`, 422 `video_not_public`, `video_not_embeddable`, `video_is_live`, `video_too_long`, 429 `daily_limit_reached` |
| `GET /v1/videos/{video_key}` | — | `200 VideoStatusResponse`: status, stage, progress\_percent, title, duration, error | 404 `video_not_found` |
| `GET /v1/videos/{video_key}/cues` | — | `200 {"cues": [NarrationCue]}` | 409 `video_not_ready` |
| `GET /v1/videos/{video_key}/cues.vtt` | — | `200 text/vtt` description track | 409 `video_not_ready` |
| `GET /v1/videos/{video_key}/notes` | `?format=markdown\|html` | `200` accessible notes | 409 `video_not_ready` |
| `POST /v1/videos/{video_key}/questions` | `{"question": str, "at_seconds": float \| null}` | `200 {"answer_text", "answer_audio_url", "referenced_seconds": [float]}` | 409 `video_not_ready`, 429 `daily_limit_reached` |
| `GET /v1/me/videos` | `?limit=20` | `200 {"videos": [VideoSummary]}`, newest first | — |

**Error body** follows RFC 9457 problem details, so the frontend handles every failure the same way:

```json
{
  "type": "https://ishaara.app/errors/video_too_long",
  "title": "Video is too long",
  "status": 422,
  "detail": "This video is 74 minutes; the limit is 45 minutes.",
  "code": "video_too_long"
}
```

**How the frontend plays cues** (backend contract, frontend implements): at `cue.start_seconds` it calls `player.pauseVideo()`, plays `cue.audio_url`, and on the audio's `ended` event calls `player.playVideo()`. The `.vtt` track is the fallback for players that support description tracks and for screen readers.

**CORS** allows only origins listed in `ALLOWED_ORIGINS` (the Firebase Hosting domain and `http://localhost:5173`).

## Processing pipeline and prompts

`run_video_pipeline(video_key)` runs five stages and writes `stage` and `progress_percent` to Firestore after each step. Any stage failure sets `status=failed` with an `error_code`; nothing half-done is served.

1. **Resolve (0–5%).** Parse the video id, call `videos.list?part=snippet,contentDetails,status`. Reject unless `privacyStatus == "public"`, `embeddable == true`, `liveBroadcastContent == "none"`, and duration ≤ `MAX_VIDEO_MINUTES` (default 45).
2. **Analyze (5–70%).** Split the video into clip windows and call Gemini once per window, in parallel (semaphore of 3).
3. **Merge (70–75%).** Normalize timestamps, drop duplicates from window overlaps, merge moments that are too close together.
4. **Write narration (75–80%).** One text call turns every cue's `board_description` into a short spoken line in the target language.
5. **Voice and save (80–100%).** One TTS call per cue (semaphore of 4), upload WAVs, write the transcript JSON and the final record.

**Accepted YouTube URL forms** (`parse_youtube_video_id`, regex on an 11-character `[A-Za-z0-9_-]` id): `youtube.com/watch?v=`, `m.youtube.com/watch?v=`, `youtu.be/`, `youtube.com/shorts/`, `youtube.com/embed/`, `youtube.com/live/`, and a bare 11-character id. Extra query parameters like `&t=` and `&list=` are ignored. Always rebuild the canonical `https://www.youtube.com/watch?v={id}` before sending to Gemini.

**Clip windows** (`plan_clip_windows`): 600-second windows with 10 seconds of overlap, so a pointing word on a boundary is seen whole. A 45-minute lecture is 5 windows.

**The analysis call** (`GoogleGeminiGateway.analyze_clip`). This follows the documented Interactions API shape; confirm `processing` and `media_resolution` field names against the installed SDK in M2 and record any change in `docs/DECISIONS.md`.

```python
interaction = await asyncio.to_thread(
    self._client.interactions.create,
    model=self._settings.analysis_model,  # "gemini-3.8-flash"
    input=[
        {
            "type": "video",
            "uri": canonical_youtube_url,
            "processing": {
                "type": "static",
                "start_offset": clip_window.start_seconds,
                "end_offset": clip_window.end_seconds,
            },
        },
        {"type": "text", "text": build_analysis_prompt(clip_window)},  # prompt AFTER video
    ],
    response_format={
        "type": "text",
        "mime_type": "application/json",
        "schema": ChunkAnalysis.model_json_schema(),
    },
)
chunk_analysis = ChunkAnalysis.model_validate_json(interaction.output_text)
```

**Analysis prompt** (`prompts.ANALYSIS_PROMPT`):

```text
You are helping blind students follow a recorded lecture.
This clip covers {clip_start_mmss} to {clip_end_mmss} of the full video.
Write every timestamp as absolute video time in MM:SS (or HH:MM:SS).

1. Transcribe the speech as segments of one sentence each. Keep Hindi, English
   and Hinglish exactly as spoken.
2. Find every POINTING MOMENT: the speaker uses a word like "this", "that",
   "here", "these", "ye", "yahan", "isko", "is wale", "dekho" to refer to
   something visible on screen (board, slide, diagram, code, object).
   Ignore the word when it refers to an idea rather than something visible.
3. For each moment, describe what is being referred to so a blind student
   understands it without the video:
   - Write math in words: "x squared plus 2x, all over x minus 1".
   - Name diagram parts and how they connect.
   - Say only what is new or highlighted since the previous moment.
4. Give a confidence from 0 to 1 that this is a real on-screen reference.
Return JSON matching the schema.
```

**Timestamp normalization** (`normalize_clip_timestamps`). Parse `MM:SS` or `HH:MM:SS` to seconds. The prompt asks for absolute time, but models sometimes return time relative to the clip. If every timestamp in a window is below the window length and the window does not start at 0, add `start_seconds` to all of them. Then drop anything outside `[start_seconds, end_seconds]`. Unit-test both cases.

**Merging rules** (`merge_pointing_moments`, pure, unit-tested):

- Drop moments with `confidence < MIN_CONFIDENCE` (default 0.5).
- Sort by time. Two moments within 3 seconds with the same `referent_kind` from overlapping windows are duplicates; keep the higher confidence.
- Moments less than `MIN_CUE_GAP_SECONDS` (default 6) apart merge into one cue, joining their descriptions, so the video is not paused every two seconds.
- Assign `cue_id` values `cue-0001`, `cue-0002` in time order.

**Narration writing** (`gemini-3.5-flash-lite`, one call, `NarrationBatch` schema). Input is the list of `{cue_id, spoken_phrase, board_description}`; instruction: one line per cue, at most 30 words, in `{English | Hindi}`, math spoken naturally, starting with "On the board:" (or "Board par:") only when the referent is on the board. Validate that every input `cue_id` comes back exactly once; retry once if not.

**Speech** (`gemini-3.8-flash-lite-tts`): one call per cue; default unary output is WAV (24 kHz, mono, 16-bit), so bytes are uploaded as-is.

```python
interaction = await asyncio.to_thread(
    self._client.interactions.create,
    model=self._settings.tts_model,  # "gemini-3.8-flash-lite-tts"
    input=[
        {
            "type": "user_input",
            "content": [
                {
                    "type": "text",
                    "text": narration_text,
                    "annotations": [{"type": "speech_metadata", "style": "calm and clear"}],
                }
            ],
        }
    ],
    response_format={"type": "audio"},
    generation_config={"speech_config": [{"voice": self._settings.tts_voice}]},  # "Kore"
)
wav_bytes = base64.b64decode(interaction.output_audio.data)
```

Read the duration with the standard `wave` module and store it on the cue. Upload to `audio/{video_key}/{cue_id}.wav` with `Cache-Control: public, max-age=31536000`.

**Questions** (`QuestionService.answer`):

1. Build text context: transcript segments and cues within ±120 seconds of `at_seconds`, or the whole cue list when no time is given.
2. Ask `gemini-3.5-flash-lite` with the `QuestionAnswer` schema.
3. If `needs_visual_check` is true and `at_seconds` is set, ask `gemini-3.8-flash` again with the YouTube URL clipped to `[at_seconds - 15, at_seconds + 15]`.
4. Voice the answer with the TTS call above and upload it to `answers/{video_key}/{uuid}.wav`.

**Renderers** (pure functions over `VideoRecord`): the WebVTT track has one cue per narration line, from `start_seconds` for `audio_duration_seconds`, with header `WEBVTT` and `Kind: descriptions`. Notes are Markdown (or HTML via `markdown-it-py`) grouped in 5-minute sections with `###` headings, transcript paragraphs, and each cue inlined as `[Board, 12:40] full board_description`.

## Timeline

&#91;embedded content: Build plan · 9 milestones over 2 days, blocks not to scale\]

Day 1 ends with a deployed service that analyzes real videos; Day 2 adds voice, outputs, questions and the final deploy. If M2 overruns, cut M6 to text-only answers before touching anything else.

## Milestones

M0 to M7 make a complete, deployed backend in about 19.5 hours. M8 adds observability only if time remains.

### M0 · Project setup (1.5 h)

- [ ] `uv init --package ishaara-backend --python 3.12`; add runtime deps: `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `google-genai>=2.25`, `firebase-admin`, `google-cloud-storage`, `httpx`, `tenacity`, `structlog`, `isodate`, `markdown-it-py`
- [ ] Dev deps: `uv add --dev ruff mypy pytest pytest-asyncio respx`
- [ ] `pyproject.toml`: ruff line length 100, rules `E,F,I,B,UP,SIM,D` with Google docstring convention; mypy `strict = true`; pytest `asyncio_mode = "auto"`
- [ ] `settings.py`, `logging_config.py`, `errors.py` (base `IshaaraError(code, status, title, detail)` + problem+json handler), `main.py` with `create_app()` and `GET /healthz`
- [ ] `Dockerfile` and `.env.example` (see Configuration and Deployment)

**Acceptance:** `uv run uvicorn ishaara.main:create_app --factory` serves `/healthz`; `docker build .` succeeds; Definition of done commands pass.

### M1 · Auth and YouTube validation (2 h)

- [ ] `api/dependencies.py`: `get_current_user` reads the Bearer token, calls `firebase_admin.auth.verify_id_token` via `asyncio.to_thread`, returns `AuthenticatedUser(uid, email)`; 401 `invalid_token` on failure. `AUTH_BYPASS_UID` setting (local only, refused when `ENVIRONMENT=production`)
- [ ] `pipeline/youtube_url.py`: `parse_youtube_video_id(raw_url) -> str`, raises `InvalidYouTubeUrlError`
- [ ] `clients/youtube_metadata.py`: `YouTubeMetadataClient.fetch(video_id) -> YouTubeVideoMetadata` (title, channel\_title, duration\_seconds, privacy\_status, embeddable, live\_broadcast\_content)
- [ ] `services/video_service.py`: `validate_video_for_processing(metadata)` raising the 422 errors from the API contract

**Acceptance:** unit tests cover all 7 URL forms plus 5 invalid inputs; `respx` tests cover public, private, live and too-long videos; an API test shows 401 without a token.

### M2 · Gemini video analysis (4 h, highest risk)

- [ ] `clients/gemini_gateway.py`: `GeminiGateway` Protocol with `analyze_clip`, `write_narration`, `synthesize_speech`, `answer_question`; `GoogleGeminiGateway` implements it with `tenacity` retries (3 attempts, exponential backoff, 429 and 5xx only) and a 180-second timeout per analysis call
- [ ] `pipeline/chunking.py`, `pipeline/timestamps.py`, `pipeline/cue_merging.py` (pure, from the Pipeline section)
- [ ] `pipeline/prompts.py`: analysis and narration prompts as module constants
- [ ] `cli.py`: `uv run ishaara analyze <youtube_url> --language en` prints merged cues as JSON, and saves raw Gemini responses to `tests/fixtures/`
- [ ] Run the CLI on 3 real lectures (one English math, one Hinglish, one with diagrams); tune the prompt; record findings in `docs/DECISIONS.md`

**Acceptance:** CLI returns cues with plausible timestamps for all 3 videos; unit tests for chunking, both timestamp cases, and every merge rule pass using the saved fixtures; no test calls the real API.

### M3 · Jobs, persistence, caching, first deploy (3 h)

- [ ] `clients/firestore_repository.py`: `VideoRepository` (`get`, `create_if_absent` in a transaction, `update_progress`, `mark_ready`, `mark_failed`, `list_for_user`), `UsageRepository.increment_and_check(uid, field, limit)`
- [ ] `services/job_runner.py`: `JobRunner.start(video_key)` creates an `asyncio.Task`, keeps a strong reference in a set, refreshes `lease_expires_at` every 30 seconds, and logs any exception
- [ ] `video_service.submit`: ready → 200; queued or processing with a live lease → 202 with the existing record; failed or expired lease → reset and restart; new → create, start, 202
- [ ] `pipeline/orchestrator.py`: stages 1–3 wired to the repository with progress updates (narration and voicing stubbed until M4)
- [ ] `POST /v1/videos`, `GET /v1/videos/{video_key}`, `GET /v1/me/videos`
- [ ] First Cloud Run deploy (see Deployment)

**Acceptance:** with fakes, submitting the same URL twice starts one job; an expired lease is restarted; the daily limit returns 429. On Cloud Run, a real video reaches `stage=writing` with progress visible while polling.

### M4 · Narration and speech (3 h)

- [ ] `write_narration` with `NarrationBatch` validation (every `cue_id` exactly once, retry once)
- [ ] `synthesize_speech` returning WAV bytes; `clients/audio_storage.py` `AudioStorage.upload_wav(path, wav_bytes) -> public_url`
- [ ] Orchestrator stages 4–5: concurrency 4, per-cue failure retried twice then that cue keeps `audio_url=None` (the job still succeeds; the frontend falls back to the screen reader)
- [ ] Transcript JSON written to Cloud Storage; `token_usage` summed from every interaction's usage metadata

**Acceptance:** a real 10-minute video reaches `ready` with cues that have playable `audio_url`s in English and in Hindi; a test with a fake gateway that fails one TTS call still ends `ready`.

### M5 · Cues, WebVTT and notes (2 h)

- [ ] `renderers/webvtt.py` and `renderers/notes.py`
- [ ] `GET .../cues`, `GET .../cues.vtt` (`media_type="text/vtt"`), `GET .../notes?format=markdown|html`; all return 409 until ready

**Acceptance:** golden-file tests for the VTT and Markdown output; the VTT validates in a browser `<track>` element; HTML notes pass an axe or Lighthouse accessibility check with no critical issues.

### M6 · Questions (2 h)

- [ ] `services/question_service.py` following the Questions flow; counts toward `questions_asked` (default limit 50 per day)
- [ ] `POST /v1/videos/{video_key}/questions`

**Acceptance:** a test with the fake gateway covers the text-only path and the visual-check path; a real question such as "What was on the board at 12:40?" gets a spoken answer.

### M7 · Hardening and final deploy (2 h)

- [ ] Request-size limits, `max_length` on `question` (500 characters), input trimming
- [ ] Graceful shutdown: on SIGTERM, mark running jobs' leases expired so the next request restarts them
- [ ] Final deploy; pre-process the 3 demo videos so the demo never waits
- [ ] `README.md`: setup, env vars, run, test, deploy, API examples with `curl`

**Acceptance:** full flow works from the deployed URL with a real Firebase token; demo videos return `200 ready` instantly.

### M8 · Observability (2 h, only if time allows)

- [ ] Request middleware: bind `request_id`, `uid` and the Cloud Trace id from `X-Cloud-Trace-Context` into structlog context; log method, path, status, latency
- [ ] Emit Cloud Logging fields: `severity` and `logging.googleapis.com/trace`, so logs group under each request
- [ ] One `pipeline_stage_completed` event per stage with `video_key`, `stage`, `duration_ms`, `input_tokens`, `output_tokens`, `model`
- [ ] Log-based metrics in Cloud Monitoring: jobs failed by `error_code`, p95 pipeline duration, tokens per day
- [ ] Optional: OpenTelemetry tracing with `opentelemetry-exporter-gcp-trace`, a span per Gemini call

**Acceptance:** one Logs Explorer query on `jsonPayload.video_key` shows a whole job end to end.

## Configuration and secrets

Every setting is a field on `Settings` with the default shown. Secrets come from Secret Manager in production and from `.env` locally.

| Variable | Default | Secret | Purpose |
| --- | --- | --- | --- |
| `ENVIRONMENT` | `local` |  | `local` or `production`; production refuses `AUTH_BYPASS_UID` |
| `GEMINI_API_KEY` | — | Yes | Gemini API key from AI Studio |
| `YOUTUBE_API_KEY` | — | Yes | YouTube Data API v3 key, restricted to that API |
| `GCP_PROJECT_ID` | — |  | Firebase and Google Cloud project |
| `AUDIO_BUCKET` | — |  | Public-read bucket for narration WAVs |
| `TRANSCRIPT_BUCKET` | — |  | Private bucket for transcript JSON |
| `ANALYSIS_MODEL` | `gemini-3.8-flash` |  | Video analysis and visual Q&A |
| `TEXT_MODEL` | `gemini-3.5-flash-lite` |  | Narration writing and text Q&A |
| `TTS_MODEL` | `gemini-3.8-flash-lite-tts` |  | Speech |
| `TTS_VOICE` | `Kore` |  | Prebuilt voice |
| `PIPELINE_VERSION` | `1` |  | Part of the cache key; bump on prompt changes |
| `MAX_VIDEO_MINUTES` | `45` |  | Longest accepted video |
| `CLIP_WINDOW_SECONDS` | `600` |  | Analysis window length |
| `CLIP_OVERLAP_SECONDS` | `10` |  | Window overlap |
| `ANALYSIS_CONCURRENCY` | `3` |  | Parallel Gemini video calls per job |
| `TTS_CONCURRENCY` | `4` |  | Parallel TTS calls per job |
| `MIN_CONFIDENCE` | `0.5` |  | Pointing moments below this are dropped |
| `MIN_CUE_GAP_SECONDS` | `6` |  | Closer moments merge into one cue |
| `DAILY_VIDEO_LIMIT` | `10` |  | Videos per user per day |
| `DAILY_QUESTION_LIMIT` | `50` |  | Questions per user per day |
| `ALLOWED_ORIGINS` | `http://localhost:5173` |  | Comma-separated CORS origins |
| `AUTH_BYPASS_UID` | empty |  | Local development only |
| `LOG_LEVEL` | `INFO` |  | structlog level |

Locally, Firebase Admin and Google Cloud clients use Application Default Credentials (`gcloud auth application-default login`). On Cloud Run they use the service account, so no key files exist anywhere.

## Deployment to Cloud Run

One Cloud Run service in `asia-south1` with instance-based billing, so background jobs keep their CPU after the response returns. Minimum 1 instance keeps a job from being cut off by scale-to-zero during the demo; maximum 2 caps cost.

**Dockerfile**

```dockerfile
FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
CMD exec uvicorn ishaara.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8080}
```

**One-time project setup**

```bash
export PROJECT_ID=ishaara-hack REGION=asia-south1
gcloud config set project $PROJECT_ID

gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com firestore.googleapis.com \
  secretmanager.googleapis.com youtube.googleapis.com

gcloud firestore databases create --location=$REGION

gcloud storage buckets create gs://$PROJECT_ID-narration-audio --location=$REGION --uniform-bucket-level-access
gcloud storage buckets add-iam-policy-binding gs://$PROJECT_ID-narration-audio \
  --member=allUsers --role=roles/storage.objectViewer
gcloud storage buckets create gs://$PROJECT_ID-transcripts --location=$REGION --uniform-bucket-level-access

printf '%s' "$GEMINI_API_KEY"  | gcloud secrets create gemini-api-key  --data-file=-
printf '%s' "$YOUTUBE_API_KEY" | gcloud secrets create youtube-api-key --data-file=-

gcloud iam service-accounts create ishaara-api
export SA=ishaara-api@$PROJECT_ID.iam.gserviceaccount.com
for ROLE in roles/datastore.user roles/storage.objectAdmin roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding $PROJECT_ID --member=serviceAccount:$SA --role=$ROLE
done
```

**Deploy** (repeat for every release)

```bash
gcloud run deploy ishaara-api --source . --region $REGION \
  --service-account $SA \
  --no-cpu-throttling --min-instances 1 --max-instances 2 \
  --cpu 1 --memory 1Gi --concurrency 40 --timeout 300 \
  --allow-unauthenticated \
  --set-secrets GEMINI_API_KEY=gemini-api-key:latest,YOUTUBE_API_KEY=youtube-api-key:latest \
  --set-env-vars ENVIRONMENT=production,GCP_PROJECT_ID=$PROJECT_ID,AUDIO_BUCKET=$PROJECT_ID-narration-audio,TRANSCRIPT_BUCKET=$PROJECT_ID-transcripts,ALLOWED_ORIGINS=https://$PROJECT_ID.web.app
```

`--allow-unauthenticated` only opens the network edge; every `/v1` route still demands a valid Firebase ID token. The `--timeout 300` applies to HTTP requests, not background jobs.

**Smoke test after each deploy**

```bash
URL=$(gcloud run services describe ishaara-api --region $REGION --format 'value(status.url)')
curl -s $URL/healthz
curl -s -X POST $URL/v1/videos -H "Authorization: Bearer $FIREBASE_ID_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"youtube_url": "https://youtu.be/<demo-id>", "narration_language": "en"}'
```

Get `FIREBASE_ID_TOKEN` from the frontend's browser console with `await firebase.auth().currentUser.getIdToken()`, or from the Firebase Auth REST sign-in endpoint for a test user.

**Upgrade path after the hackathon:** move job execution to Cloud Tasks (it retries failed tasks and supports runs up to 30 minutes), then return to request-based billing and scale to zero.

## Risks and fallbacks

| Risk | Effect | Mitigation |
| --- | --- | --- |
| YouTube URL input is a preview feature; pricing and limits may change | Analysis calls fail or get rate-limited | Daily per-user limits; cache every result; pre-process demo videos; fallback below |
| Free tier allows 8 hours of YouTube video per day | Quota exhausted mid-demo | 45-minute cap, per-user limits, cache by `video_key`; enable billing before demo day |
| Private, unlisted, live or non-embeddable videos | Gemini cannot read them, or the player cannot embed them | Rejected up front in the resolve stage with a clear 422 |
| Gemini returns clip-relative timestamps | Cues land at the wrong moment | `normalize_clip_timestamps` plus unit tests for both cases |
| Board handwriting is unreadable | Wrong or vague descriptions | `confidence` filter; prompt asks the model to say it is unsure; pick clear demo videos |
| Job lost when an instance stops | Video stuck in `processing` | Lease heartbeat; an expired lease restarts the job on the next request |
| Interactions API field names differ from this plan | Calls fail in M2 | All calls isolated in `GeminiGateway`; checked against the docs on the first real run |
| Too many cues pause the video constantly | Unwatchable playback | `MIN_CUE_GAP_SECONDS` merging; tune on the 3 test lectures |

**Fallback if the YouTube URL path fails entirely:** for the 3 demo videos only, download them once on a laptop, upload them with the Gemini Files API, and point `analyze_clip` at the file URI instead. The rest of the pipeline is unchanged because everything sits behind `GeminiGateway`.

## Sources

- [Gemini API: Video understanding](https://ai.google.dev/gemini-api/docs/video-understanding) — YouTube URL input, limits, clipping, static and agentic modes
- [Gemini API: Models](https://ai.google.dev/gemini-api/docs/models) — current model ids
- [Gemini API: Structured outputs](https://ai.google.dev/gemini-api/docs/structured-output) — `response_format` with Pydantic schemas
- [Gemini API: Speech generation](https://ai.google.dev/gemini-api/docs/speech-generation) — TTS models, WAV output, Hindi support, SDK version
- [Cloud Run: Billing settings](https://docs.cloud.google.com/run/docs/configuring/billing-settings) — instance-based billing for background work, Cloud Tasks guidance
- [YouTube Data API quota costs](https://www.socialcrawl.dev/blog/youtube-data-api-2026) — `videos.list` costs 1 unit
