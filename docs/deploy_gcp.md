# Deploying the feature pipeline on Google Cloud

The poller runs as a **Cloud Run Job**, started every 5 minutes by **Cloud Scheduler**, and the
feature build as a second job, once a day. Data goes to a **Cloud Storage bucket** as
date-partitioned Parquet files. GitHub Actions reads the bucket for training and the daily
completeness report.

Steps 0 to 4 are a one-time setup. After that, every deploy is one command (step 5).

Run these commands in the Google Cloud Shell (top right in the console, `>_` icon),
or locally after installing the `gcloud` CLI and running `gcloud auth login`.

## 0. Variables

The values of this project. To run your own copy, pick another bucket name (it must be
globally unique) and change it here and in `deploy.sh`.

```bash
cat >> ~/.bashrc <<'EOF'
# HN Breakout
export PROJECT=$(gcloud config get-value project)
export REGION=europe-west6
export BUCKET=hn-breakout-data-15244
export REPO=hn-breakout
export SA=hn-pipeline@$PROJECT.iam.gserviceaccount.com
EOF
source ~/.bashrc
echo $PROJECT $BUCKET   # check
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

## 3. Cloud Build and the image repository (once)

Cloud Build runs with the Compute Engine default service account, which has no permissions in
new projects (without this the build fails with `403 ... storage.objects.get`):

```bash
export PROJECT_NUMBER=$(gcloud projects describe $PROJECT --format='value(projectNumber)')
gcloud projects add-iam-policy-binding $PROJECT \
  --member=serviceAccount:$PROJECT_NUMBER-compute@developer.gserviceaccount.com \
  --role=roles/cloudbuild.builds.builder
gcloud artifacts repositories create $REPO --repository-format=docker --location=$REGION
```

## 4. Read access for GitHub Actions (once)

Training and the completeness report run in GitHub Actions and read the bucket. GitHub logs in
with a short-lived token (Workload Identity Federation), so no key file exists. Only workflows
from this repository are accepted, and they can only read.

```bash
export GH_REPO=AbsturzWindows/hn-breakout GH_SA=github-reader@$PROJECT.iam.gserviceaccount.com
gcloud services enable iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com
gcloud iam service-accounts create github-reader --display-name="GitHub Actions (read-only)"
gcloud storage buckets add-iam-policy-binding gs://$BUCKET \
  --member=serviceAccount:$GH_SA --role=roles/storage.objectViewer
gcloud iam workload-identity-pools create github --location=global --display-name="GitHub"
gcloud iam workload-identity-pools providers create-oidc github-repo \
  --location=global --workload-identity-pool=github \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='$GH_REPO'"
gcloud iam service-accounts add-iam-policy-binding $GH_SA --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$GH_REPO"
```

In GitHub (Settings → Secrets and variables → Actions), the variables `GCP_WIF_PROVIDER`
(`projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github-repo`),
`GCP_SERVICE_ACCOUNT` (`$GH_SA`) and `STORAGE_ROOT` (`gs://$BUCKET/v1`); for MLflow the variable
`MLFLOW_TRACKING_URI` and the secrets `MLFLOW_TRACKING_USERNAME` and `MLFLOW_TRACKING_PASSWORD`
(a DagsHub token).

If a `gcloud services enable` call fails with `429 Quota exceeded`, add `--billing-project=$PROJECT`.

## 5. Deploy (after every code change)

From the repository root, with everything committed:

```bash
./deploy.sh
```

It builds the image from the current commit, creates or updates both jobs (poller every
5 minutes, feature build daily at 03:15 UTC) and their schedules, and appends the commit and image
digest to `docs/deploys.log`; commit that file as the record of the deploy. The jobs get the
commit as `GIT_SHA`, and the feature build writes it into every file (`code_version`).

The poller runs with `--max-retries=0`: a failed run is not repeated, because the next run 5
minutes later takes a fresh snapshot anyway, and missed labels are caught up automatically.

After a change to the features, rebuild the past days once (up to about an hour):

```bash
gcloud run jobs execute hn-build-features --region=$REGION \
  --args=2026-10-13,$(date -u -d yesterday +%F) --task-timeout=3600s --wait
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
