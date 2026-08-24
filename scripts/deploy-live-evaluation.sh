#!/bin/sh
set -eu

project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
region=${CAFFEMATE_GCP_REGION:-asia-northeast3}
source_revision=${CAFFEMATE_SOURCE_REVISION:-}
agent_runtime_resource_id=${CAFFEMATE_AGENT_RUNTIME_RESOURCE_ID:-}
bucket_name=${CAFFEMATE_EVALUATION_BUCKET:-${project_id}-caffemate-evaluation}
job_name=${CAFFEMATE_EVALUATION_JOB:-caffemate-live-e2e-evaluation}
pipeline_sa="caffemate-evaluation-pipeline@${project_id}.iam.gserviceaccount.com"
api_sa="caffemate-api-runtime@${project_id}.iam.gserviceaccount.com"

if [ -z "$project_id" ] || [ -z "$source_revision" ] || [ -z "$agent_runtime_resource_id" ]; then
  printf '%s\n' 'project id, source revision and Agent Runtime resource id are required' >&2
  exit 2
fi
case "$source_revision" in
  *[!0-9a-f]*|'') printf '%s\n' 'source revision must be lowercase hexadecimal' >&2; exit 2 ;;
esac
[ "${#source_revision}" -eq 40 ] || {
  printf '%s\n' 'source revision must be a full commit SHA' >&2
  exit 2
}
[ "$(gcloud config get-value project 2>/dev/null)" = "$project_id" ] || {
  printf '%s\n' 'active gcloud project does not match requested project' >&2
  exit 2
}
[ "$(git rev-parse HEAD)" = "$source_revision" ] || {
  printf '%s\n' 'checked-out source differs from requested revision' >&2
  exit 2
}
[ -z "$(git status --porcelain)" ] || {
  printf '%s\n' 'evaluation deployment requires a clean checkout' >&2
  exit 2
}
[ "$(git ls-remote origin refs/heads/main | awk '{print $1}')" = "$source_revision" ] || {
  printf '%s\n' 'evaluation deployment source must be immutable origin/main' >&2
  exit 2
}

gcloud services enable aiplatform.googleapis.com run.googleapis.com storage.googleapis.com \
  --project="$project_id" --quiet >/dev/null

if ! gcloud iam service-accounts describe "$pipeline_sa" \
  --project="$project_id" >/dev/null 2>&1; then
  gcloud iam service-accounts create caffemate-evaluation-pipeline \
    --project="$project_id" \
    --display-name='CaffeMate Vertex evaluation pipeline' \
    --quiet >/dev/null
fi

if ! gcloud storage buckets describe "gs://${bucket_name}" \
  --project="$project_id" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${bucket_name}" \
    --project="$project_id" \
    --location="$region" \
    --uniform-bucket-level-access \
    --quiet >/dev/null
fi
for member in "$api_sa" "$pipeline_sa"; do
  gcloud storage buckets add-iam-policy-binding "gs://${bucket_name}" \
    --member="serviceAccount:${member}" \
    --role='roles/storage.objectAdmin' \
    --quiet >/dev/null
done
for role in roles/aiplatform.user roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$project_id" \
    --member="serviceAccount:${pipeline_sa}" \
    --role="$role" \
    --quiet >/dev/null
done

# 사용자 의도: Pipeline은 다른 Cloud Run 자원을 관리하지 않고, 평가 Job에 실행별
# 보고서 경로를 넘기는 override 실행 권한만 가져야 한다.
runner_role='caffemateEvaluationJobRunner'
runner_permissions='run.jobs.get,run.jobs.run,run.jobs.runWithOverrides,run.executions.get,run.executions.list'
if gcloud iam roles describe "$runner_role" --project="$project_id" >/dev/null 2>&1; then
  gcloud iam roles update "$runner_role" \
    --project="$project_id" \
    --title='CaffeMate evaluation job runner' \
    --description='Execute the evaluation Job with a report URI override' \
    --stage=GA \
    --permissions="$runner_permissions" \
    --quiet >/dev/null
else
  gcloud iam roles create "$runner_role" \
    --project="$project_id" \
    --title='CaffeMate evaluation job runner' \
    --description='Execute the evaluation Job with a report URI override' \
    --stage=GA \
    --permissions="$runner_permissions" \
    --quiet >/dev/null
fi

tagged_image="${region}-docker.pkg.dev/${project_id}/caffemate-backend/backend:${source_revision}"
build_sa="projects/${project_id}/serviceAccounts/caffemate-backend-build@${project_id}.iam.gserviceaccount.com"
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
  *) printf '%s\n' 'backend image digest is unavailable' >&2; exit 1 ;;
esac

instance="${project_id}:${region}:caffemate-postgres"
mcp_url=$(gcloud run services describe caffemate-mcp \
  --project="$project_id" --region="$region" --format='value(status.url)')
default_report_uri="gs://${bucket_name}/reports/manual-${source_revision}.json"

configure_job() {
  action=$1
  gcloud run jobs "$action" "$job_name" \
    --project="$project_id" \
    --region="$region" \
    --image="$image" \
    --service-account="$api_sa" \
    --set-cloudsql-instances="$instance" \
    --set-env-vars="INSTANCE_CONNECTION_NAME=${instance},DB_USER=caffemate_app,DB_NAME=caffemate,CLOUD_SQL_IP_TYPE=PUBLIC,MCP_BASE_URL=${mcp_url},MCP_AUDIENCE=${mcp_url},CAFFEMATE_POLICY_SNAPSHOT_ID=policy-v1,AGENT_RUNTIME_PROJECT_ID=${project_id},AGENT_RUNTIME_RESOURCE_ID=${agent_runtime_resource_id},CAFFEMATE_SOURCE_REVISION=${source_revision},CAFFEMATE_GCP_PROJECT_ID=${project_id},CAFFEMATE_OTEL_ENABLED=true,CAFFEMATE_ENVIRONMENT=production,CAFFEMATE_EVALUATION_REPORT_URI=${default_report_uri}" \
    --set-secrets='DB_PASS=caffemate-db-password:latest,MCP_SCOPE_HMAC_SECRET=caffemate-mcp-scope-hmac:latest,AGENT_RUNTIME_USER_HMAC_SECRET=caffemate-agent-runtime-user-hmac:latest' \
    --command=caffemate-api \
    --args=verify-live-evaluation \
    --tasks=1 \
    --parallelism=1 \
    --max-retries=0 \
    --task-timeout=60m \
    --cpu=1 \
    --memory=1Gi \
    --labels="source-revision=${source_revision},managed-by=caffemate-evaluation" \
    --quiet >/dev/null
}
if gcloud run jobs describe "$job_name" \
  --project="$project_id" --region="$region" >/dev/null 2>&1; then
  configure_job update
else
  configure_job create
fi
gcloud run jobs add-iam-policy-binding "$job_name" \
  --project="$project_id" \
  --region="$region" \
  --member="serviceAccount:${pipeline_sa}" \
  --role='roles/run.invoker' \
  --quiet >/dev/null
gcloud run jobs add-iam-policy-binding "$job_name" \
  --project="$project_id" \
  --region="$region" \
  --member="serviceAccount:${pipeline_sa}" \
  --role="projects/${project_id}/roles/${runner_role}" \
  --quiet >/dev/null

printf 'evaluation job: %s\n' "$job_name"
printf 'evaluation bucket: gs://%s\n' "$bucket_name"
printf 'pipeline service account: %s\n' "$pipeline_sa"
