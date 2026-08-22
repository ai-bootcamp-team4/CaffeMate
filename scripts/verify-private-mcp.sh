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
build_sa="projects/${project_id}/serviceAccounts/caffemate-backend-build@${project_id}.iam.gserviceaccount.com"

. "$(dirname "$0")/build-provenance-helpers.sh"

[ -n "$project_id" ] && [ "${#source_revision}" -eq 40 ] && [ -n "$official_rag_corpus_resource" ] || { printf '%s\n' 'project, full source revision and official RAG corpus resource are required' >&2; exit 2; }
service_json=$(mktemp)
policy_json=$(mktemp)
project_policy_json=$(mktemp)
retriever_role_json=$(mktemp)
trap 'rm -f "$service_json" "$policy_json" "$project_policy_json" "$retriever_role_json"' EXIT
gcloud run services describe "$service_name" --project="$project_id" --region="$region" --format=json >"$service_json"
gcloud run services get-iam-policy "$service_name" --project="$project_id" --region="$region" --format=json >"$policy_json"
gcloud projects get-iam-policy "$project_id" --format=json >"$project_policy_json"
gcloud iam roles describe caffemateMcpRetriever \
  --project="$project_id" --format=json >"$retriever_role_json"

tagged_image="${region}-docker.pkg.dev/${project_id}/caffemate-backend/mcp:${source_revision}"
preflight_tagged_image="${region}-docker.pkg.dev/${project_id}/caffemate-backend/agent-release-preflight:${source_revision}"
tagged_digest=$(gcloud artifacts docker images describe "$tagged_image" \
  --project="$project_id" --format='value(image_summary.fully_qualified_digest)')
case "$tagged_digest" in
  "${region}-docker.pkg.dev/${project_id}/caffemate-backend/mcp@sha256:"*) ;;
  *) printf '%s\n' 'tagged MCP image digest is unavailable' >&2; exit 1 ;;
esac
digest=${tagged_digest##*@}
build_id=$(verified_build_id_for_image \
  "$tagged_image" "$digest" "$source_revision" "$build_sa")
preflight_tagged_digest=$(gcloud artifacts docker images describe "$preflight_tagged_image" \
  --project="$project_id" --format='value(image_summary.fully_qualified_digest)')
case "$preflight_tagged_digest" in
  "${region}-docker.pkg.dev/${project_id}/caffemate-backend/agent-release-preflight@sha256:"*) ;;
  *) printf '%s\n' 'tagged Agent release-preflight image digest is unavailable' >&2; exit 1 ;;
esac
preflight_build_id=$(verified_build_id_for_image \
  "$preflight_tagged_image" "${preflight_tagged_digest##*@}" "$source_revision" "$build_sa")
[ "$preflight_build_id" = "$build_id" ] || {
  printf '%s\n' 'MCP runtime and Agent release-preflight build provenance differ' >&2
  exit 1
}

python3 - "$service_json" "$policy_json" "$project_policy_json" "$retriever_role_json" "$source_revision" "$api_sa" "$mcp_sa" "$project_id" "$official_rag_corpus_resource" "$tagged_digest" "$build_id" <<'PY'
import json, sys
service = json.load(open(sys.argv[1]))
policy = json.load(open(sys.argv[2]))
project_policy = json.load(open(sys.argv[3]))
retriever_role_definition = json.load(open(sys.argv[4]))
revision, api_sa, mcp_sa, project_id, corpus, tagged_digest, build_id = sys.argv[5:]
template = service["spec"]["template"]
assert template["metadata"]["labels"]["source-revision"] == revision
assert template["metadata"]["labels"]["build-id"] == build_id
assert template["spec"]["serviceAccountName"] == mcp_sa
image = template["spec"]["containers"][0]["image"]
assert image == tagged_digest
env = {row["name"]: row.get("value") for row in template["spec"]["containers"][0].get("env", [])}
assert env["CAFFEMATE_GCP_PROJECT_ID"] == project_id
assert env["RAG_OFFICIAL_CORPUS_RESOURCE"] == corpus
members = {m for b in policy.get("bindings", []) if b["role"] == "roles/run.invoker" for m in b.get("members", [])}
assert "allUsers" not in members
assert f"serviceAccount:{api_sa}" in members
assert all(m == f"serviceAccount:{api_sa}" for m in members)
member = f"serviceAccount:{mcp_sa}"
retriever_role = f"projects/{project_id}/roles/caffemateMcpRetriever"
assert set(retriever_role_definition["includedPermissions"]) == {
    "aiplatform.ragCorpora.query",
    "discoveryengine.rankingConfigs.rank",
}
assert any(b["role"] == retriever_role and member in b.get("members", []) for b in project_policy.get("bindings", []))
direct_roles = {
    b["role"] for b in project_policy.get("bindings", []) if member in b.get("members", [])
}
assert direct_roles == {retriever_role, "roles/serviceusage.serviceUsageConsumer"}, direct_roles
assert not any(
    b["role"] in {"roles/aiplatform.user", "roles/discoveryengine.viewer"}
    and member in b.get("members", [])
    for b in project_policy.get("bindings", [])
)
print("MCP_DEPLOYMENT_CONTRACT_OK")
PY

runtime_resource=$(python3 - <<'PY'
import json
print(json.load(open("agents/release-manifest.json"))["runtime"]["resource_name"])
PY
)
access_token=$(gcloud auth print-access-token)
rag_files=$(curl --fail --silent --show-error --connect-timeout 10 --max-time 30 \
  --header "Authorization: Bearer ${access_token}" \
  "https://${region}-aiplatform.googleapis.com/v1/${official_rag_corpus_resource}/ragFiles?pageSize=100")
rag_file_resource=$(printf '%s' "$rag_files" | python3 -c \
  'import json,sys; rows=json.load(sys.stdin).get("ragFiles", []); assert rows and rows[0].get("name"); print(rows[0]["name"])')

iam_verify_job='caffemate-mcp-iam-verify'
configure_iam_verify_job() {
  action=$1
  gcloud run jobs "$action" "$iam_verify_job" \
    --project="$project_id" --region="$region" \
    --image="$tagged_digest" --service-account="$mcp_sa" \
    --set-env-vars="CAFFEMATE_GCP_REGION=${region},AGENT_RUNTIME_RESOURCE=${runtime_resource},RAG_CORPUS_RESOURCE=${official_rag_corpus_resource},RAG_FILE_RESOURCE=${rag_file_resource}" \
    --command=node --args=deploy/runtime-iam-smoke.mjs \
    --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=2m \
    --cpu=1 --memory=512Mi --quiet >/dev/null
}
if gcloud run jobs describe "$iam_verify_job" \
  --project="$project_id" --region="$region" >/dev/null 2>&1; then
  configure_iam_verify_job update
else
  configure_iam_verify_job create
fi
gcloud run jobs execute "$iam_verify_job" \
  --project="$project_id" --region="$region" --wait --quiet >/dev/null
printf '%s\n' 'PASS MCP runtime identity has no prohibited effective mutation permission'

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
