# Deploying Ishaara to Cloud Run

One Cloud Run service in `asia-south1` (Mumbai). It runs the API and, in the same process, the
background jobs that analyse videos, so it is deployed with CPU always allocated and at least one
instance (otherwise a job is frozen as soon as its request returns).

> **What has and has not been verified.** The Docker image builds, starts, answers `/health`,
> applies the request limits and shuts down cleanly on SIGTERM. The Supabase database and Supabase
> Storage adapters and the **Firestore adapter** have run against real services (Firestore: the 13
> repository contract tests passed against the real `ishaara-v1` database) and the **Google Cloud
> Storage adapter** (public and private upload, read-back and the public/private access rules were
> checked on the real buckets). **Nothing has been deployed to Cloud Run yet.**

## 1. Choose your backends

| | Database (`DATABASE_BACKEND`) | Audio and transcripts (`STORAGE_BACKEND`) |
| --- | --- | --- |
| **Recommended: Supabase** | `supabase`: needs the tables from `supabase/schema.sql` and the **session pooler** URL | `supabase`: a public bucket for audio, a private bucket for transcripts, the **secret** key |
| Google | `firestore`: verified against a real database; free-tier quotas are small | `gcs`: a public-read bucket and a private bucket (verified on real buckets) |

You can mix them. Everything below shows the Supabase variant first and the Google differences after.

**Region:** create the Supabase project in Mumbai (`ap-south-1`) if you can. A project in Sydney adds
about 0.3 s to every database call.

## 2. Prerequisites

- A Google Cloud project with **billing enabled**. It is also your Firebase project: the frontend
  signs users in with Firebase Auth, and the backend verifies those tokens.
- `gcloud` installed and logged in. In this session you can run the interactive steps yourself
  with the `!` prefix:

  ```bash
  curl -sSL https://sdk.cloud.google.com | bash      # installs gcloud; then restart the shell
  gcloud auth login
  gcloud config set project <your-project-id>
  ```

  The deploy builds the image in Cloud Build, so local Docker is not needed.
- A YouTube Data API v3 key (enable `youtube.googleapis.com`; restrict the key to that API).
- A Gemini API key.
- Your frontend's origin, e.g. `https://<project>.web.app`, for CORS.

```bash
export PROJECT_ID=<your-project-id>
export REGION=asia-south1
gcloud config set project $PROJECT_ID
```

## 3. One-time setup

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com youtube.googleapis.com

gcloud iam service-accounts create ishaara-api
export SA=ishaara-api@$PROJECT_ID.iam.gserviceaccount.com
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member=serviceAccount:$SA --role=roles/secretmanager.secretAccessor
```

**Secrets.** These commands read the values from your local env files and send them straight to
Secret Manager without printing them. Run them from the repository root. `secret <name> <VAR> [env]`
reads `.env` overlaid by `.env.<env>` (default `prod`).

```bash
secret() {
  uv run python -c "
import sys
from dotenv import dotenv_values
from ishaara.settings import env_files
values = {}
for env_file in env_files('${3:-prod}'):
    values.update(dotenv_values(env_file))
sys.stdout.write(values['$2'])" | gcloud secrets create "$1" --data-file=-
}
secret gemini-api-key       GEMINI_API_KEY
secret youtube-api-key      YOUTUBE_API_KEY
# Only if you deploy with Supabase (their values live in .env.local):
secret supabase-service-key SUPABASE_SERVICE_KEY local   # the SECRET key, never the publishable one
secret supabase-db-url      SUPABASE_DB_URL      local   # the session-pooler connection string
```

Let the service account read each secret:

```bash
for NAME in gemini-api-key youtube-api-key; do
  gcloud secrets add-iam-policy-binding $NAME --member=serviceAccount:$SA \
    --role=roles/secretmanager.secretAccessor
done
```

Rotate a secret later with `gcloud secrets versions add <name> --data-file=-`, then redeploy.

**Supabase.** Run `supabase/schema.sql` once in the Supabase SQL editor (it also turns on row-level
security so the public API key cannot read your tables). Create two Storage buckets: a **public**
one for audio and a **private** one for transcripts.

**Google variant instead of (or alongside) Supabase:**

```bash
# Firestore
gcloud services enable firestore.googleapis.com
gcloud firestore databases create --location=$REGION
gcloud projects add-iam-policy-binding $PROJECT_ID --member=serviceAccount:$SA --role=roles/datastore.user

# Cloud Storage: one public-read bucket for audio, one private bucket for transcripts
gcloud storage buckets create gs://$PROJECT_ID-narration-audio --location=$REGION --uniform-bucket-level-access
gcloud storage buckets add-iam-policy-binding gs://$PROJECT_ID-narration-audio \
  --member=allUsers --role=roles/storage.objectViewer
gcloud storage buckets create gs://$PROJECT_ID-transcripts --location=$REGION --uniform-bucket-level-access
gcloud projects add-iam-policy-binding $PROJECT_ID --member=serviceAccount:$SA --role=roles/storage.objectAdmin
```

## 4. Deploy

The production settings live in `.env.prod` (no secrets; see `.env.prod.example`), layered over the
shared `.env`. `scripts/deploy.sh` turns them into the `gcloud run deploy` command: plain settings
become environment variables, and the API keys are referenced by their Secret Manager names. Look at
it first, then run it:

```bash
DRY_RUN=1 scripts/deploy.sh      # prints the exact command, nothing is changed
scripts/deploy.sh                # builds in Cloud Build and deploys to Cloud Run
```

The command line it builds has these fixed parts: region `asia-south1`, service account
`ishaara-api@<project>.iam.gserviceaccount.com`, `--no-cpu-throttling --min-instances 1
--max-instances 2 --cpu 1 --memory 1Gi --concurrency 40 --timeout 300 --allow-unauthenticated`.
`uv run ishaara deploy-env` prints just the project id, the `--set-env-vars` value and the
`--set-secrets` value, so you can inspect them. The variable list uses a custom delimiter (`^@^`)
because `ALLOWED_ORIGINS` contains commas. It also checks the values the way the app will: it refuses
to produce a command for a configuration the app would not start with.

For Supabase instead of Google backends, set `DATABASE_BACKEND=supabase`, `STORAGE_BACKEND=supabase`,
`SUPABASE_URL` and the bucket names in `.env.prod`; the script then also references the
`supabase-db-url` and `supabase-service-key` secrets.

Notes:

- `--allow-unauthenticated` only opens the network edge. Every `/v1` route still demands a valid
  Firebase ID token.
- The app **refuses to start** in production with the in-memory database, local file storage or an
  auth bypass, so a mistake in these variables fails the deploy instead of silently losing data.
- `--set-env-vars` replaces the service's whole variable list on every deploy, so the env files stay
  the single source of truth.
- `--timeout 300` applies to HTTP requests, not to background jobs.
- `--min-instances 1` with `--no-cpu-throttling` is billed continuously. See "Stopping the cost".

## 5. Pre-process the demo videos

So that the demo never waits for Gemini, process the videos once, into the same database and storage
the deployed service uses. Run it on your machine with the **production** environment selected, which
loads `.env` and `.env.prod` (Firestore and Cloud Storage) and uses your Application Default
Credentials (`gcloud auth application-default login`):

```bash
ISHAARA_ENV=prod uv run ishaara process https://youtu.be/<id-1> https://youtu.be/<id-2> https://youtu.be/<id-3> --languages en hi
```

The app logs `settings_loaded` with the environment and backends at start, so you can see where it is
writing. Without `ISHAARA_ENV=prod` the same command writes to your local (Supabase) environment.

It prints one JSON line per video and language (`ready`, `cached` or `failed`, with the number of
cues and how many have audio) and exits non-zero if anything failed. It is safe to re-run: a video
that is already processed is reported as `cached` and costs nothing. Each video uses Gemini tokens
(about 55,000 input tokens for a 9-minute lecture, per language). The command uses its own user, so
it does not use up a student's daily limit.

## 6. Smoke test

```bash
URL=$(gcloud run services describe ishaara-api --region $REGION --format 'value(status.url)')
scripts/smoke_test.sh "$URL"                      # public checks
scripts/smoke_test.sh "$URL" "$FIREBASE_ID_TOKEN" # plus authenticated checks
```

Get `FIREBASE_ID_TOKEN` from the frontend's browser console with
`await firebase.auth().currentUser.getIdToken()`, or from the Firebase Auth REST sign-in endpoint
for a test user. Tokens expire after an hour. A submission that answers `200` means the video was
already processed, which is what you want on demo day.

## 7. Operating it

```bash
gcloud run services logs read ishaara-api --region $REGION --limit 100   # recent logs
gcloud run revisions list --service ishaara-api --region $REGION          # revisions
gcloud run services update-traffic ishaara-api --region $REGION --to-revisions <revision>=100  # roll back
```

Logs are JSON. Useful events: `pipeline_failed`, `gemini_retry` (a retried Gemini call), `cue_voiced`
(with how long each took), `cue_left_silent`, `question_answered`, `jobs_interrupted_for_shutdown`.

**Deploys and running jobs.** When Cloud Run replaces an instance it sends SIGTERM. The service
cancels running jobs and expires their leases, so the next request for an interrupted video restarts
it straight away. Avoid deploying while a demo video is being processed.

**Stopping the cost.** The always-on instance costs money while it exists. Between demos:

```bash
gcloud run services update ishaara-api --region $REGION --min-instances 0   # scale to zero (jobs may pause)
gcloud run services delete ishaara-api --region $REGION                     # remove it completely
```

With `--min-instances 0` and CPU throttling a background job can be frozen after its request ends,
so only do this when nothing is processing.

## 8. Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| Deploy fails at startup with a validation error | Production refuses `memory`, `local` storage and `AUTH_BYPASS_UID`; check the variables |
| `/healthz` returns Google's HTML 404 page | Cloud Run reserves `/healthz`; use `/health` |
| Every `/v1` call returns 401 | Token from a different Firebase project than `GCP_PROJECT_ID`, an expired token, or the frontend not sending `Authorization: Bearer` |
| Browser blocks calls | The frontend origin is missing from `ALLOWED_ORIGINS` (exact scheme and host, no trailing slash) |
| Supabase calls fail or time out | Using the direct host or port 6543 instead of the **session pooler** URL, or the schema was not applied |
| Audio uploads fail with a row-level-security error | `SUPABASE_SERVICE_KEY` holds the publishable key instead of the secret key |
| Cue audio does not play | The audio bucket is not public |
| `video_not_public` or `video_not_embeddable` | The lecture is private, unlisted or has embedding disabled |
| Videos finish with silent cues (`audio_url` null) | Gemini speech is rate-limited or not responding; the text still works. Check the `gemini_retry` logs and try again later |
| A video stays `processing` | The instance died; the next request restarts it once its lease has expired (at most 90 s) |
| Many 429 responses from Gemini | Free-tier limits (about 8 hours of YouTube video per day); enable billing before demo day |
