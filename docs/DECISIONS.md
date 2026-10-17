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
