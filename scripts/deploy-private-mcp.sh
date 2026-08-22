#!/bin/sh
set -eu

project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
region=${CAFFEMATE_GCP_REGION:-asia-northeast3}
source_revision=${CAFFEMATE_SOURCE_REVISION:-}
service_name=${CAFFEMATE_MCP_SERVICE_NAME:-caffemate-mcp}
official_rag_corpus_resource=${CAFFEMATE_OFFICIAL_RAG_CORPUS_RESOURCE:-}

if [ -z "$project_id" ] || [ "${#source_revision}" -ne 40 ] || [ "$region" != 'asia-northeast3' ] || [ -z "$official_rag_corpus_resource" ]; then
  printf '%s\n' 'project, Seoul region, full source revision and official RAG corpus resource are required' >&2
  exit 2
fi
case "$source_revision" in *[!0-9a-f]*) printf '%s\n' 'source revision must be lowercase hexadecimal' >&2; exit 2;; esac
case "$official_rag_corpus_resource" in
  "projects/${project_id}/locations/${region}/ragCorpora/"*) ;;
  *) printf '%s\n' 'official RAG corpus must belong to the requested project and Seoul region' >&2; exit 2;;
esac
if [ "$(gcloud config get-value project 2>/dev/null)" != "$project_id" ]; then
  printf '%s\n' 'active gcloud project does not match requested project' >&2
  exit 2
fi

tagged_image="${region}-docker.pkg.dev/${project_id}/caffemate-backend/mcp:${source_revision}"
build_sa="projects/${project_id}/serviceAccounts/caffemate-backend-build@${project_id}.iam.gserviceaccount.com"
runtime_sa="caffemate-mcp-runtime@${project_id}.iam.gserviceaccount.com"
api_sa="caffemate-api-runtime@${project_id}.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding "$project_id" \
  --member="serviceAccount:${runtime_sa}" --role='roles/aiplatform.user' --quiet >/dev/null

if ! gcloud artifacts docker images describe "$tagged_image" --project="$project_id" >/dev/null 2>&1; then
  gcloud builds submit . --project="$project_id" --region="$region" \
    --config=cloudbuild.mcp-image.yaml --substitutions="_IMAGE_TAG=${source_revision}" \
    --service-account="$build_sa" --quiet
fi
image=$(gcloud artifacts docker images describe "$tagged_image" --project="$project_id" --format='value(image_summary.fully_qualified_digest)')
case "$image" in "${region}-docker.pkg.dev/${project_id}/caffemate-backend/mcp@sha256:"*) ;; *) printf '%s\n' 'MCP image digest is unavailable' >&2; exit 1;; esac

audience='https://bootstrap.invalid'
if gcloud run services describe "$service_name" --project="$project_id" --region="$region" >/dev/null 2>&1; then
  audience=$(gcloud run services describe "$service_name" --project="$project_id" --region="$region" --format='value(status.url)')
fi

gcloud run deploy "$service_name" --project="$project_id" --region="$region" --image="$image" \
  --service-account="$runtime_sa" --port=8080 --ingress=all --no-allow-unauthenticated \
  --set-env-vars="MCP_AUDIENCE=${audience},MCP_ALLOWED_CALLER_EMAIL=${api_sa},CAFFEMATE_GCP_PROJECT_ID=${project_id},RAG_OFFICIAL_CORPUS_RESOURCE=${official_rag_corpus_resource}" \
  --set-secrets='MCP_SCOPE_HMAC_SECRET=caffemate-mcp-scope-hmac:latest,JUSO_API_KEY=caffemate-juso-api-key:latest' \
  --cpu=1 --memory=512Mi --min=0 --max=10 \
  --labels="source-revision=${source_revision},managed-by=caffemate-deploy" --quiet >/dev/null

mcp_url=$(gcloud run services describe "$service_name" --project="$project_id" --region="$region" --format='value(status.url)')
if [ "$audience" != "$mcp_url" ]; then
  gcloud run services update "$service_name" --project="$project_id" --region="$region" \
    --update-env-vars="MCP_AUDIENCE=${mcp_url}" --quiet >/dev/null
fi

gcloud run services add-iam-policy-binding "$service_name" --project="$project_id" --region="$region" \
  --member="serviceAccount:${api_sa}" --role='roles/run.invoker' --quiet >/dev/null
gcloud run services remove-iam-policy-binding "$service_name" --project="$project_id" --region="$region" \
  --member='allUsers' --role='roles/run.invoker' --quiet >/dev/null 2>&1 || true

printf '%s\n' 'private MCP deployment completed; run the verifier'
