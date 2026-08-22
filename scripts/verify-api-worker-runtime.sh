#!/bin/sh
set -eu

project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
region=${CAFFEMATE_GCP_REGION:-asia-northeast3}
source_revision=${CAFFEMATE_SOURCE_REVISION:-}

. "$(dirname "$0")/build-provenance-helpers.sh"

if [ -z "$project_id" ] || [ -z "$source_revision" ]; then
  printf '%s\n' 'CAFFEMATE_GCP_PROJECT_ID and CAFFEMATE_SOURCE_REVISION are required' >&2
  exit 2
fi

api_url=$(gcloud run services describe caffemate-api --project="$project_id" \
  --region="$region" --format='value(status.url)')
worker_url=$(gcloud run services describe caffemate-worker --project="$project_id" \
  --region="$region" --format='value(status.url)')
api_sa="caffemate-api-runtime@${project_id}.iam.gserviceaccount.com"
release_verifier_sa="caffemate-release-verifier@${project_id}.iam.gserviceaccount.com"
runtime_invoker_role="projects/${project_id}/roles/caffemateAgentRuntimeInvoker"
session_manager_role="projects/${project_id}/roles/caffemateAgentSessionManager"
release_verifier_role="projects/${project_id}/roles/caffemateReleaseVerifier"

for service in caffemate-api caffemate-worker; do
  revision=$(gcloud run services describe "$service" --project="$project_id" \
    --region="$region" --format='value(metadata.labels.source-revision)')
  [ "$revision" = "$source_revision" ] || {
    printf 'FAIL %s source revision\n' "$service" >&2; exit 1;
  }
  image=$(gcloud run services describe "$service" --project="$project_id" \
    --region="$region" --format='value(spec.template.spec.containers[0].image)')
  case "$image" in *'@sha256:'*) ;; *) printf 'FAIL %s image digest\n' "$service" >&2; exit 1;; esac
  printf 'PASS %s source revision and digest image\n' "$service"
done

api_image=$(gcloud run services describe caffemate-api --project="$project_id" \
  --region="$region" --format='value(spec.template.spec.containers[0].image)')
worker_image=$(gcloud run services describe caffemate-worker --project="$project_id" \
  --region="$region" --format='value(spec.template.spec.containers[0].image)')
[ "$api_image" = "$worker_image" ] || {
  printf '%s\n' 'FAIL API and Worker image digests differ' >&2; exit 1;
}
printf '%s\n' 'PASS API and Worker use the same image digest'

api_service_json=$(gcloud run services describe caffemate-api --project="$project_id" \
  --region="$region" --format=json)
configured_agent_project=$(printf '%s' "$api_service_json" | python3 -c \
  'import json,sys; print(next(row["value"] for row in json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0]["env"] if row["name"] == "AGENT_RUNTIME_PROJECT_ID"))')
configured_agent_resource=$(printf '%s' "$api_service_json" | python3 -c \
  'import json,sys; print(next(row["value"] for row in json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0]["env"] if row["name"] == "AGENT_RUNTIME_RESOURCE_ID"))')
configured_api_audience=$(printf '%s' "$api_service_json" | python3 -c \
  'import json,sys; print(next((row["value"] for row in json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0]["env"] if row["name"] == "CONTROL_API_AUDIENCE"), ""))')
[ "$configured_api_audience" = "$api_url" ] || {
  printf '%s\n' 'FAIL Control API internal identity audience differs from canonical service URL' >&2
  exit 1
}
printf '%s\n' 'PASS Control API internal identity audience matches canonical service URL'
[ "$configured_agent_project" = "$project_id" ] || {
  printf '%s\n' 'FAIL Control API Agent Runtime project differs from deployment project' >&2
  exit 1
}
case "$configured_agent_resource" in
  *[!0-9]*|'') printf '%s\n' 'FAIL Control API Agent Runtime resource id is invalid' >&2; exit 1 ;;
esac

agent_runtime_url="https://${region}-aiplatform.googleapis.com/v1/projects/${project_id}/locations/${region}/reasoningEngines/${configured_agent_resource}"
access_token=$(gcloud auth print-access-token)
agent_runtime_json=$(curl --fail --silent --show-error \
  --header "Authorization: Bearer ${access_token}" \
  "$agent_runtime_url")
agent_runtime_identity=$(printf '%s' "$agent_runtime_json" | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["spec"]["effectiveIdentity"])')
agent_runtime_image=$(printf '%s' "$agent_runtime_json" | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["spec"]["containerSpec"]["imageUri"])')
agent_runtime_resource_name="projects/${project_id}/locations/${region}/reasoningEngines/${configured_agent_resource}"
case "$agent_runtime_identity" in
  principal://agents.*) ;;
  agents.*) agent_runtime_identity="principal://${agent_runtime_identity}" ;;
  *) printf '%s\n' 'FAIL Agent Runtime effective identity is unavailable' >&2; exit 1 ;;
esac
project_policy=$(gcloud projects get-iam-policy "$project_id" --format=json)
project_number=$(gcloud projects describe "$project_id" --format='value(projectNumber)')
agent_default_role_json=$(gcloud iam roles describe roles/aiplatform.agentDefaultAccess \
  --format=json)
runtime_invoker_role_json=$(gcloud iam roles describe caffemateAgentRuntimeInvoker \
  --project="$project_id" --format=json)
session_manager_role_json=$(gcloud iam roles describe caffemateAgentSessionManager \
  --project="$project_id" --format=json)
release_verifier_role_json=$(gcloud iam roles describe caffemateReleaseVerifier \
  --project="$project_id" --format=json)
PROJECT_POLICY="$project_policy" PROJECT_NUMBER="$project_number" AGENT_RUNTIME_IDENTITY="$agent_runtime_identity" API_SERVICE_ACCOUNT="$api_sa" RELEASE_VERIFIER_ROLE="$release_verifier_role" RELEASE_VERIFIER_SERVICE_ACCOUNT="$release_verifier_sa" AGENT_DEFAULT_ROLE_JSON="$agent_default_role_json" RUNTIME_INVOKER_ROLE_JSON="$runtime_invoker_role_json" SESSION_MANAGER_ROLE_JSON="$session_manager_role_json" RELEASE_VERIFIER_ROLE_JSON="$release_verifier_role_json" python3 - <<'PY'
import json
import os

policy = json.loads(os.environ["PROJECT_POLICY"])
agent_default_role = json.loads(os.environ["AGENT_DEFAULT_ROLE_JSON"])
runtime_role = json.loads(os.environ["RUNTIME_INVOKER_ROLE_JSON"])
session_role = json.loads(os.environ["SESSION_MANAGER_ROLE_JSON"])
release_role = json.loads(os.environ["RELEASE_VERIFIER_ROLE_JSON"])
assert not any(
    permission.startswith("aiplatform.")
    and any(action in permission for action in (".create", ".delete", ".update", ".deploy"))
    for permission in agent_default_role["includedPermissions"]
), "managed Agent default access contains Vertex mutation permission"
assert set(runtime_role["includedPermissions"]) == {"aiplatform.reasoningEngines.query"}
assert set(session_role["includedPermissions"]) == {
    "aiplatform.sessionEvents.append",
    "aiplatform.sessionEvents.list",
    "aiplatform.sessions.create",
    "aiplatform.sessions.delete",
    "aiplatform.sessions.get",
    "aiplatform.sessions.list",
    "aiplatform.sessions.update",
}
assert set(release_role["includedPermissions"]) == {
    "aiplatform.endpoints.predict",
    "aiplatform.ragCorpora.get",
    "aiplatform.ragCorpora.list",
    "aiplatform.ragCorpora.query",
    "aiplatform.ragFiles.get",
    "aiplatform.ragFiles.list",
    "aiplatform.reasoningEngines.get",
    "aiplatform.reasoningEngines.list",
    "aiplatform.reasoningEngines.query",
    "discoveryengine.rankingConfigs.rank",
    "run.services.get",
    "storage.objects.get",
}
identity = os.environ["AGENT_RUNTIME_IDENTITY"]
api_member = f"serviceAccount:{os.environ['API_SERVICE_ACCOUNT']}"
release_member = f"serviceAccount:{os.environ['RELEASE_VERIFIER_SERVICE_ACCOUNT']}"
direct_roles = {
    row["role"]
    for row in policy.get("bindings", [])
    if identity in row.get("members", [])
}
assert direct_roles == set(), f"Agent Runtime identity retains direct project roles: {sorted(direct_roles)}"
platform_set = (
    "principalSet://" + identity.removeprefix("principal://").split("/resources/", 1)[0]
    + "/attribute.platformContainer/aiplatform/projects/" + os.environ["PROJECT_NUMBER"]
)
platform_roles = {
    row["role"]
    for row in policy.get("bindings", [])
    if platform_set in row.get("members", [])
}
assert platform_roles == {"roles/aiplatform.agentDefaultAccess"}, platform_roles
assert any(
    row.get("role") == "roles/serviceusage.serviceUsageConsumer"
    and api_member in row.get("members", [])
    for row in policy.get("bindings", [])
), "Control API lacks project service usage permission"
assert any(
    row.get("role") == os.environ["RELEASE_VERIFIER_ROLE"]
    and release_member in row.get("members", [])
    for row in policy.get("bindings", [])
), "release verifier lacks bounded AI preflight role"
print("PASS Agent Runtime identity uses only managed non-mutating default project access")
print("PASS Control API has project service usage permission")
print("PASS release verifier has bounded AI preflight permission")
print("PASS custom AI roles contain only approved permissions")
PY
agent_runtime_policy=$(curl --fail --silent --show-error --request POST \
  --header "Authorization: Bearer ${access_token}" \
  --header 'Content-Type: application/json' \
  "${agent_runtime_url}:getIamPolicy" --data '{}')
AGENT_RUNTIME_POLICY="$agent_runtime_policy" API_SERVICE_ACCOUNT="$api_sa" AGENT_RUNTIME_IDENTITY="$agent_runtime_identity" AGENT_RUNTIME_INVOKER_ROLE="$runtime_invoker_role" AGENT_SESSION_MANAGER_ROLE="$session_manager_role" python3 - <<'PY'
import json
import os

policy = json.loads(os.environ["AGENT_RUNTIME_POLICY"])
member = f"serviceAccount:{os.environ['API_SERVICE_ACCOUNT']}"
agent_identity = os.environ["AGENT_RUNTIME_IDENTITY"]
assert any(
    row.get("role") == os.environ["AGENT_RUNTIME_INVOKER_ROLE"]
    and member in row.get("members", [])
    for row in policy.get("bindings", [])
), "Control API lacks Agent Runtime query IAM"
assert not any(
    row.get("role") == "roles/aiplatform.user" and member in row.get("members", [])
    for row in policy.get("bindings", [])
), "Control API retains broad Agent Runtime mutation role"
assert any(
    row.get("role") == os.environ["AGENT_SESSION_MANAGER_ROLE"]
    and agent_identity in row.get("members", [])
    for row in policy.get("bindings", [])
), "Agent Runtime identity lacks resource-scoped session lifecycle IAM"
assert not any(
    row.get("role") in {"roles/aiplatform.agentContextEditor", "roles/aiplatform.user"}
    and agent_identity in row.get("members", [])
    for row in policy.get("bindings", [])
), "Agent Runtime identity retains resource mutation role"
print("PASS Control API has resource-scoped Agent Runtime query IAM")
print("PASS Agent Runtime identity has resource-scoped session lifecycle IAM")
PY
unset access_token agent_runtime_identity agent_runtime_json agent_runtime_policy project_policy
unset agent_default_role_json runtime_invoker_role_json session_manager_role_json release_verifier_role_json

agent_iam_verify_job='caffemate-agent-runtime-iam-verify'
configure_agent_iam_verify_job() {
  action=$1
  gcloud run jobs "$action" "$agent_iam_verify_job" \
    --project="$project_id" --region="$region" \
    --image="$api_image" --service-account="$api_sa" \
    --set-env-vars="AGENT_RUNTIME_PROJECT_ID=${project_id},AGENT_RUNTIME_RESOURCE_ID=${configured_agent_resource}" \
    --command=caffemate-api --args=verify-agent-runtime-iam \
    --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=2m \
    --cpu=1 --memory=512Mi \
    --labels="source-revision=${source_revision},managed-by=caffemate-verify" \
    --quiet >/dev/null
}
if gcloud run jobs describe "$agent_iam_verify_job" \
  --project="$project_id" --region="$region" >/dev/null 2>&1; then
  configure_agent_iam_verify_job update
else
  configure_agent_iam_verify_job create
fi
gcloud run jobs execute "$agent_iam_verify_job" \
  --project="$project_id" --region="$region" --wait --quiet >/dev/null
printf '%s\n' 'PASS Control API runtime identity has query-only effective access'

agent_preflight_job='caffemate-agent-runtime-control-preflight'
configure_agent_preflight_job() {
  action=$1
  gcloud run jobs "$action" "$agent_preflight_job" --project="$project_id" --region="$region" \
    --image="$api_image" --service-account="$api_sa" \
    --set-env-vars="AGENT_RUNTIME_PROJECT_ID=${project_id},AGENT_RUNTIME_RESOURCE_ID=${configured_agent_resource}" \
    --set-secrets='AGENT_RUNTIME_USER_HMAC_SECRET=caffemate-agent-runtime-user-hmac:latest' \
    --command=caffemate-api --args=verify-agent-runtime \
    --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=5m \
    --cpu=1 --memory=512Mi \
    --labels="source-revision=${source_revision},managed-by=caffemate-verify" \
    --quiet >/dev/null
}
if gcloud run jobs describe "$agent_preflight_job" --project="$project_id" --region="$region" >/dev/null 2>&1; then
  configure_agent_preflight_job update
else
  configure_agent_preflight_job create
fi
gcloud run jobs execute "$agent_preflight_job" --project="$project_id" --region="$region" \
  --wait --quiet >/dev/null
printf '%s\n' 'PASS Control API created, executed, validated and deleted an Agent Runtime session'

intent_preflight_job='caffemate-agent-runtime-intent-preflight'
configure_intent_preflight_job() {
  action=$1
  gcloud run jobs "$action" "$intent_preflight_job" --project="$project_id" --region="$region" \
    --image="$api_image" --service-account="$api_sa" \
    --set-env-vars="AGENT_RUNTIME_PROJECT_ID=${project_id},AGENT_RUNTIME_RESOURCE_ID=${configured_agent_resource}" \
    --set-secrets='AGENT_RUNTIME_USER_HMAC_SECRET=caffemate-agent-runtime-user-hmac:latest' \
    --command=caffemate-api \
    --args=verify-agent-runtime,--agent-fixture-id,intent_delta-complete,--repeat,3 \
    --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=5m \
    --cpu=1 --memory=512Mi \
    --labels="source-revision=${source_revision},managed-by=caffemate-verify" \
    --quiet >/dev/null
}
if gcloud run jobs describe "$intent_preflight_job" --project="$project_id" --region="$region" >/dev/null 2>&1; then
  configure_intent_preflight_job update
else
  configure_intent_preflight_job create
fi
intent_probe_started_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
gcloud run jobs execute "$intent_preflight_job" --project="$project_id" --region="$region" \
  --wait --quiet >/dev/null
intent_generation_logs='[]'
intent_log_attempt=0
while [ "$intent_log_attempt" -lt 12 ]; do
  intent_generation_logs=$(gcloud logging read \
    "timestamp>=\"${intent_probe_started_at}\" AND jsonPayload.event=\"VERTEX_AGENT_GENERATION\" AND jsonPayload.task_type=\"INTENT_DELTA\" AND jsonPayload.preflight=true" \
    --project="$project_id" --freshness=10m --limit=20 --format=json)
  intent_generation_count=$(printf '%s' "$intent_generation_logs" | python3 -c \
    'import json,sys; print(len(json.load(sys.stdin)))')
  [ "$intent_generation_count" -ge 3 ] && break
  intent_log_attempt=$((intent_log_attempt + 1))
  sleep 5
done
INTENT_GENERATION_LOGS="$intent_generation_logs" python3 - <<'PY'
import json
import os

rows = json.loads(os.environ["INTENT_GENERATION_LOGS"])
assert len(rows) == 3, f"expected exactly three INTENT_DELTA generations, got {len(rows)}"
for row in rows:
    payload = row.get("jsonPayload", {})
    assert payload.get("http_status") == 200, payload
    assert payload.get("finish_reason") == "STOP", payload
    assert payload.get("repair_attempt") == 0, payload
PY
unset intent_generation_logs
printf '%s\n' 'PASS INTENT_DELTA completed three managed Agent Runtime sessions without repair'

mcp_release_service_name=$(python3 -c \
  'import json; print(json.load(open("agents/release-manifest.json"))["mcp"]["runtime"]["service_name"])')
mcp_release_region=$(python3 -c \
  'import json; print(json.load(open("agents/release-manifest.json"))["mcp"]["runtime"]["region"])')
mcp_release_source_revision=$(python3 -c \
  'import json; print(json.load(open("agents/release-manifest.json"))["mcp"]["runtime"]["source_revision"])')
mcp_release_image=$(python3 -c \
  'import json; print(json.load(open("agents/release-manifest.json"))["mcp"]["runtime"]["image_uri"])')
[ "$mcp_release_service_name" = 'caffemate-mcp' ] && [ "$mcp_release_region" = "$region" ] || {
  printf '%s\n' 'FAIL MCP release manifest service or region pin is invalid' >&2; exit 1;
}
[ "${#mcp_release_source_revision}" -eq 40 ] || {
  printf '%s\n' 'FAIL MCP release manifest source revision is invalid' >&2; exit 1;
}
case "$mcp_release_source_revision" in
  *[!0-9a-f]*) printf '%s\n' 'FAIL MCP release manifest source revision is invalid' >&2; exit 1 ;;
esac
case "$mcp_release_image" in
  "${region}-docker.pkg.dev/${project_id}/caffemate-backend/mcp@sha256:"*) ;;
  *) printf '%s\n' 'FAIL MCP release manifest image pin is invalid' >&2; exit 1 ;;
esac

mcp_service_json=$(gcloud run services describe "$mcp_release_service_name" --project="$project_id" \
  --region="$region" --format=json)
mcp_url=$(printf '%s' "$mcp_service_json" | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["status"]["url"])')
mcp_image=$(printf '%s' "$mcp_service_json" | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0]["image"])')
mcp_source_revision=$(printf '%s' "$mcp_service_json" | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["spec"]["template"]["metadata"]["labels"]["source-revision"])')
mcp_build_id=$(printf '%s' "$mcp_service_json" | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["spec"]["template"]["metadata"]["labels"]["build-id"])')
[ "$mcp_source_revision" = "$mcp_release_source_revision" ] || {
  printf '%s\n' 'FAIL deployed MCP source differs from release manifest pin' >&2; exit 1;
}
[ "$mcp_image" = "$mcp_release_image" ] || {
  printf '%s\n' 'FAIL deployed MCP image differs from release manifest pin' >&2; exit 1;
}
mcp_tagged_image="${region}-docker.pkg.dev/${project_id}/caffemate-backend/mcp:${mcp_release_source_revision}"
mcp_tagged_digest=$(gcloud artifacts docker images describe "$mcp_tagged_image" \
  --project="$project_id" --format='value(image_summary.fully_qualified_digest)')
[ "$mcp_tagged_digest" = "$mcp_release_image" ] || {
  printf '%s\n' 'FAIL MCP producer image digest differs from pinned source tag' >&2; exit 1;
}
mcp_verified_build_id=$(verified_build_id_for_image \
  "$mcp_tagged_image" "${mcp_release_image##*@}" "$mcp_release_source_revision" \
  "projects/${project_id}/serviceAccounts/caffemate-backend-build@${project_id}.iam.gserviceaccount.com")
[ "$mcp_build_id" = "$mcp_verified_build_id" ] || {
  printf '%s\n' 'FAIL MCP producer build provenance differs from deployed label' >&2; exit 1;
}
printf '%s\n' 'PASS deployed MCP source, image and build match release manifest pin'

agent_release_preflight_tag="${region}-docker.pkg.dev/${project_id}/caffemate-backend/agent-release-preflight:${source_revision}"
agent_release_preflight_image=$(gcloud artifacts docker images describe "$agent_release_preflight_tag" \
  --project="$project_id" --format='value(image_summary.fully_qualified_digest)')
case "$agent_release_preflight_image" in
  "${region}-docker.pkg.dev/${project_id}/caffemate-backend/agent-release-preflight@sha256:"*) ;;
  *) printf '%s\n' 'FAIL Agent release-preflight image digest is unavailable' >&2; exit 1 ;;
esac
verified_build_id_for_image \
  "$agent_release_preflight_tag" "${agent_release_preflight_image##*@}" "$source_revision" \
  "projects/${project_id}/serviceAccounts/caffemate-backend-build@${project_id}.iam.gserviceaccount.com" >/dev/null
printf '%s\n' 'PASS Agent release-preflight artifact matches current release source provenance'
configured_mcp_url=$(printf '%s' "$api_service_json" | python3 -c \
  'import json,sys; print(next(row["value"] for row in json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0]["env"] if row["name"] == "MCP_BASE_URL"))')
configured_mcp_audience=$(printf '%s' "$api_service_json" | python3 -c \
  'import json,sys; print(next(row["value"] for row in json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0]["env"] if row["name"] == "MCP_AUDIENCE"))')
[ "$configured_mcp_url" = "$mcp_url" ] && [ "$configured_mcp_audience" = "$mcp_url" ] || {
  printf '%s\n' 'FAIL Control API MCP URL or audience differs from deployed MCP' >&2; exit 1;
}

agent_gcp_preflight_job='caffemate-agent-gcp-release-preflight'
configure_agent_gcp_preflight_job() {
  action=$1
  gcloud run jobs "$action" "$agent_gcp_preflight_job" \
    --project="$project_id" --region="$region" \
    --image="$agent_release_preflight_image" --service-account="$release_verifier_sa" \
    --command=node \
    --args='--import,tsx,agents/src/control-cli.ts,gcp-preflight,--json' \
    --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=5m \
    --cpu=1 --memory=512Mi \
    --labels="source-revision=${source_revision},managed-by=caffemate-verify" \
    --quiet >/dev/null
}
if gcloud run jobs describe "$agent_gcp_preflight_job" \
  --project="$project_id" --region="$region" >/dev/null 2>&1; then
  configure_agent_gcp_preflight_job update
else
  configure_agent_gcp_preflight_job create
fi
gcloud run jobs execute "$agent_gcp_preflight_job" \
  --project="$project_id" --region="$region" --wait --quiet >/dev/null
printf '%s\n' 'PASS shared Agent GCP release preflight'

preflight_job='caffemate-mcp-control-preflight'
configure_preflight_job() {
  action=$1
  gcloud run jobs "$action" "$preflight_job" --project="$project_id" --region="$region" \
    --image="$api_image" --service-account="$api_sa" \
    --set-env-vars="MCP_BASE_URL=${mcp_url},MCP_AUDIENCE=${mcp_url},CAFFEMATE_POLICY_SNAPSHOT_ID=policy-v1" \
    --set-secrets='MCP_SCOPE_HMAC_SECRET=caffemate-mcp-scope-hmac:latest' \
    --command=caffemate-api --args=verify-mcp-preflight \
    --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=2m \
    --cpu=1 --memory=512Mi \
    --labels="source-revision=${source_revision},managed-by=caffemate-verify" \
    --quiet >/dev/null
}
if gcloud run jobs describe "$preflight_job" --project="$project_id" --region="$region" >/dev/null 2>&1; then
  configure_preflight_job update
else
  configure_preflight_job create
fi
gcloud run jobs execute "$preflight_job" --project="$project_id" --region="$region" \
  --wait --quiet >/dev/null
printf '%s\n' 'PASS Control API SDK manifest preflight against deployed MCP'

configured_instance=$(printf '%s' "$api_service_json" | python3 -c \
  'import json,sys; env={row["name"]:row.get("value") for row in json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0]["env"]}; print(env["INSTANCE_CONNECTION_NAME"])')
configured_db_user=$(printf '%s' "$api_service_json" | python3 -c \
  'import json,sys; env={row["name"]:row.get("value") for row in json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0]["env"]}; print(env["DB_USER"])')
configured_db_name=$(printf '%s' "$api_service_json" | python3 -c \
  'import json,sys; env={row["name"]:row.get("value") for row in json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0]["env"]}; print(env["DB_NAME"])')
configured_db_ip_type=$(printf '%s' "$api_service_json" | python3 -c \
  'import json,sys; env={row["name"]:row.get("value") for row in json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0]["env"]}; print(env["CLOUD_SQL_IP_TYPE"])')
configured_policy=$(printf '%s' "$api_service_json" | python3 -c \
  'import json,sys; env={row["name"]:row.get("value") for row in json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0]["env"]}; print(env["CAFFEMATE_POLICY_SNAPSHOT_ID"])')

first_proposal_job='caffemate-first-proposal-canary'
configure_first_proposal_job() {
  action=$1
  gcloud run jobs "$action" "$first_proposal_job" \
    --project="$project_id" --region="$region" \
    --image="$api_image" --service-account="$api_sa" \
    --set-cloudsql-instances="$configured_instance" \
    --set-env-vars="INSTANCE_CONNECTION_NAME=${configured_instance},DB_USER=${configured_db_user},DB_NAME=${configured_db_name},CLOUD_SQL_IP_TYPE=${configured_db_ip_type},MCP_BASE_URL=${mcp_url},MCP_AUDIENCE=${mcp_url},CAFFEMATE_POLICY_SNAPSHOT_ID=${configured_policy}" \
    --set-secrets='DB_PASS=caffemate-db-password:latest,MCP_SCOPE_HMAC_SECRET=caffemate-mcp-scope-hmac:latest' \
    --command=caffemate-api \
    --args='verify-first-proposal,--timeout-seconds=1200,--poll-interval-seconds=3' \
    --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=25m \
    --cpu=1 --memory=512Mi \
    --labels="source-revision=${source_revision},managed-by=caffemate-verify" \
    --quiet >/dev/null
}
if gcloud run jobs describe "$first_proposal_job" \
  --project="$project_id" --region="$region" >/dev/null 2>&1; then
  configure_first_proposal_job update
else
  configure_first_proposal_job create
fi

canary_started_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
canary_status_file=$(mktemp)
canary_wait_log=$(mktemp)
cleanup_canary_files() {
  rm -f "$canary_status_file" "$canary_wait_log"
}
trap cleanup_canary_files EXIT HUP INT TERM
(
  if gcloud run jobs execute "$first_proposal_job" \
    --project="$project_id" --region="$region" --wait --quiet >"$canary_wait_log" 2>&1; then
    printf '0\n' >"$canary_status_file"
  else
    printf '%s\n' "$?" >"$canary_status_file"
  fi
) &
canary_wait_pid=$!
canary_attempt=0
while [ ! -s "$canary_status_file" ] && [ "$canary_attempt" -lt 240 ]; do
  gcloud scheduler jobs run caffemate-outbox-drain \
    --project="$project_id" --location="$region" --quiet >/dev/null
  canary_attempt=$((canary_attempt + 1))
  sleep 5
done
wait "$canary_wait_pid" || true
canary_exit=$(cat "$canary_status_file")
if [ "$canary_exit" != '0' ]; then
  sed -n '1,120p' "$canary_wait_log" >&2
  printf '%s\n' 'FAIL FIRST_PROPOSAL canary Cloud Run Job' >&2
  exit 1
fi
canary_reports='[]'
canary_log_attempt=0
while [ "$canary_log_attempt" -lt 12 ]; do
  canary_reports=$(gcloud logging read \
    "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"${first_proposal_job}\" AND timestamp>=\"${canary_started_at}\" AND jsonPayload.status=\"verified\"" \
    --project="$project_id" --limit=1 --order=desc --format=json)
  canary_report_count=$(printf '%s' "$canary_reports" | python3 -c \
    'import json,sys; print(len(json.load(sys.stdin)))')
  [ "$canary_report_count" -ge 1 ] && break
  canary_log_attempt=$((canary_log_attempt + 1))
  sleep 5
done
CANARY_REPORTS="$canary_reports" python3 - <<'PY'
import json
import os

rows = json.loads(os.environ["CANARY_REPORTS"])
assert len(rows) == 1, f"expected one FIRST_PROPOSAL canary report, got {len(rows)}"
report = rows[0]["jsonPayload"]
assert report["status"] == "verified"
assert report["workflow_status"] == "SUCCEEDED"
assert report["stage_count"] == 13
assert report["max_stage_attempt"] == 1
assert report["elapsed_ms"] <= 120_000, report
assert report["candidate_count"] >= 1
assert report["result_freshness"] == "CURRENT"
print("PASS FIRST_PROPOSAL traversed all 13 production stages to a current result card")
PY
unset canary_reports

first_proposal_generation_logs='[]'
first_proposal_validation_logs='[]'
agent_log_attempt=0
while [ "$agent_log_attempt" -lt 12 ]; do
  first_proposal_generation_logs=$(gcloud logging read \
    "timestamp>=\"${canary_started_at}\" AND jsonPayload.event=\"VERTEX_AGENT_GENERATION\" AND (jsonPayload.task_type=\"EVIDENCE_ASSESS\" OR jsonPayload.task_type=\"PROPOSE_INDEPENDENT\" OR jsonPayload.task_type=\"CANDIDATE_AUDIT\")" \
    --project="$project_id" --freshness=10m --limit=30 --format=json)
  first_proposal_validation_logs=$(gcloud logging read \
    "timestamp>=\"${canary_started_at}\" AND jsonPayload.event=\"AGENT_RESULT_VALIDATION\" AND (jsonPayload.task_type=\"EVIDENCE_ASSESS\" OR jsonPayload.task_type=\"PROPOSE_INDEPENDENT\" OR jsonPayload.task_type=\"CANDIDATE_AUDIT\")" \
    --project="$project_id" --freshness=10m --limit=30 --format=json)
  observed_agent_types=$(FIRST_PROPOSAL_GENERATION_LOGS="$first_proposal_generation_logs" \
    FIRST_PROPOSAL_VALIDATION_LOGS="$first_proposal_validation_logs" python3 - <<'PY'
import json
import os

required = {"EVIDENCE_ASSESS", "PROPOSE_INDEPENDENT", "CANDIDATE_AUDIT"}
generations = {
    row.get("jsonPayload", {}).get("task_type")
    for row in json.loads(os.environ["FIRST_PROPOSAL_GENERATION_LOGS"])
}
validations = {
    row.get("jsonPayload", {}).get("task_type")
    for row in json.loads(os.environ["FIRST_PROPOSAL_VALIDATION_LOGS"])
}
print("ready" if required <= generations and required <= validations else "waiting")
PY
  )
  [ "$observed_agent_types" = 'ready' ] && break
  agent_log_attempt=$((agent_log_attempt + 1))
  sleep 5
done
FIRST_PROPOSAL_GENERATION_LOGS="$first_proposal_generation_logs" \
FIRST_PROPOSAL_VALIDATION_LOGS="$first_proposal_validation_logs" python3 - <<'PY'
import json
import os

required = {"EVIDENCE_ASSESS", "PROPOSE_INDEPENDENT", "CANDIDATE_AUDIT"}
token_budgets = {
    "EVIDENCE_ASSESS": 6_000,
    "PROPOSE_INDEPENDENT": 4_500,
    "CANDIDATE_AUDIT": 6_500,
}
generation_rows = [
    row.get("jsonPayload", {})
    for row in json.loads(os.environ["FIRST_PROPOSAL_GENERATION_LOGS"])
]
validation_rows = [
    row.get("jsonPayload", {})
    for row in json.loads(os.environ["FIRST_PROPOSAL_VALIDATION_LOGS"])
]
observed_generations = {row.get("task_type") for row in generation_rows}
observed_validations = {row.get("task_type") for row in validation_rows}
assert required <= observed_generations, (
    f"missing Agent generations: {sorted(required - observed_generations)}"
)
assert required <= observed_validations, (
    f"missing Agent validations: {sorted(required - observed_validations)}"
)
for row in generation_rows:
    task_type = row.get("task_type")
    if task_type not in required:
        continue
    assert row.get("http_status") == 200, row
    assert row.get("finish_reason") == "STOP", row
    assert row.get("repair_attempt") == 0, row
    assert 0 <= row.get("elapsed_ms", -1) <= 60_000, row
    assert 0 < row.get("prompt_token_count", 0) <= token_budgets[task_type], row
for row in validation_rows:
    if row.get("task_type") not in required:
        continue
    assert row.get("repair_attempt") == 0, row
    assert row.get("outcome") == "VALID", row
    if row.get("task_type") == "PROPOSE_INDEPENDENT":
        assert row.get("candidate_proposal_count", 0) >= 1, row
    if row.get("task_type") == "CANDIDATE_AUDIT":
        assert row.get("candidate_audit_count", 0) >= 1, row
print("PASS FIRST_PROPOSAL managed Agents stopped within budget without repair")
PY
unset first_proposal_generation_logs first_proposal_validation_logs observed_agent_types
cleanup_canary_files
trap - EXIT HUP INT TERM

api_public=$(gcloud run services get-iam-policy caffemate-api \
  --project="$project_id" --region="$region" \
  --flatten='bindings[].members' \
  --filter='bindings.role=roles/run.invoker AND bindings.members=allUsers' \
  --format='value(bindings.role)')
[ "$api_public" = 'roles/run.invoker' ] || {
  printf '%s\n' 'FAIL API public invoker policy' >&2; exit 1;
}

worker_public=$(gcloud run services get-iam-policy caffemate-worker \
  --project="$project_id" --region="$region" \
  --flatten='bindings[].members' \
  --filter='bindings.role=roles/run.invoker AND bindings.members=allUsers' \
  --format='value(bindings.role)')
[ -z "$worker_public" ] || {
  printf '%s\n' 'FAIL Worker has public invoker policy' >&2; exit 1;
}
printf '%s\n' 'PASS API is public at Cloud Run IAM and Worker is not public'

curl --fail --silent --output /dev/null "${api_url}/health"
printf '%s\n' 'PASS API health returned HTTP 200'

api_status=$(curl --silent --output /dev/null --write-out '%{http_code}' "${api_url}/v1/projects")
[ "$api_status" = '401' ] || { printf 'FAIL API unauthenticated status %s\n' "$api_status" >&2; exit 1; }
printf '%s\n' 'PASS API unauthenticated business request returned HTTP 401'

worker_status=$(curl --silent --output /dev/null --write-out '%{http_code}' "${worker_url}/health")
[ "$worker_status" = '403' ] || [ "$worker_status" = '404' ] || {
  printf 'FAIL Worker unauthenticated status %s\n' "$worker_status" >&2; exit 1;
}
printf 'PASS Worker unauthenticated internet request rejected with HTTP %s\n' "$worker_status"

subscription_json=$(gcloud pubsub subscriptions describe caffemate-workflow-stage-worker \
  --project="$project_id" --format=json)
SUBSCRIPTION_JSON="$subscription_json" WORKER_URL="$worker_url" PROJECT_ID="$project_id" python3 - <<'PY'
import json, os
s = json.loads(os.environ["SUBSCRIPTION_JSON"])
push = s["pushConfig"]
assert s["topic"] == f"projects/{os.environ['PROJECT_ID']}/topics/caffemate-workflow-stage-ready"
assert push["pushEndpoint"] == os.environ["WORKER_URL"] + "/internal/v1/pubsub/workflow-stages"
assert push["oidcToken"]["audience"] == os.environ["WORKER_URL"]
assert push["oidcToken"]["serviceAccountEmail"].startswith("caffemate-pubsub-push@")
print("PASS authenticated Pub/Sub push configuration")
PY

scheduler_state=$(gcloud scheduler jobs describe caffemate-outbox-drain \
  --project="$project_id" --location="$region" --format='value(state)')
[ "$scheduler_state" = 'ENABLED' ] || { printf 'FAIL Scheduler state %s\n' "$scheduler_state" >&2; exit 1; }
printf '%s\n' 'PASS outbox Scheduler is enabled'

verification_started_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
gcloud scheduler jobs run caffemate-outbox-drain \
  --project="$project_id" \
  --location="$region" \
  --quiet >/dev/null

attempt=0
while [ "$attempt" -lt 12 ]; do
  internal_status=$(gcloud logging read \
    "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"caffemate-worker\" AND timestamp>=\"${verification_started_at}\" AND httpRequest.requestMethod=\"POST\" AND httpRequest.requestUrl:\"/internal/v1/outbox\" AND httpRequest.status=200" \
    --project="$project_id" \
    --limit=1 \
    --format='value(httpRequest.status)')
  if [ "$internal_status" = '200' ]; then
    printf '%s\n' 'PASS Scheduler reached internal Worker with HTTP 200'
    break
  fi
  attempt=$((attempt + 1))
  sleep 5
done
[ "$internal_status" = '200' ] || {
  printf '%s\n' 'FAIL Scheduler did not reach internal Worker with HTTP 200' >&2
  exit 1
}

printf '%s\n' 'API and Worker runtime verification passed'
