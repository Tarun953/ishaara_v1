# Postman

A complete Postman setup for the Ishaara API: requests for every endpoint, tests, saved example
responses, automatic polling, and two environments. The full API reference is
[`../API.md`](../API.md); the collection's own description (shown in Postman) repeats the quick start.

## Files

| File | What it is |
| --- | --- |
| `ishaara.postman_collection.json` | 25 requests in 6 folders, 33 saved example responses, and tests on every request |
| `ishaara-local.postman_environment.json` | **Ishaara - local**: `http://localhost:8000`, `authBypass=true` |
| `ishaara-prod.postman_environment.json` | **Ishaara - prod (Cloud Run)**: the deployed service URL, `authBypass=false`, `idToken` blank |

No secrets are stored in these files. `idToken` is declared as a secret variable and left empty.

## Using it

1. In Postman, **Import** the collection and both environments, then select one.
2. **Prod:** set `idToken` to a Firebase ID token. In your frontend's browser console:
   `await firebase.auth().currentUser.getIdToken()` (it expires after an hour).
   **Local:** start the server with `AUTH_BYPASS_UID=dev-user uv run uvicorn ishaara.main:create_app --factory`
   and leave `idToken` empty.
3. Open folder **2. Process a lecture** and run it in the **Collection Runner**. *Submit a video* sends
   `youtubeUrl`, then *Poll status until finished* repeats every 3 seconds (up to 200 times) until the
   video is `ready` or `failed`. A video that is already processed returns `200` immediately.
4. Run folders **3. Read the result**, **4. Ask a question** and **5. History**.
5. Folder **6. Error cases** shows every documented failure.

You can also send requests one at a time; to follow progress that way, re-send the poll request.
To use a video that is already processed, set the collection variable `videoKey` (for example
`v8kBd64_WAc_en_v2`) and skip folder 2.

## Folders

| Folder | Requests |
| --- | --- |
| 1. Health | `GET /health`, `GET /healthz` |
| 2. Process a lecture | Submit a video; Poll status until finished |
| 3. Read the result | Cues (JSON); Cue audio (public URL); WebVTT track; Notes (Markdown); Notes (HTML) |
| 4. Ask a question | Ask about a moment (with `at_seconds`); Ask without a time |
| 5. History | My videos |
| 6. Error cases | No token; Invalid token; Not a YouTube link; Missing field; Oversized body; Unknown video; Malformed key; Cues of an unknown video; Unknown notes format; Blank question; Question over 500 characters; Negative `at_seconds`; `at_seconds` past the end |

## Variables

| Variable | Scope | Meaning |
| --- | --- | --- |
| `baseUrl` | environment | Server address, no trailing slash |
| `idToken` | environment (secret) | Firebase ID token, sent as `Authorization: Bearer ...` |
| `authBypass` | environment | `true` for a local server with `AUTH_BYPASS_UID`: the two auth error tests then expect 200 |
| `youtubeUrl`, `narrationLanguage` | environment | Video to process, `en` or `hi` |
| `question`, `atSeconds` | environment | Used by *Ask about a moment* |
| `videoKey` | collection | Set by *Submit a video*; used by every other request |
| `audioUrl` | collection | Set by *Cues (JSON)* to the first cue's audio URL |
| `pollAttempts` | collection | Poll counter, reset automatically |

## What the tests check

- **Every response:** any 4xx/5xx must be `application/problem+json` with `type`, `title`, `status`,
  `detail` and `code`, and `status` must equal the HTTP status. (Google's own HTML error pages are exempt.)
- **Status:** the full status object, progress within 0 to 100, and `200` meaning `ready` while `202`
  means not yet.
- **Cues:** every documented field, ids `cue-0001...` in order, times ascending, a known `referent_kind`.
- **WebVTT:** the `WEBVTT` / `Kind: descriptions` header, valid timing lines, ordered, never overlapping.
- **Notes:** one title and `##` sections with no skipped heading level; for HTML, `lang`, `<title>`,
  one `<main>`, one `<h1>`, and no raw `<script>`.
- **Answers:** non-empty text, an ascending list of times, and an audio URL that is a URL or `null`.
- **History:** valid keys, titles and times, newest first.

## Notes

- A new video costs Gemini tokens and one of the user's 10 daily videos; each question costs one of 50
  daily questions and can take 30 to 90 seconds. Folders 3 and 6 spend nothing.
- Error cases that need an existing video (unknown notes format, blank or long question, negative or
  too-large `at_seconds`) use `videoKey`, so run folder 2 first.
- **Cue audio** uses the cue's public URL without authentication, as a browser `<audio>` element would.
  Use **Send and Download** to listen to it.
- On Cloud Run, `GET /healthz` is answered by Google with an HTML 404; that request accepts it.
- On a local server started with the auth bypass, set `authBypass` to `true` (the local environment does).

## Running it from the command line

[Newman](https://github.com/postmanlabs/newman) runs the same tests without the Postman app:

```bash
npx newman run docs/postman/ishaara.postman_collection.json \
  -e docs/postman/ishaara-local.postman_environment.json \
  --env-var videoKey=<a ready video key> \
  --folder "1. Health" --folder "3. Read the result" --folder "5. History" --folder "6. Error cases"
```

Folders 2 and 4 call Gemini, so leave them out unless you want that cost. Against the deployed service,
requests that need no token (`GET /health`, `No token`, `Invalid token`, `Oversized body`) can be run with
`-e docs/postman/ishaara-prod.postman_environment.json`; the rest need `--env-var idToken=<token>`.

## Keeping it in step

`tests/unit/test_api_docs.py` fails if a route in the API is missing from the collection or from
`docs/API.md`. The collection was run with Newman against a local server (21 requests, 48 assertions,
all passing, using a seeded ready video) and the token-free requests against the live Cloud Run
service. It has not been run inside the Postman desktop app, and folders 2 and 4 (which spend Gemini
tokens) have not been run end to end through Newman.
