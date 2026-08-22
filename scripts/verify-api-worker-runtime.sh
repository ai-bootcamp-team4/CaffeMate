#!/bin/sh
set -eu

project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
region=${CAFFEMATE_GCP_REGION:-asia-northeast3}
source_revision=${CAFFEMATE_SOURCE_REVISION:-}

if [ -z "$project_id" ] || [ -z "$source_revision" ]; then
  printf '%s\n' 'CAFFEMATE_GCP_PROJECT_ID and CAFFEMATE_SOURCE_REVISION are required' >&2
  exit 2
fi

api_url=$(gcloud run services describe caffemate-api --project="$project_id" \
  --region="$region" --format='value(status.url)')
worker_url=$(gcloud run services describe caffemate-worker --project="$project_id" \
  --region="$region" --format='value(status.url)')
api_sa="caffemate-api-runtime@${project_id}.iam.gserviceaccount.com"

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
case "$agent_runtime_identity" in
  principal://agents.*) ;;
  agents.*) agent_runtime_identity="principal://${agent_runtime_identity}" ;;
  *) printf '%s\n' 'FAIL Agent Runtime effective identity is unavailable' >&2; exit 1 ;;
esac
project_policy=$(gcloud projects get-iam-policy "$project_id" --format=json)
PROJECT_POLICY="$project_policy" AGENT_RUNTIME_IDENTITY="$agent_runtime_identity" python3 - <<'PY'
import json
import os

policy = json.loads(os.environ["PROJECT_POLICY"])
identity = os.environ["AGENT_RUNTIME_IDENTITY"]
roles = {
    row["role"]
    for row in policy.get("bindings", [])
    if identity in row.get("members", [])
}
required = {"roles/aiplatform.expressUser", "roles/serviceusage.serviceUsageConsumer"}
assert required <= roles, f"Agent Runtime identity lacks roles: {sorted(required - roles)}"
print("PASS Agent Runtime identity has model and service usage permissions")
PY
agent_runtime_policy=$(curl --fail --silent --show-error --request POST \
  --header "Authorization: Bearer ${access_token}" \
  --header 'Content-Type: application/json' \
  "${agent_runtime_url}:getIamPolicy" --data='{}')
AGENT_RUNTIME_POLICY="$agent_runtime_policy" API_SERVICE_ACCOUNT="$api_sa" python3 - <<'PY'
import json
import os

policy = json.loads(os.environ["AGENT_RUNTIME_POLICY"])
member = f"serviceAccount:{os.environ['API_SERVICE_ACCOUNT']}"
assert any(
    row.get("role") == "roles/aiplatform.user" and member in row.get("members", [])
    for row in policy.get("bindings", [])
), "Control API lacks Agent Runtime query IAM"
print("PASS Control API has resource-scoped Agent Runtime query IAM")
PY
unset access_token agent_runtime_identity agent_runtime_json agent_runtime_policy project_policy

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

mcp_url=$(gcloud run services describe caffemate-mcp --project="$project_id" \
  --region="$region" --format='value(status.url)')
configured_mcp_url=$(printf '%s' "$api_service_json" | python3 -c \
  'import json,sys; print(next(row["value"] for row in json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0]["env"] if row["name"] == "MCP_BASE_URL"))')
configured_mcp_audience=$(printf '%s' "$api_service_json" | python3 -c \
  'import json,sys; print(next(row["value"] for row in json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0]["env"] if row["name"] == "MCP_AUDIENCE"))')
[ "$configured_mcp_url" = "$mcp_url" ] && [ "$configured_mcp_audience" = "$mcp_url" ] || {
  printf '%s\n' 'FAIL Control API MCP URL or audience differs from deployed MCP' >&2; exit 1;
}

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
