#!/usr/bin/env bash
# Deploy the API to Cloud Run using the production env files (.env + .env.prod).
#
#   scripts/deploy.sh              deploy
#   DRY_RUN=1 scripts/deploy.sh    print the gcloud command and stop
#
# Plain settings come from the env files; secrets are referenced by their Secret Manager names
# and never read from the files. The service gets exactly these variables on every deploy
# (--set-env-vars replaces the whole list), so the env files are the single source of truth.
# Overrides: SERVICE (default ishaara-api), REGION (default asia-south1), ISHAARA_ENV (default prod).
set -euo pipefail
cd "$(dirname "$0")/.."

SERVICE="${SERVICE:-ishaara-api}"
REGION="${REGION:-asia-south1}"

mapfile -t CONFIG < <(uv run --quiet ishaara deploy-env --env-name "${ISHAARA_ENV:-prod}")
PROJECT_ID="${CONFIG[0]}"
ENV_VARS="${CONFIG[1]}"
SECRETS="${CONFIG[2]}"

COMMAND=(gcloud run deploy "$SERVICE" --source . --project "$PROJECT_ID" --region "$REGION"
  --service-account "ishaara-api@${PROJECT_ID}.iam.gserviceaccount.com"
  --no-cpu-throttling --min-instances 1 --max-instances 2
  --cpu 1 --memory 1Gi --concurrency 40 --timeout 300
  --allow-unauthenticated
  --set-secrets "$SECRETS"
  --set-env-vars "$ENV_VARS")

if [ "${DRY_RUN:-}" = 1 ]; then
  printf '%q ' "${COMMAND[@]}"
  echo
  exit 0
fi

"${COMMAND[@]}"
URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --format 'value(status.url)')"
echo "Deployed: $URL"
echo "Next: scripts/smoke_test.sh $URL [firebase-id-token]"
