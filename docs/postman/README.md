# Postman

Two files:

- `ishaara.postman_collection.json`: one request for every implemented endpoint, plus
  error-case requests, each with tests that check the documented status codes and fields.
- `ishaara-local.postman_environment.json`: variables for a local server.

## Use

1. In Postman, **Import** both files and select the **Ishaara - local** environment.
2. Set `idToken` to a Firebase ID token (from the frontend's `getIdToken()`), or leave it empty
   when the server runs with `AUTH_BYPASS_UID` set in `.env` (local development only).
3. Start the server: `uv run uvicorn ishaara.main:create_app --factory`.
4. Run **Videos** in order: *submit* stores `videoKey`, then re-send *poll status* until `status`
   is `ready` or `failed`.
5. For a deployed service, change `baseUrl` to the Cloud Run URL and use a real token.

Notes:

- The *No token* request expects a 401, so it fails when the server uses `AUTH_BYPASS_UID`.
- Submitting a new video calls Gemini and counts towards the daily limit (10). A video that is
  already `ready` returns `200` instantly and costs nothing.
- Endpoints that are planned but not built (cues, WebVTT, notes, questions) are listed in
  `docs/API.md` and are not in the collection yet.
