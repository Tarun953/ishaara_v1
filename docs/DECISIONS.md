# Decisions

Deviations from `ishaara_backend_implementation_plan.md`, one line each.

- M0: The project lives at the repo root instead of an `ishaara-backend/` subfolder.
- M0: Secrets and resource names (`GEMINI_API_KEY`, buckets, etc.) default to empty strings so `/healthz` and tests run without them.
- M0: The Dockerfile also copies `README.md` because `pyproject.toml` declares it as the package readme.
- M1: Added a `youtube_unavailable` (502) error for persistent YouTube Data API failures; the plan's error list has no code for it.
- M1: `isodate` and `firebase_admin` are in mypy's `ignore_missing_imports` override because they ship no type stubs.
- M2: The Interactions API takes video clip offsets as strings with an `s` suffix (`"590s"`), not integers; `GoogleGeminiGateway` formats them that way.
- M2: Replaced the plan's pointing-word detection with two-pass gap detection. Pointing cues repeated what the teacher had just said aloud (e.g. reading a definition) and missed silent board content. Pass 1 (video, `analyze_clip`) returns the transcript plus every visual event; pass 2 (text, `judge_gaps`) rates each event `fully_spoken`, `partly_spoken` or `not_spoken`; code keeps the last two. `PointingMoment`/`TimedPointingMoment` became `VisualEvent`/`TimedVisualEvent`, and `GeminiGateway` gained `judge_gaps`.
- M2: Tuning pass 2 is cheap because `uv run ishaara analyze <url> --from-fixtures` reuses the saved pass 1 file and calls only the text model. Fixtures are `<id>_w<start>.json` (pass 1) and `<id>_w<start>_gaps.json` (pass 2).
- M2: Findings on two 3-minute clips: the judge needs the rules "look 20 s either side", "a lone bracket or symbol is not a gap" and "labels, arrows, tick marks and shapes are gaps unless named aloud". Judgements vary slightly between runs.
- M2: `NarrationCue.narration_text` defaults to `""` (merging runs before narration exists) and `NarrationCue` gained `gap_summary` (what a listener would miss).
- M2: `write_narration`, `synthesize_speech` and `answer_question` on `GeminiGateway` are placeholder signatures and may change in M4 and M6.
- M2: Only two videos were used: the English circular-track lecture covers both "English math" and "diagrams", and the Hinglish power-set lecture is the third category. The video `resolution` option was not tried.
- M2: Timestamp normalization can misread a window whose events all fall in its first `window length` seconds as clip-relative; real windows have many events, so this was left as is.
- M3: Storage is behind `VideoRepository` / `UsageRepository` ports (`clients/repositories.py`) with in-memory, Firestore and Supabase (Postgres via `psycopg`) adapters chosen by `DATABASE_BACKEND`, so tests and demos can avoid Firestore's free-tier quota. State changes live once in `VideoRecord` update dicts that every adapter applies; one contract suite (`tests/contract`) runs against each backend. Production refuses `memory`.
- M3: Supabase must be reached through the session pooler: the direct host `db.<ref>.supabase.co` is IPv6-only and unreachable from IPv4 networks such as this WSL machine. The current project is in Sydney (about 0.3 s per query); use a Mumbai project for a deployment in `asia-south1`.
- M3: The Supabase contract tests run in a throwaway schema (`ishaara_test_<id>`) and drop it afterwards. They take about 12 s each because of that latency.
- M3: The Firestore adapter has only been type-checked; there is no Java or emulator on this machine, so its 13 contract tests are skipped until `FIRESTORE_EMULATOR_HOST` is set.
- M3: Single-writer updates (progress, lease, ready, failed) are partial writes without a read to save Firestore quota; only create, restart and quota use transactions.
- M3: The pipeline ends after merge and marks the video `ready` with cues that have no narration text or audio. Bump `PIPELINE_VERSION` to 2 when M4 lands so these cached results are not reused.
- M3: A user who loses a race between two first submissions of the same video has already spent one daily-video quota unit; this undercounts in the user's favour and was left as is.
- M3: `get_youtube_client` (M1) was removed; the `AppContainer` composition root owns the YouTube client. Quotas use the UTC date.
- M3: `GeminiError` (502, `gemini_error` / `gemini_invalid_response`) wraps SDK, network and parsing failures so business logic never imports `google.genai`.
