#!/bin/sh
set -eu

manifest='agents/release-manifest.json'
project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
[ -n "$project_id" ] || { printf '%s\n' 'CAFFEMATE_GCP_PROJECT_ID is required' >&2; exit 2; }
[ -f "$manifest" ] || { printf '%s\n' 'agents/release-manifest.json is required' >&2; exit 2; }

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { printf '%s\n' 'deployment must run from a Git worktree' >&2; exit 2; }
source_revision=$(git rev-parse HEAD)
if [ -n "$(git status --porcelain --untracked-files=all)" ]; then
  printf '%s\n' 'deployment requires a clean working tree' >&2
  exit 2
fi

set -- $(python3 - "$manifest" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
r = m['mcp']['runtime']
print(r['service_name'], r['region'], r['source_revision'], r['image_uri'], m['index_generation']['corpus_resource_name'])
PY
)
service_name=$1
region=$2
pinned_revision=$3
pinned_image=$4
official_rag_corpus_resource=$5

case "$pinned_image" in "${region}-docker.pkg.dev/${project_id}/caffemate-backend/mcp@sha256:"*) ;; *) printf '%s\n' 'release MCP image does not belong to requested project/region' >&2; exit 2;; esac
case "$official_rag_corpus_resource" in "projects/${project_id}/locations/${region}/ragCorpora/"*) ;; *) printf '%s\n' 'release RAG corpus does not belong to requested project/region' >&2; exit 2;; esac
if [ "$(gcloud config get-value project 2>/dev/null)" != "$project_id" ]; then
  printf '%s\n' 'active gcloud project does not match requested project' >&2
  exit 2
fi

tagged_image="${region}-docker.pkg.dev/${project_id}/caffemate-backend/mcp:${source_revision}"
build_sa="projects/${project_id}/serviceAccounts/caffemate-backend-build@${project_id}.iam.gserviceaccount.com"
runtime_sa="caffemate-mcp-runtime@${project_id}.iam.gserviceaccount.com"
api_sa="caffemate-api-runtime@${project_id}.iam.gserviceaccount.com"

if ! gcloud artifacts docker images describe "$tagged_image" --project="$project_id" >/dev/null 2>&1; then
  gcloud builds submit . --project="$project_id" --region="$region" \
    --config=cloudbuild.mcp-image.yaml --substitutions="_IMAGE_TAG=${source_revision}" \
    --service-account="$build_sa" --quiet
fi
image=$(gcloud artifacts docker images describe "$tagged_image" --project="$project_id" --format='value(image_summary.fully_qualified_digest)')
case "$image" in "${region}-docker.pkg.dev/${project_id}/caffemate-backend/mcp@sha256:"*) ;; *) printf '%s\n' 'MCP image digest is unavailable' >&2; exit 1;; esac

if [ "$source_revision" != "$pinned_revision" ] || [ "$image" != "$pinned_image" ]; then
  printf 'MCP_RELEASE_PIN_REQUIRED source_revision=%s image_uri=%s\n' "$source_revision" "$image" >&2
  exit 3
fi

# Existing project-level roles are intentionally not broadened here. IAM least-privilege
# remediation is owned by the GCP deployment lane and independently verified.
gcloud projects add-iam-policy-binding "$project_id" \
  --member="serviceAccount:${runtime_sa}" --role='roles/aiplatform.user' --quiet >/dev/null
gcloud projects add-iam-policy-binding "$project_id" \
  --member="serviceAccount:${runtime_sa}" --role='roles/discoveryengine.viewer' --quiet >/dev/null

audience='https://bootstrap.invalid'
if gcloud run services describe "$service_name" --project="$project_id" --region="$region" >/dev/null 2>&1; then
  audience=$(gcloud run services describe "$service_name" --project="$project_id" --region="$region" --format='value(status.url)')
fi

gcloud run deploy "$service_name" --project="$project_id" --region="$region" --image="$pinned_image" \
  --service-account="$runtime_sa" --port=8080 --ingress=all --no-allow-unauthenticated \
  --set-env-vars="MCP_AUDIENCE=${audience},MCP_ALLOWED_CALLER_EMAIL=${api_sa},CAFFEMATE_GCP_PROJECT_ID=${project_id},RAG_OFFICIAL_CORPUS_RESOURCE=${official_rag_corpus_resource}" \
  --set-secrets='MCP_SCOPE_HMAC_SECRET=caffemate-mcp-scope-hmac:latest,JUSO_API_KEY=caffemate-juso-api-key:latest' \
  --cpu=1 --memory=512Mi --min=0 --max=10 \
  --labels="source-revision=${pinned_revision},managed-by=caffemate-deploy" --quiet >/dev/null

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
