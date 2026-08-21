#!/bin/sh
set -eu

project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
region=${CAFFEMATE_GCP_REGION:-asia-northeast3}
source_revision=${CAFFEMATE_SOURCE_REVISION:-}
instance_id=${CAFFEMATE_DB_INSTANCE_ID:-caffemate-postgres}
database_name=${CAFFEMATE_DB_NAME:-caffemate}
database_user=${CAFFEMATE_DB_USER:-caffemate_app}
job_name=${CAFFEMATE_MIGRATION_JOB:-caffemate-migrate}

if [ -z "$project_id" ] || [ -z "$source_revision" ]; then
  printf '%s\n' 'CAFFEMATE_GCP_PROJECT_ID and CAFFEMATE_SOURCE_REVISION are required' >&2
  exit 2
fi

case "$source_revision" in
  *[!0-9a-f]*|'')
    printf '%s\n' 'CAFFEMATE_SOURCE_REVISION must contain lowercase hexadecimal characters' >&2
    exit 2
    ;;
esac

if [ "${#source_revision}" -ne 40 ]; then
  printf '%s\n' 'CAFFEMATE_SOURCE_REVISION must be a full 40-character commit SHA' >&2
  exit 2
fi

if [ "$region" != 'asia-northeast3' ]; then
  printf 'refusing non-canonical region: %s\n' "$region" >&2
  exit 2
fi

active_project=$(gcloud config get-value project 2>/dev/null)
if [ "$active_project" != "$project_id" ]; then
  printf 'active gcloud project %s does not match requested project %s\n' \
    "$active_project" "$project_id" >&2
  exit 2
fi

tagged_image="${region}-docker.pkg.dev/${project_id}/caffemate-backend/backend:${source_revision}"
build_sa="projects/${project_id}/serviceAccounts/caffemate-backend-build@${project_id}.iam.gserviceaccount.com"
runtime_sa="caffemate-migrate@${project_id}.iam.gserviceaccount.com"
instance_connection_name=$(gcloud sql instances describe "$instance_id" \
  --project="$project_id" \
  --format='value(connectionName)')

if [ -z "$instance_connection_name" ]; then
  printf '%s\n' 'Cloud SQL instance connection name is empty' >&2
  exit 1
fi

if ! gcloud artifacts docker images describe "$tagged_image" \
  --project="$project_id" >/dev/null 2>&1; then
  gcloud builds submit . \
    --project="$project_id" \
    --region="$region" \
    --config=cloudbuild.backend-image.yaml \
    --substitutions="_IMAGE_TAG=${source_revision}" \
    --service-account="$build_sa" \
    --quiet
fi

image=$(gcloud artifacts docker images describe "$tagged_image" \
  --project="$project_id" \
  --format='value(image_summary.fully_qualified_digest)')
case "$image" in
  "${region}-docker.pkg.dev/${project_id}/caffemate-backend/backend@sha256:"*) ;;
  *)
    printf '%s\n' 'Artifact Registry did not return the expected backend image digest' >&2
    exit 1
    ;;
esac

configure_job() {
  action=$1
  gcloud run jobs "$action" "$job_name" \
    --project="$project_id" \
    --region="$region" \
    --image="$image" \
    --service-account="$runtime_sa" \
    --set-cloudsql-instances="$instance_connection_name" \
    --set-env-vars="INSTANCE_CONNECTION_NAME=${instance_connection_name},DB_USER=${database_user},DB_NAME=${database_name},CLOUD_SQL_IP_TYPE=PUBLIC" \
    --set-secrets='DB_PASS=caffemate-db-password:latest' \
    --command=caffemate-api \
    --args=migrate \
    --tasks=1 \
    --parallelism=1 \
    --max-retries=0 \
    --task-timeout=10m \
    --cpu=1 \
    --memory=512Mi \
    --labels="source-revision=${source_revision},managed-by=caffemate-deploy" \
    --quiet >/dev/null
}

if gcloud run jobs describe "$job_name" \
  --project="$project_id" \
  --region="$region" >/dev/null 2>&1; then
  configure_job update
else
  configure_job create
fi

gcloud run jobs execute "$job_name" \
  --project="$project_id" \
  --region="$region" \
  --wait \
  --quiet >/dev/null

gcloud run jobs execute "$job_name" \
  --project="$project_id" \
  --region="$region" \
  --args=verify-migrations \
  --wait \
  --quiet >/dev/null

printf '%s\n' 'migration and independent migration verification executions succeeded'
