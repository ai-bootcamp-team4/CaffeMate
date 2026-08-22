#!/bin/sh
set -eu

project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
region=${CAFFEMATE_GCP_REGION:-asia-northeast3}
source_revision=${CAFFEMATE_SOURCE_REVISION:-}
service_name=${CAFFEMATE_MCP_SERVICE_NAME:-caffemate-mcp}
official_rag_corpus_resource=${CAFFEMATE_OFFICIAL_RAG_CORPUS_RESOURCE:-}
api_sa="caffemate-api-runtime@${project_id}.iam.gserviceaccount.com"
worker_sa="caffemate-worker-runtime@${project_id}.iam.gserviceaccount.com"
mcp_sa="caffemate-mcp-runtime@${project_id}.iam.gserviceaccount.com"
verify_job=${CAFFEMATE_MCP_VERIFY_JOB:-caffemate-mcp-verify}

[ -n "$project_id" ] && [ "${#source_revision}" -eq 40 ] && [ -n "$official_rag_corpus_resource" ] || { printf '%s\n' 'project, full source revision and official RAG corpus resource are required' >&2; exit 2; }
service_json=$(mktemp)
policy_json=$(mktemp)
project_policy_json=$(mktemp)
trap 'rm -f "$service_json" "$policy_json" "$project_policy_json"' EXIT
gcloud run services describe "$service_name" --project="$project_id" --region="$region" --format=json >"$service_json"
gcloud run services get-iam-policy "$service_name" --project="$project_id" --region="$region" --format=json >"$policy_json"
gcloud projects get-iam-policy "$project_id" --format=json >"$project_policy_json"

python3 - "$service_json" "$policy_json" "$project_policy_json" "$source_revision" "$api_sa" "$mcp_sa" "$project_id" "$official_rag_corpus_resource" <<'PY'
import json, sys
service = json.load(open(sys.argv[1]))
policy = json.load(open(sys.argv[2]))
project_policy = json.load(open(sys.argv[3]))
revision, api_sa, mcp_sa, project_id, corpus = sys.argv[4:]
template = service["spec"]["template"]
assert template["metadata"]["labels"]["source-revision"] == revision
assert template["spec"]["serviceAccountName"] == mcp_sa
image = template["spec"]["containers"][0]["image"]
assert "@sha256:" in image
env = {row["name"]: row.get("value") for row in template["spec"]["containers"][0].get("env", [])}
assert env["CAFFEMATE_GCP_PROJECT_ID"] == project_id
assert env["RAG_OFFICIAL_CORPUS_RESOURCE"] == corpus
members = {m for b in policy.get("bindings", []) if b["role"] == "roles/run.invoker" for m in b.get("members", [])}
assert "allUsers" not in members
assert f"serviceAccount:{api_sa}" in members
assert all(m == f"serviceAccount:{api_sa}" for m in members)
vertex_members = {m for b in project_policy.get("bindings", []) if b["role"] == "roles/aiplatform.user" for m in b.get("members", [])}
assert f"serviceAccount:{mcp_sa}" in vertex_members
ranking_members = {m for b in project_policy.get("bindings", []) if b["role"] == "roles/discoveryengine.viewer" for m in b.get("members", [])}
assert f"serviceAccount:{mcp_sa}" in ranking_members
print("MCP_DEPLOYMENT_CONTRACT_OK")
PY

mcp_url=$(gcloud run services describe "$service_name" --project="$project_id" --region="$region" --format='value(status.url)')
unauth_status=$(curl -sS -o /dev/null -w '%{http_code}' "${mcp_url}/healthz")
case "$unauth_status" in 401|403|404) ;; *) printf 'unauthenticated request returned %s\n' "$unauth_status" >&2; exit 1;; esac

image=$(python3 - "$service_json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))["spec"]["template"]["spec"]["containers"][0]["image"])
PY
)
configure_verify_job() {
  action=$1
  gcloud run jobs "$action" "$verify_job" --project="$project_id" --region="$region" \
    --image="$image" --service-account="$api_sa" \
    --set-env-vars="MCP_BASE_URL=${mcp_url}" \
    --set-secrets='MCP_SCOPE_HMAC_SECRET=caffemate-mcp-scope-hmac:latest' \
    --command=node --args=--import,tsx,mcp/src/smoke.ts \
    --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=2m \
    --cpu=1 --memory=512Mi --quiet >/dev/null
}
if gcloud run jobs describe "$verify_job" --project="$project_id" --region="$region" >/dev/null 2>&1; then
  configure_verify_job update
else
  configure_verify_job create
fi
gcloud run jobs execute "$verify_job" --project="$project_id" --region="$region" --wait --quiet >/dev/null

worker_binding=$(gcloud run services get-iam-policy "$service_name" --project="$project_id" --region="$region" \
  --flatten='bindings[].members' --filter="bindings.role=roles/run.invoker AND bindings.members=serviceAccount:${worker_sa}" --format='value(bindings.members)')
[ -z "$worker_binding" ] || { printf '%s\n' 'worker identity unexpectedly has MCP invoker access' >&2; exit 1; }

printf '%s\n' 'private MCP deployment and protocol verification succeeded'
