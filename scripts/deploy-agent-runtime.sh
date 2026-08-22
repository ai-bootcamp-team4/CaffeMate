#!/bin/sh
set -eu

project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
region=${CAFFEMATE_GCP_REGION:-asia-northeast3}
source_revision=${CAFFEMATE_SOURCE_REVISION:-}

. "$(dirname "$0")/iam-role-helpers.sh"
. "$(dirname "$0")/build-provenance-helpers.sh"

if [ -z "$project_id" ] || [ "$region" != 'asia-northeast3' ] || [ "${#source_revision}" -ne 40 ]; then
  printf '%s\n' 'project, Seoul region and full source revision are required' >&2
  exit 2
fi
case "$source_revision" in *[!0-9a-f]*) printf '%s\n' 'source revision must be lowercase hexadecimal' >&2; exit 2;; esac
[ "$(gcloud config get-value project 2>/dev/null)" = "$project_id" ] || {
  printf '%s\n' 'active gcloud project does not match requested project' >&2
  exit 2
}
[ "$(git rev-parse HEAD)" = "$source_revision" ] || {
  printf '%s\n' 'requested Agent Runtime source revision differs from checked-out HEAD' >&2
  exit 2
}
[ -z "$(git status --porcelain)" ] || {
  printf '%s\n' 'Agent Runtime deployment requires a clean source checkout' >&2
  exit 2
}
remote_main=$(git ls-remote origin refs/heads/main | awk '{print $1}')
[ "$remote_main" = "$source_revision" ] || {
  printf '%s\n' 'Agent Runtime deployment source must be the immutable origin/main revision' >&2
  exit 2
}

tagged_image="${region}-docker.pkg.dev/${project_id}/caffemate-agents/caffemate-agent-runtime:${source_revision}"
approved_tag="${region}-docker.pkg.dev/${project_id}/caffemate-agents/caffemate-agent-runtime:approved-${source_revision}"
build_sa="projects/${project_id}/serviceAccounts/caffemate-backend-build@${project_id}.iam.gserviceaccount.com"
image=$(gcloud artifacts docker images describe "$approved_tag" \
  --project="$project_id" \
  --format='value(image_summary.fully_qualified_digest)')
case "$image" in
  "${region}-docker.pkg.dev/${project_id}/caffemate-agents/caffemate-agent-runtime@sha256:"*) ;;
  *) printf '%s\n' 'approved Agent Runtime image digest is unavailable' >&2; exit 1 ;;
esac
digest=${image##*@}
build_id=$(verified_build_id_for_image \
  "$tagged_image" "$digest" "$source_revision" "$build_sa")

manifest_resource=$(python3 - <<'PY'
import json
manifest = json.load(open("agents/release-manifest.json"))
print(manifest["runtime"]["resource_name"])
PY
)
case "$manifest_resource" in
  "projects/${project_id}/locations/${region}/reasoningEngines/"*) ;;
  *) printf '%s\n' 'release manifest Runtime resource is outside the requested project or region' >&2; exit 1 ;;
esac

runtime_spec=$(npm run --silent agent:control -- runtime-spec --json)
request_body=$(RUNTIME_SPEC="$runtime_spec" IMAGE_URI="$image" SOURCE_REVISION="$source_revision" BUILD_ID="$build_id" python3 - <<'PY'
import json
import os

runtime = json.loads(os.environ["RUNTIME_SPEC"])
assert runtime["ok"] is True
data = runtime["data"]
print(json.dumps({
    "displayName": data["appName"],
    "description": "CaffeMate deterministic ADK multi-agent runtime; generation uses approved global gemini-3.7-flash.",
    "labels": {
        "app": "caffemate",
        "component": "agent-runtime",
        "build-id": os.environ["BUILD_ID"],
        "git-sha": os.environ["SOURCE_REVISION"],
    },
    "spec": {
        "classMethods": data["classMethods"],
        "deploymentSpec": {
            "env": [{"name": "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY", "value": "true"}],
            "minInstances": 1,
            "maxInstances": 10,
            "resourceLimits": {"cpu": "1", "memory": "2Gi"},
            "containerConcurrency": 9,
        },
        "agentFramework": "google-adk",
        "identityType": "AGENT_IDENTITY",
        "containerSpec": {"imageUri": os.environ["IMAGE_URI"]},
    },
}, separators=(",", ":")))
PY
)

access_token=$(gcloud auth print-access-token)
runtime_url="https://${region}-aiplatform.googleapis.com/v1/${manifest_resource}"
runtime_response=$(mktemp)
trap 'rm -f "$runtime_response"' EXIT
runtime_http=$(curl --silent --show-error --connect-timeout 10 --max-time 30 \
  --output "$runtime_response" --write-out '%{http_code}' \
  --header "Authorization: Bearer ${access_token}" "$runtime_url")
if [ "$runtime_http" = '200' ]; then
  operation=$(curl --fail --silent --show-error --request PATCH \
    --connect-timeout 10 --max-time 60 \
    --header "Authorization: Bearer ${access_token}" \
    --header 'Content-Type: application/json' \
    "${runtime_url}?updateMask=description,labels,spec.classMethods,spec.deploymentSpec,spec.agentFramework,spec.identityType,spec.containerSpec.imageUri" \
    --data "$request_body")
elif [ "$runtime_http" = '404' ]; then
  printf '%s\n' 'pinned Agent Runtime is absent; bootstrap must create and approve a new manifest resource before release' >&2
  exit 1
else
  printf 'pinned Agent Runtime GET failed with HTTP %s\n' "$runtime_http" >&2
  exit 1
fi
operation_name=$(printf '%s' "$operation" | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')

attempt=0
while [ "$attempt" -lt 120 ]; do
  operation=$(curl --fail --silent --show-error \
    --connect-timeout 10 --max-time 30 \
    --header "Authorization: Bearer ${access_token}" \
    "https://${region}-aiplatform.googleapis.com/v1/${operation_name}")
  done_value=$(printf '%s' "$operation" | python3 -c 'import json,sys; print(str(json.load(sys.stdin).get("done", False)).lower())')
  if [ "$done_value" = 'true' ]; then break; fi
  attempt=$((attempt + 1))
  sleep 5
done
[ "$done_value" = 'true' ] || { printf '%s\n' 'Agent Runtime deployment operation timed out' >&2; exit 1; }
OPERATION_JSON="$operation" python3 - <<'PY'
import json
import os
operation = json.loads(os.environ["OPERATION_JSON"])
assert "error" not in operation, operation.get("error")
assert operation.get("response", {}).get("name"), "operation has no deployed Runtime response"
print(f"Agent Runtime deployed: {operation['response']['name']}")
PY

runtime=$(curl --fail --silent --show-error \
  --connect-timeout 10 --max-time 30 \
  --header "Authorization: Bearer ${access_token}" "$runtime_url")
agent_runtime_identity=$(printf '%s' "$runtime" | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["spec"]["effectiveIdentity"])')
case "$agent_runtime_identity" in
  principal://agents.*) ;;
  agents.*) agent_runtime_identity="principal://${agent_runtime_identity}" ;;
  *) printf '%s\n' 'Agent Runtime effective identity is unavailable after deployment' >&2; exit 1 ;;
esac
session_manager_role_id='caffemateAgentSessionManager'
session_manager_role="projects/${project_id}/roles/${session_manager_role_id}"
ensure_project_custom_role \
  "$session_manager_role_id" \
  'CaffeMate Agent Session Manager' \
  'Manage only the ephemeral sessions and events beneath the pinned CaffeMate Runtime.' \
  'aiplatform.sessionEvents.append,aiplatform.sessionEvents.list,aiplatform.sessions.create,aiplatform.sessions.delete,aiplatform.sessions.get,aiplatform.sessions.list,aiplatform.sessions.update'
remove_project_role_binding "$agent_runtime_identity" 'roles/aiplatform.expressUser'
remove_project_role_binding "$agent_runtime_identity" 'roles/serviceusage.serviceUsageConsumer'
remove_project_role_binding "$agent_runtime_identity" "projects/${project_id}/roles/caffemateAgentModelInvoker"

runtime_policy=$(curl --fail --silent --show-error --request POST \
  --connect-timeout 10 --max-time 30 \
  --header "Authorization: Bearer ${access_token}" \
  --header 'Content-Type: application/json' \
  "${runtime_url}:getIamPolicy" --data '{}')
runtime_policy=$(RUNTIME_POLICY="$runtime_policy" AGENT_RUNTIME_IDENTITY="$agent_runtime_identity" AGENT_SESSION_MANAGER_ROLE="$session_manager_role" python3 - <<'PY'
import json
import os

policy = json.loads(os.environ["RUNTIME_POLICY"])
identity = os.environ["AGENT_RUNTIME_IDENTITY"]
role = os.environ["AGENT_SESSION_MANAGER_ROLE"]
for row in policy.get("bindings", []):
    if row.get("role") in {
        "roles/aiplatform.agentContextEditor",
        "roles/aiplatform.user",
    }:
        row["members"] = [value for value in row.get("members", []) if value != identity]
policy["bindings"] = [row for row in policy.get("bindings", []) if row.get("members")]
binding = next((row for row in policy.get("bindings", []) if row.get("role") == role), None)
if binding is None:
    binding = {"role": role, "members": []}
    policy.setdefault("bindings", []).append(binding)
if identity not in binding["members"]:
    binding["members"].append(identity)
binding["members"].sort()
print(json.dumps({"policy": policy}, separators=(",", ":")))
PY
)
curl --fail --silent --show-error --request POST \
  --connect-timeout 10 --max-time 30 \
  --header "Authorization: Bearer ${access_token}" \
  --header 'Content-Type: application/json' \
  "${runtime_url}:setIamPolicy" --data "$runtime_policy" >/dev/null

CAFFEMATE_GCP_PROJECT_ID="$project_id" \
CAFFEMATE_GCP_REGION="$region" \
CAFFEMATE_SOURCE_REVISION="$source_revision" \
  ./scripts/verify-agent-runtime-deployment.sh
