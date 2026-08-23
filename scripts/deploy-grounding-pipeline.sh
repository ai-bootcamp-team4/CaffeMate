#!/bin/sh
set -eu

project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
region=${CAFFEMATE_GCP_REGION:-asia-northeast3}
source_revision=${CAFFEMATE_SOURCE_REVISION:-}
dataset_id=${CAFFEMATE_GROUNDING_DATASET:-caffemate_grounding}
bucket_name=${CAFFEMATE_GROUNDING_BUCKET:-${project_id}-caffemate-grounding}
job_name=${CAFFEMATE_GROUNDING_JOB:-caffemate-grounding-ingest}
scheduler_name=${CAFFEMATE_GROUNDING_SCHEDULER:-caffemate-grounding-weekly}
schedule=${CAFFEMATE_GROUNDING_SCHEDULE:-0 3 * * 1}

if [ -z "$project_id" ] || [ -z "$source_revision" ]; then
  printf '%s\n' 'CAFFEMATE_GCP_PROJECT_ID and CAFFEMATE_SOURCE_REVISION are required' >&2
  exit 2
fi
case "$source_revision" in
  *[!0-9a-f]*|'') printf '%s\n' 'source revision must be lowercase hexadecimal' >&2; exit 2 ;;
esac
[ "${#source_revision}" -eq 40 ] || {
  printf '%s\n' 'source revision must be a full 40-character commit SHA' >&2
  exit 2
}
[ "$region" = 'asia-northeast3' ] || {
  printf 'refusing non-canonical region: %s\n' "$region" >&2
  exit 2
}
[ "$(gcloud config get-value project 2>/dev/null)" = "$project_id" ] || {
  printf '%s\n' 'active gcloud project does not match requested project' >&2
  exit 2
}
[ "$(git rev-parse HEAD)" = "$source_revision" ] || {
  printf '%s\n' 'requested source revision differs from checked-out HEAD' >&2
  exit 2
}
[ -z "$(git status --porcelain)" ] || {
  printf '%s\n' 'grounding deployment requires a clean source checkout' >&2
  exit 2
}
[ "$(git ls-remote origin refs/heads/main | awk '{print $1}')" = "$source_revision" ] || {
  printf '%s\n' 'grounding deployment source must be immutable origin/main' >&2
  exit 2
}

for identifier in "$dataset_id" "$job_name" "$scheduler_name"; do
  case "$identifier" in
    *[!A-Za-z0-9_-]*|'') printf 'invalid resource identifier: %s\n' "$identifier" >&2; exit 2 ;;
  esac
done

gcloud services enable \
  bigquery.googleapis.com \
  cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com \
  run.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  --project="$project_id" --quiet >/dev/null

create_service_account() {
  account_id=$1
  display_name=$2
  email="${account_id}@${project_id}.iam.gserviceaccount.com"
  if ! gcloud iam service-accounts describe "$email" --project="$project_id" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$account_id" \
      --project="$project_id" --display-name="$display_name" --quiet >/dev/null
  fi
}

create_service_account caffemate-grounding-ingest 'CaffeMate grounding ingestion'
create_service_account caffemate-grounding-scheduler 'CaffeMate grounding scheduler'
runtime_sa="caffemate-grounding-ingest@${project_id}.iam.gserviceaccount.com"
scheduler_sa="caffemate-grounding-scheduler@${project_id}.iam.gserviceaccount.com"

if ! gcloud storage buckets describe "gs://${bucket_name}" --project="$project_id" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${bucket_name}" \
    --project="$project_id" \
    --location="$region" \
    --uniform-bucket-level-access \
    --quiet >/dev/null
fi
gcloud storage buckets update "gs://${bucket_name}" --versioning --quiet >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://${bucket_name}" \
  --member="serviceAccount:${runtime_sa}" \
  --role='roles/storage.objectAdmin' \
  --quiet >/dev/null

if ! bq --project_id="$project_id" show "${project_id}:${dataset_id}" >/dev/null 2>&1; then
  bq --project_id="$project_id" mk --dataset --location="$region" \
    --description='Versioned CaffeMate public grounding snapshots' \
    "${project_id}:${dataset_id}" >/dev/null
fi
gcloud projects add-iam-policy-binding "$project_id" \
  --member="serviceAccount:${runtime_sa}" \
  --role='roles/bigquery.jobUser' \
  --quiet >/dev/null
bq --project_id="$project_id" add-iam-policy-binding --dataset \
  --member="serviceAccount:${runtime_sa}" \
  --role='roles/bigquery.dataEditor' \
  "${project_id}:${dataset_id}" >/dev/null
gcloud secrets add-iam-policy-binding seoul-open-api-key \
  --project="$project_id" \
  --member="serviceAccount:${runtime_sa}" \
  --role='roles/secretmanager.secretAccessor' \
  --quiet >/dev/null
tagged_image="${region}-docker.pkg.dev/${project_id}/caffemate-backend/backend:${source_revision}"
build_sa="projects/${project_id}/serviceAccounts/caffemate-backend-build@${project_id}.iam.gserviceaccount.com"
if ! gcloud artifacts docker images describe "$tagged_image" --project="$project_id" >/dev/null 2>&1; then
  gcloud builds submit . \
    --project="$project_id" \
    --region="$region" \
    --config=cloudbuild.backend-image.yaml \
    --substitutions="_IMAGE_TAG=${source_revision}" \
    --service-account="$build_sa" \
    --quiet
fi
image=$(gcloud artifacts docker images describe "$tagged_image" \
  --project="$project_id" --format='value(image_summary.fully_qualified_digest)')
case "$image" in
  "${region}-docker.pkg.dev/${project_id}/caffemate-backend/backend@sha256:"*) ;;
  *) printf '%s\n' 'backend image digest is unavailable' >&2; exit 1 ;;
esac

configure_job() {
  action=$1
  gcloud run jobs "$action" "$job_name" \
    --project="$project_id" \
    --region="$region" \
    --image="$image" \
    --service-account="$runtime_sa" \
    --set-env-vars="CAFFEMATE_GCP_PROJECT_ID=${project_id},CAFFEMATE_GCP_REGION=${region},CAFFEMATE_GROUNDING_BUCKET=${bucket_name},CAFFEMATE_GROUNDING_DATASET=${dataset_id}" \
    --set-secrets='SEOUL_OPEN_API_KEY=seoul-open-api-key:latest' \
    --command=python \
    --args=-m,app.grounding.seoul_ingest \
    --tasks=1 \
    --parallelism=1 \
    --max-retries=0 \
    --task-timeout=30m \
    --cpu=1 \
    --memory=1Gi \
    --labels="source-revision=${source_revision},managed-by=caffemate-deploy" \
    --quiet >/dev/null
}
if gcloud run jobs describe "$job_name" --project="$project_id" --region="$region" >/dev/null 2>&1; then
  configure_job update
else
  configure_job create
fi
gcloud run jobs add-iam-policy-binding "$job_name" \
  --project="$project_id" \
  --region="$region" \
  --member="serviceAccount:${scheduler_sa}" \
  --role='roles/run.invoker' \
  --quiet >/dev/null

scheduler_uri="https://${region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${project_id}/jobs/${job_name}:run"
if gcloud scheduler jobs describe "$scheduler_name" \
  --project="$project_id" --location="$region" >/dev/null 2>&1; then
  scheduler_action=update
else
  scheduler_action=create
fi
gcloud scheduler jobs "$scheduler_action" http "$scheduler_name" \
  --project="$project_id" \
  --location="$region" \
  --schedule="$schedule" \
  --time-zone='Asia/Seoul' \
  --uri="$scheduler_uri" \
  --http-method=POST \
  --oauth-service-account-email="$scheduler_sa" \
  --oauth-token-scope='https://www.googleapis.com/auth/cloud-platform' \
  --quiet >/dev/null

printf '%s\n' 'grounding pipeline configured; run scripts/verify-grounding-pipeline.sh'
