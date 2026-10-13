# Deploying the feature pipeline on Google Cloud

The poller runs as a **Cloud Run Job**, started every 5 minutes by **Cloud Scheduler**.
Data goes to a **Cloud Storage bucket** as date-partitioned Parquet files.

Run these commands in the Google Cloud Shell (top right in the console, `>_` icon),
or locally after installing the `gcloud` CLI and running `gcloud auth login`.

## 0. Variables

Pick your own names. The bucket name must be globally unique.

```bash
export PROJECT=your-gcp-project-id
export REGION=europe-west6          # Zurich
export BUCKET=hn-breakout-data-$RANDOM
export REPO=hn-breakout
export IMAGE=$REGION-docker.pkg.dev/$PROJECT/$REPO/hn-breakout:latest
export SA=hn-pipeline@$PROJECT.iam.gserviceaccount.com

gcloud config set project $PROJECT
```

## 1. Enable the APIs (once)

```bash
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com \
  artifactregistry.googleapis.com cloudbuild.googleapis.com storage.googleapis.com
```

## 2. Bucket and service account (once)

```bash
gcloud storage buckets create gs://$BUCKET --location=$REGION --uniform-bucket-level-access

gcloud iam service-accounts create hn-pipeline --display-name="HN Breakout pipeline"
# the job may read and write the bucket
gcloud storage buckets add-iam-policy-binding gs://$BUCKET \
  --member=serviceAccount:$SA --role=roles/storage.objectAdmin
# Cloud Scheduler may start Cloud Run jobs as this account
gcloud projects add-iam-policy-binding $PROJECT \
  --member=serviceAccount:$SA --role=roles/run.invoker
```

## 3. Build the Docker image (after every code change)

Run from the repository root:

```bash
gcloud artifacts repositories create $REPO --repository-format=docker --location=$REGION  # once
gcloud builds submit --tag $IMAGE .
```

## 4. Create the poller job (once; later use `jobs update` with the same flags)

```bash
gcloud run jobs create hn-poll \
  --image=$IMAGE --region=$REGION --service-account=$SA \
  --set-env-vars=STORAGE_ROOT=gs://$BUCKET/v1 \
  --task-timeout=240s --max-retries=0 --memory=1Gi

# test one run by hand and check the logs
gcloud run jobs execute hn-poll --region=$REGION --wait
gcloud storage ls -r gs://$BUCKET/v1/ | head
```

`--max-retries=0`: a failed run is not repeated, because the next scheduled run
5 minutes later takes a fresh snapshot anyway. Missed labels are caught up automatically.

## 5. Schedule it every 5 minutes

```bash
gcloud scheduler jobs create http hn-poll-every-5min \
  --location=$REGION --schedule="*/5 * * * *" --time-zone="UTC" \
  --uri="https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/jobs/hn-poll:run" \
  --http-method=POST --oauth-service-account-email=$SA
```

If Cloud Scheduler is not offered in `europe-west6`, use `--location=europe-west1`
for this command only (the URI keeps `$REGION`).

## 6. Daily feature build

Same image, different command. Runs once a day at 03:15 UTC and rebuilds the last 3 days.

```bash
gcloud run jobs create hn-build-features \
  --image=$IMAGE --region=$REGION --service-account=$SA \
  --set-env-vars=STORAGE_ROOT=gs://$BUCKET/v1 \
  --command=hn-build-features --task-timeout=600s --memory=2Gi

gcloud scheduler jobs create http hn-build-features-daily \
  --location=$REGION --schedule="15 3 * * *" --time-zone="UTC" \
  --uri="https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/jobs/hn-build-features:run" \
  --http-method=POST --oauth-service-account-email=$SA
```

## Checking that it works

```bash
# last executions (should be one every 5 minutes, mostly "Succeeded")
gcloud run jobs executions list --job=hn-poll --region=$REGION --limit=10
# files written today
gcloud storage ls gs://$BUCKET/v1/snapshots/date=$(date -u +%F)/ | wc -l
```

Each run also writes a row to `runs/` with counts, failed requests and errors,
which is the basis for monitoring later on.

## Stopping it (e.g. to save credits after the semester)

```bash
gcloud scheduler jobs pause hn-poll-every-5min --location=$REGION
```
