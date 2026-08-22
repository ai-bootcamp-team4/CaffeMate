#!/bin/sh
set -eu

project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
region=${CAFFEMATE_GCP_REGION:-asia-northeast3}
source_revision=${CAFFEMATE_SOURCE_REVISION:-}

. "$(dirname "$0")/build-provenance-helpers.sh"

if [ -z "$project_id" ] || [ "$region" != 'asia-northeast3' ] || [ "${#source_revision}" -ne 40 ]; then
  printf '%s\n' 'project, Seoul region and full source revision are required' >&2
  exit 2
fi

runtime_resource=$(python3 - <<'PY'
import json
manifest = json.load(open("agents/release-manifest.json"))
print(manifest["runtime"]["resource_name"])
PY
)
tagged_image="${region}-docker.pkg.dev/${project_id}/caffemate-agents/caffemate-agent-runtime:${source_revision}"
approved_tag="${region}-docker.pkg.dev/${project_id}/caffemate-agents/caffemate-agent-runtime:approved-${source_revision}"
build_sa="projects/${project_id}/serviceAccounts/caffemate-backend-build@${project_id}.iam.gserviceaccount.com"
built_image=$(gcloud artifacts docker images describe "$tagged_image" \
  --project="$project_id" --format='value(image_summary.fully_qualified_digest)')
approved_image=$(gcloud artifacts docker images describe "$approved_tag" \
  --project="$project_id" --format='value(image_summary.fully_qualified_digest)')
[ "$built_image" = "$approved_image" ] || {
  printf '%s\n' 'FAIL Runtime build digest lacks matching approval artifact' >&2
  exit 1
}
digest=${built_image##*@}
build_id=$(verified_build_id_for_image \
  "$tagged_image" "$digest" "$source_revision" "$build_sa")

access_token=$(gcloud auth print-access-token)
runtime=$(curl --fail --silent --show-error \
  --connect-timeout 10 --max-time 30 \
  --header "Authorization: Bearer ${access_token}" \
  "https://${region}-aiplatform.googleapis.com/v1/${runtime_resource}")
runtime_policy=$(curl --fail --silent --show-error --request POST \
  --connect-timeout 10 --max-time 30 \
  --header "Authorization: Bearer ${access_token}" \
  --header 'Content-Type: application/json' \
  "https://${region}-aiplatform.googleapis.com/v1/${runtime_resource}:getIamPolicy" \
  --data '{}')
project_policy=$(gcloud projects get-iam-policy "$project_id" --format=json)
project_number=$(gcloud projects describe "$project_id" --format='value(projectNumber)')
agent_default_role=$(gcloud iam roles describe roles/aiplatform.agentDefaultAccess \
  --format=json)
session_role=$(gcloud iam roles describe caffemateAgentSessionManager \
  --project="$project_id" --format=json)
runtime_spec=$(npm run --silent agent:control -- runtime-spec --json)
RUNTIME_JSON="$runtime" RUNTIME_POLICY="$runtime_policy" PROJECT_POLICY="$project_policy" PROJECT_NUMBER="$project_number" AGENT_DEFAULT_ROLE="$agent_default_role" SESSION_ROLE="$session_role" RUNTIME_SPEC="$runtime_spec" EXPECTED_IMAGE="$built_image" SOURCE_REVISION="$source_revision" BUILD_ID="$build_id" PROJECT_ID="$project_id" python3 - <<'PY'
import json
import os

runtime = json.loads(os.environ["RUNTIME_JSON"])
runtime_policy = json.loads(os.environ["RUNTIME_POLICY"])
project_policy = json.loads(os.environ["PROJECT_POLICY"])
agent_default_role = json.loads(os.environ["AGENT_DEFAULT_ROLE"])
session_role = json.loads(os.environ["SESSION_ROLE"])
expected = json.loads(os.environ["RUNTIME_SPEC"])["data"]
assert runtime["spec"]["containerSpec"]["imageUri"] == os.environ["EXPECTED_IMAGE"]
assert runtime["spec"]["classMethods"] == expected["classMethods"]
assert runtime["spec"]["agentFramework"] == "google-adk"
assert runtime["spec"]["identityType"] == "AGENT_IDENTITY"
assert runtime["labels"]["git-sha"] == os.environ["SOURCE_REVISION"]
assert runtime["labels"]["build-id"] == os.environ["BUILD_ID"]
identity = runtime["spec"]["effectiveIdentity"]
assert identity.startswith("agents.") or identity.startswith("principal://agents.")
if not identity.startswith("principal://"):
    identity = f"principal://{identity}"
assert not any(
    permission.startswith("aiplatform.")
    and any(action in permission for action in (".create", ".delete", ".update", ".deploy"))
    for permission in agent_default_role["includedPermissions"]
)
assert set(session_role["includedPermissions"]) == {
    "aiplatform.sessionEvents.append",
    "aiplatform.sessionEvents.list",
    "aiplatform.sessions.create",
    "aiplatform.sessions.delete",
    "aiplatform.sessions.get",
    "aiplatform.sessions.list",
    "aiplatform.sessions.update",
}
direct_project_roles = {
    row["role"]
    for row in project_policy.get("bindings", [])
    if identity in row.get("members", [])
}
assert direct_project_roles == set(), direct_project_roles
platform_set = (
    "principalSet://" + identity.removeprefix("principal://").split("/resources/", 1)[0]
    + "/attribute.platformContainer/aiplatform/projects/" + os.environ["PROJECT_NUMBER"]
)
platform_roles = {
    row["role"]
    for row in project_policy.get("bindings", [])
    if platform_set in row.get("members", [])
}
assert platform_roles == {"roles/aiplatform.agentDefaultAccess"}, platform_roles
assert any(
    row.get("role") == f"projects/{os.environ['PROJECT_ID']}/roles/caffemateAgentSessionManager"
    and identity in row.get("members", [])
    for row in runtime_policy.get("bindings", [])
)
assert not any(
    row.get("role") in {"roles/aiplatform.agentContextEditor", "roles/aiplatform.user"}
    and identity in row.get("members", [])
    for row in runtime_policy.get("bindings", [])
)
print("PASS Agent Runtime source, digest, class methods and effective identity read-back")
print("PASS Agent Runtime build provenance and least-privilege IAM read-back")
PY

printf '%s\n' 'Agent Runtime deployment verification succeeded'
