#!/bin/sh
set -eu

project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
region=${CAFFEMATE_GCP_REGION:-asia-northeast3}
source_revision=${CAFFEMATE_SOURCE_REVISION:-}
dataset_id=${CAFFEMATE_GROUNDING_DATASET:-caffemate_grounding}
bucket_name=${CAFFEMATE_GROUNDING_BUCKET:-${project_id}-caffemate-grounding}
job_name=${CAFFEMATE_GROUNDING_JOB:-caffemate-grounding-ingest}
scheduler_name=${CAFFEMATE_GROUNDING_SCHEDULER:-caffemate-grounding-weekly}

if [ -z "$project_id" ] || [ "${#source_revision}" -ne 40 ]; then
  printf '%s\n' 'project and full source revision are required' >&2
  exit 2
fi
[ "$region" = 'asia-northeast3' ] || {
  printf '%s\n' 'canonical Seoul region required' >&2
  exit 2
}
[ "$(git rev-parse HEAD)" = "$source_revision" ] || {
  printf '%s\n' 'checked-out revision does not match requested revision' >&2
  exit 2
}
[ "$(git ls-remote origin refs/heads/main | awk '{print $1}')" = "$source_revision" ] || {
  printf '%s\n' 'verification must target immutable origin/main' >&2
  exit 2
}

job_revision=$(gcloud run jobs describe "$job_name" \
  --project="$project_id" --region="$region" \
  --format='value(metadata.labels.source-revision)')
[ "$job_revision" = "$source_revision" ] || {
  printf 'job source revision mismatch: %s\n' "$job_revision" >&2
  exit 1
}

bucket_location=$(gcloud storage buckets describe "gs://${bucket_name}" \
  --format='value(location)')
[ "$bucket_location" = 'ASIA-NORTHEAST3' ] || {
  printf 'grounding bucket location mismatch: %s\n' "$bucket_location" >&2
  exit 1
}
dataset_location=$(bq --project_id="$project_id" show --format=prettyjson \
  "${project_id}:${dataset_id}" | jq -r '.location')
[ "$dataset_location" = 'asia-northeast3' ] || {
  printf 'grounding dataset location mismatch: %s\n' "$dataset_location" >&2
  exit 1
}
scheduler_state=$(gcloud scheduler jobs describe "$scheduler_name" \
  --project="$project_id" --location="$region" --format='value(state)')
[ "$scheduler_state" = 'ENABLED' ] || {
  printf 'grounding scheduler state mismatch: %s\n' "$scheduler_state" >&2
  exit 1
}

gcloud run jobs execute "$job_name" \
  --project="$project_id" --region="$region" --wait --quiet >/dev/null

latest_manifest=$(bq --project_id="$project_id" query --nouse_legacy_sql --format=json \
  "SELECT ingestion_id, status, loaded_at, source_periods_json, row_counts_json
   FROM \`${project_id}.${dataset_id}.source_manifest\`
   WHERE status = 'APPROVED'
   ORDER BY loaded_at DESC
   LIMIT 1")
ingestion_id=$(printf '%s' "$latest_manifest" | jq -r '.[0].ingestion_id // empty')
[ -n "$ingestion_id" ] || {
  printf '%s\n' 'approved source manifest is missing' >&2
  exit 1
}

gcloud storage cat "gs://${bucket_name}/manifests/${ingestion_id}.json" >/dev/null
gcloud storage cat "gs://${bucket_name}/approvals/${ingestion_id}.json" \
  | jq -e --arg id "$ingestion_id" \
    '.status == "APPROVED" and .ingestion_id == $id' >/dev/null

mapping_count=$(bq --project_id="$project_id" query --nouse_legacy_sql --format=csv \
  "SELECT COUNT(*) FROM \`${project_id}.${dataset_id}.area_mapping\`
   WHERE ingestion_id = '${ingestion_id}' AND legal_dong_code = '1144012300'
     AND admin_dong_code IN ('11440690', '11440700')" \
  | tail -n 1 | tr -d '"')
[ "$mapping_count" -ge 2 ] || {
  printf 'official legal-to-admin mapping verification failed: %s\n' "$mapping_count" >&2
  exit 1
}

store_count=$(bq --project_id="$project_id" query --nouse_legacy_sql --format=csv \
  "SELECT COUNT(*) FROM \`${project_id}.${dataset_id}.seoul_cafe_store_fact\`
   WHERE ingestion_id = '${ingestion_id}'" | tail -n 1 | tr -d '"')
population_count=$(bq --project_id="$project_id" query --nouse_legacy_sql --format=csv \
  "SELECT COUNT(*) FROM \`${project_id}.${dataset_id}.seoul_population_fact\`
   WHERE ingestion_id = '${ingestion_id}'" | tail -n 1 | tr -d '"')
[ "$store_count" -gt 0 ] || {
  printf '%s\n' 'cafe store facts are empty' >&2
  exit 1
}
[ "$population_count" -gt 0 ] || {
  printf '%s\n' 'population facts are empty' >&2
  exit 1
}

printf '%s\n' "$latest_manifest" | jq '{status:"VERIFIED", latest:.[0]}'
