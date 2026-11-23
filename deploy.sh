#!/usr/bin/env bash
# Deploy the feature pipeline: build the image from the current commit, then create or update
# both Cloud Run jobs and their schedules. Safe to re-run after every code change.
# One-time setup (APIs, bucket, service accounts): docs/deploy_gcp.md
set -euo pipefail

PROJECT=$(gcloud config get-value project)
REGION=europe-west6
SCHEDULER_REGION=europe-west6   # where the Cloud Scheduler jobs live
BUCKET=hn-breakout-data-15244
SA=hn-pipeline@$PROJECT.iam.gserviceaccount.com
SHA=$(git rev-parse --short HEAD)
IMAGE=$REGION-docker.pkg.dev/$PROJECT/hn-breakout/hn-breakout

# the image is tagged with the commit, so it must contain exactly that commit
git diff --quiet HEAD || { echo "commit your changes first (also docs/deploys.log)"; exit 1; }

gcloud builds submit --tag "$IMAGE:$SHA" .
DIGEST=$(gcloud artifacts docker images describe "$IMAGE:$SHA" --format='value(image_summary.digest)')

# name, command, timeout, memory, schedule (UTC), scheduler job name
deploy() {
  gcloud run jobs deploy "$1" --region="$REGION" --image="$IMAGE@$DIGEST" \
    --service-account="$SA" --set-env-vars="STORAGE_ROOT=gs://$BUCKET/v1,GIT_SHA=$SHA" \
    --command="$2" --task-timeout="$3" --memory="$4" --max-retries=0
  schedule=(--location="$SCHEDULER_REGION" --schedule="$5" --time-zone=UTC --http-method=POST
    --uri="https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/jobs/$1:run"
    --oauth-service-account-email="$SA")
  if gcloud scheduler jobs describe "$6" --location="$SCHEDULER_REGION" >/dev/null 2>&1; then
    gcloud scheduler jobs update http "$6" "${schedule[@]}"
  else
    gcloud scheduler jobs create http "$6" "${schedule[@]}"
  fi
}
deploy hn-poll hn-poll 240s 1Gi "*/5 * * * *" hn-poll-every-5min
deploy hn-build-features hn-build-features 600s 2Gi "15 3 * * *" hn-build-features-daily

echo "$(date -u +%FT%TZ) $SHA $DIGEST" >> docs/deploys.log
echo "Deployed $SHA ($DIGEST). Commit docs/deploys.log as the record of this deploy."
