#!/usr/bin/env bash
# Smoke test for a running Ishaara API (local container or Cloud Run).
#
#   scripts/smoke_test.sh <base-url> [firebase-id-token]
#
# Without a token only the public checks run. Environment overrides:
#   DEMO_URL            video to submit (default: the English circular-tracks lecture)
#   NARRATION_LANGUAGE  en or hi (default: en)
# A submission that answers 200 means the video was already processed (instant demo).
set -u

BASE="${1:?usage: smoke_test.sh <base-url> [firebase-id-token]}"
BASE="${BASE%/}"
TOKEN="${2:-${FIREBASE_ID_TOKEN:-}}"
DEMO_URL="${DEMO_URL:-https://youtu.be/v8kBd64_WAc}"
LANGUAGE="${NARRATION_LANGUAGE:-en}"
failures=0

check() { # name expected actual
  if [ "$2" = "$3" ]; then
    echo "PASS  $1 (HTTP $3)"
  else
    echo "FAIL  $1: expected HTTP $2, got $3"
    failures=$((failures + 1))
  fi
}

status() { curl -s -o /dev/null -w '%{http_code}' --max-time 60 "$@"; }

# /health, not /healthz: Cloud Run reserves /healthz and answers it with its own 404.
check "health check" 200 "$(status "$BASE/health")"
check "/v1 requires a token" 401 "$(status "$BASE/v1/me/videos")"
check "oversized body is rejected" 413 \
  "$(head -c 20000 /dev/zero | tr '\0' x | curl -s -o /dev/null -w '%{http_code}' --max-time 60 \
      -X POST -H 'Content-Type: application/json' --data-binary @- "$BASE/v1/videos")"

if [ -z "$TOKEN" ]; then
  echo "SKIP  authenticated checks (pass a Firebase ID token as the second argument)"
else
  auth=(-H "Authorization: Bearer $TOKEN")
  check "history with a token" 200 "$(status "${auth[@]}" "$BASE/v1/me/videos")"
  submit_status="$(status "${auth[@]}" -X POST -H 'Content-Type: application/json' \
    -d "{\"youtube_url\": \"$DEMO_URL\", \"narration_language\": \"$LANGUAGE\"}" "$BASE/v1/videos")"
  if [ "$submit_status" = 200 ] || [ "$submit_status" = 202 ]; then
    echo "PASS  submit demo video (HTTP $submit_status; 200 = already processed)"
  else
    echo "FAIL  submit demo video: expected HTTP 200 or 202, got $submit_status"
    failures=$((failures + 1))
  fi
fi

if [ "$failures" -eq 0 ]; then echo "All checks passed."; else echo "$failures check(s) failed."; fi
exit "$failures"
