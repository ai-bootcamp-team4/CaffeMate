#!/bin/sh
set -eu

project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
region=${CAFFEMATE_GCP_REGION:-asia-northeast3}
source_revision=${CAFFEMATE_SOURCE_REVISION:-}
instance_id=${CAFFEMATE_DB_INSTANCE_ID:-caffemate-postgres}
agent_runtime_resource_id=${CAFFEMATE_AGENT_RUNTIME_RESOURCE_ID:-}
document_bucket=${CAFFEMATE_DOCUMENT_BUCKET:-}

. "$(dirname "$0")/iam-role-helpers.sh"

if [ -z "$project_id" ] || [ -z "$source_revision" ] || [ -z "$agent_runtime_resource_id" ] || [ -z "$document_bucket" ]; then
  printf '%s\n' 'CAFFEMATE_GCP_PROJECT_ID, CAFFEMATE_SOURCE_REVISION, CAFFEMATE_AGENT_RUNTIME_RESOURCE_ID and CAFFEMATE_DOCUMENT_BUCKET are required' >&2
  exit 2
fi
if [ "$region" != 'asia-northeast3' ] || [ "${#source_revision}" -ne 40 ]; then
  printf '%s\n' 'canonical region and full commit SHA are required' >&2
  exit 2
fi
case "$agent_runtime_resource_id" in
  *[!0-9]*|'')
    printf '%s\n' 'Agent Runtime resource id must be numeric' >&2
    exit 2
    ;;
esac

active_project=$(gcloud config get-value project 2>/dev/null)
if [ "$active_project" != "$project_id" ]; then
  printf 'active gcloud project %s does not match requested project %s\n' \
    "$active_project" "$project_id" >&2
  exit 2
fi
gcloud services enable policytroubleshooter.googleapis.com \
  --project="$project_id" --quiet >/dev/null

tagged_image="${region}-docker.pkg.dev/${project_id}/caffemate-backend/backend:${source_revision}"
image=$(gcloud artifacts docker images describe "$tagged_image" \
  --project="$project_id" \
  --format='value(image_summary.fully_qualified_digest)')
case "$image" in
  *'@sha256:'*) ;;
  *) printf '%s\n' 'backend image digest is unavailable' >&2; exit 1 ;;
esac

instance_connection_name=$(gcloud sql instances describe "$instance_id" \
  --project="$project_id" \
  --format='value(connectionName)')
api_sa="caffemate-api-runtime@${project_id}.iam.gserviceaccount.com"
worker_sa="caffemate-worker-runtime@${project_id}.iam.gserviceaccount.com"
scheduler_sa="caffemate-scheduler@${project_id}.iam.gserviceaccount.com"
mcp_url=$(gcloud run services describe caffemate-mcp \
  --project="$project_id" --region="$region" --format='value(status.url)')
if [ -z "$mcp_url" ]; then
  printf '%s\n' 'private MCP service must be deployed before Control API' >&2
  exit 1
fi

agent_runtime_name="projects/${project_id}/locations/${region}/reasoningEngines/${agent_runtime_resource_id}"
agent_runtime_url="https://${region}-aiplatform.googleapis.com/v1/${agent_runtime_name}"
access_token=$(gcloud auth print-access-token)
agent_runtime_json=$(curl --fail --silent --show-error \
  --header "Authorization: Bearer ${access_token}" \
  "$agent_runtime_url")
agent_runtime_identity=$(printf '%s' "$agent_runtime_json" | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["spec"]["effectiveIdentity"])')
case "$agent_runtime_identity" in
  principal://agents.*) ;;
  agents.*) agent_runtime_identity="principal://${agent_runtime_identity}" ;;
  *) printf '%s\n' 'Agent Runtime effective identity is unavailable' >&2; exit 1 ;;
esac

runtime_invoker_role_id='caffemateAgentRuntimeInvoker'
runtime_invoker_role="projects/${project_id}/roles/${runtime_invoker_role_id}"
session_manager_role_id='caffemateAgentSessionManager'
session_manager_role="projects/${project_id}/roles/${session_manager_role_id}"
release_verifier_role_id='caffemateReleaseVerifier'
release_verifier_role="projects/${project_id}/roles/${release_verifier_role_id}"
release_verifier_sa="caffemate-release-verifier@${project_id}.iam.gserviceaccount.com"
document_signer_role_id='caffemateDocumentUrlSigner'
document_signer_role="projects/${project_id}/roles/${document_signer_role_id}"
document_object_role_id='caffemateDocumentObjectAccess'
document_object_role="projects/${project_id}/roles/${document_object_role_id}"
document_reader_role_id='caffemateDocumentObjectReader'
document_reader_role="projects/${project_id}/roles/${document_reader_role_id}"

ensure_project_custom_role \
  "$runtime_invoker_role_id" \
  'CaffeMate Agent Runtime Invoker' \
  'Invoke only the pinned CaffeMate Reasoning Engine.' \
  'aiplatform.reasoningEngines.query'
ensure_project_custom_role \
  "$session_manager_role_id" \
  'CaffeMate Agent Session Manager' \
  'Manage only the ephemeral sessions and events beneath the pinned CaffeMate Runtime.' \
  'aiplatform.sessionEvents.append,aiplatform.sessionEvents.list,aiplatform.sessions.create,aiplatform.sessions.delete,aiplatform.sessions.get,aiplatform.sessions.list,aiplatform.sessions.update'
ensure_project_custom_role \
  "$release_verifier_role_id" \
  'CaffeMate AI Release Verifier' \
  'Read and execute the bounded Agent, RAG, embedding and reranker release probes.' \
  'aiplatform.endpoints.predict,aiplatform.ragCorpora.get,aiplatform.ragCorpora.list,aiplatform.ragCorpora.query,aiplatform.ragFiles.get,aiplatform.ragFiles.list,aiplatform.reasoningEngines.get,aiplatform.reasoningEngines.list,aiplatform.reasoningEngines.query,discoveryengine.rankingConfigs.rank,run.services.get,storage.objects.get'
ensure_project_custom_role \
  "$document_signer_role_id" \
  'CaffeMate Document URL Signer' \
  'Sign only short-lived document upload and download URLs.' \
  'iam.serviceAccounts.signBlob'
ensure_project_custom_role \
  "$document_object_role_id" \
  'CaffeMate Document Object Access' \
  'Create, inspect, download and delete objects only inside the document bucket.' \
  'storage.objects.create,storage.objects.delete,storage.objects.get'
ensure_project_custom_role \
  "$document_reader_role_id" \
  'CaffeMate Document Object Reader' \
  'Read document objects for scanner and parser workers.' \
  'storage.objects.get'

remove_project_role_binding "$agent_runtime_identity" 'roles/aiplatform.expressUser'
remove_project_role_binding "$agent_runtime_identity" 'roles/serviceusage.serviceUsageConsumer'
remove_project_role_binding "$agent_runtime_identity" "projects/${project_id}/roles/caffemateAgentModelInvoker"
gcloud projects add-iam-policy-binding "$project_id" \
  --member="serviceAccount:${api_sa}" \
  --role='roles/serviceusage.serviceUsageConsumer' \
  --condition=None \
  --quiet >/dev/null

if ! gcloud iam service-accounts describe "$release_verifier_sa" \
  --project="$project_id" >/dev/null 2>&1; then
  gcloud iam service-accounts create caffemate-release-verifier \
    --project="$project_id" \
    --display-name='CaffeMate AI release verifier' \
    --quiet >/dev/null
fi
for role in "$release_verifier_role" roles/serviceusage.serviceUsageConsumer; do
  gcloud projects add-iam-policy-binding "$project_id" \
    --member="serviceAccount:${release_verifier_sa}" \
    --role="$role" \
    --condition=None \
    --quiet >/dev/null
done

agent_runtime_policy=$(curl --fail --silent --show-error --request POST \
  --connect-timeout 10 --max-time 30 \
  --header "Authorization: Bearer ${access_token}" \
  --header 'Content-Type: application/json' \
  "${agent_runtime_url}:getIamPolicy" \
  --data '{}')
agent_runtime_policy=$(AGENT_RUNTIME_POLICY="$agent_runtime_policy" API_SERVICE_ACCOUNT="$api_sa" WORKER_SERVICE_ACCOUNT="$worker_sa" AGENT_RUNTIME_IDENTITY="$agent_runtime_identity" AGENT_RUNTIME_INVOKER_ROLE="$runtime_invoker_role" AGENT_SESSION_MANAGER_ROLE="$session_manager_role" python3 - <<'PY'
import json
import os

policy = json.loads(os.environ["AGENT_RUNTIME_POLICY"])
members = {
    f"serviceAccount:{os.environ['API_SERVICE_ACCOUNT']}",
    f"serviceAccount:{os.environ['WORKER_SERVICE_ACCOUNT']}",
}
approved_role = os.environ["AGENT_RUNTIME_INVOKER_ROLE"]
agent_identity = os.environ["AGENT_RUNTIME_IDENTITY"]
session_role = os.environ["AGENT_SESSION_MANAGER_ROLE"]
for row in policy.get("bindings", []):
    if row.get("role") == "roles/aiplatform.user":
        row["members"] = [value for value in row.get("members", []) if value not in members]
    if row.get("role") in {
        "roles/aiplatform.agentContextEditor",
        "roles/aiplatform.user",
    }:
        row["members"] = [value for value in row.get("members", []) if value != agent_identity]
policy["bindings"] = [row for row in policy.get("bindings", []) if row.get("members")]
binding = next((row for row in policy["bindings"] if row.get("role") == approved_role), None)
if binding is None:
    binding = {"role": approved_role, "members": []}
    policy.setdefault("bindings", []).append(binding)
binding["members"] = sorted(set(binding["members"]) | members)
binding["members"].sort()
session_binding = next(
    (row for row in policy["bindings"] if row.get("role") == session_role),
    None,
)
if session_binding is None:
    session_binding = {"role": session_role, "members": []}
    policy["bindings"].append(session_binding)
if agent_identity not in session_binding["members"]:
    session_binding["members"].append(agent_identity)
session_binding["members"].sort()
print(json.dumps({"policy": policy}, separators=(",", ":")))
PY
)
agent_runtime_policy=$(curl --fail --silent --show-error --request POST \
  --connect-timeout 10 --max-time 30 \
  --header "Authorization: Bearer ${access_token}" \
  --header 'Content-Type: application/json' \
  "${agent_runtime_url}:setIamPolicy" \
  --data "$agent_runtime_policy")
AGENT_RUNTIME_POLICY="$agent_runtime_policy" API_SERVICE_ACCOUNT="$api_sa" WORKER_SERVICE_ACCOUNT="$worker_sa" AGENT_RUNTIME_IDENTITY="$agent_runtime_identity" AGENT_RUNTIME_INVOKER_ROLE="$runtime_invoker_role" AGENT_SESSION_MANAGER_ROLE="$session_manager_role" python3 - <<'PY'
import json
import os

policy = json.loads(os.environ["AGENT_RUNTIME_POLICY"])
members = {
    f"serviceAccount:{os.environ['API_SERVICE_ACCOUNT']}",
    f"serviceAccount:{os.environ['WORKER_SERVICE_ACCOUNT']}",
}
approved_role = os.environ["AGENT_RUNTIME_INVOKER_ROLE"]
agent_identity = os.environ["AGENT_RUNTIME_IDENTITY"]
session_role = os.environ["AGENT_SESSION_MANAGER_ROLE"]
assert any(
    row.get("role") == approved_role and members <= set(row.get("members", []))
    for row in policy.get("bindings", [])
), "Agent Runtime query IAM binding was not persisted"
assert not any(
    row.get("role") == "roles/aiplatform.user"
    and members.intersection(row.get("members", []))
    for row in policy.get("bindings", [])
), "broad Agent Runtime role remains bound"
assert any(
    row.get("role") == session_role and agent_identity in row.get("members", [])
    for row in policy.get("bindings", [])
), "Agent Runtime identity lacks resource-scoped session lifecycle IAM"
assert not any(
    row.get("role") in {"roles/aiplatform.agentContextEditor", "roles/aiplatform.user"}
    and agent_identity in row.get("members", [])
    for row in policy.get("bindings", [])
), "Agent Runtime identity retains broad context mutation IAM"
PY
unset access_token agent_runtime_json agent_runtime_identity agent_runtime_policy

expected_document_bucket="${project_id}-caffemate-documents"
if [ "$document_bucket" != "$expected_document_bucket" ]; then
  printf 'document bucket must be the pinned regional bucket %s\n' \
    "$expected_document_bucket" >&2
  exit 2
fi
if ! gcloud storage buckets describe "gs://${document_bucket}" \
  --project="$project_id" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${document_bucket}" \
    --project="$project_id" \
    --location="$region" \
    --uniform-bucket-level-access \
    --public-access-prevention \
    --quiet >/dev/null
fi
gcloud storage buckets update "gs://${document_bucket}" \
  --project="$project_id" \
  --uniform-bucket-level-access \
  --public-access-prevention \
  --cors-file="deploy/gcs/document-cors.json" \
  --quiet >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://${document_bucket}" \
  --project="$project_id" \
  --member="serviceAccount:${api_sa}" \
  --role="$document_object_role" \
  --quiet >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://${document_bucket}" \
  --project="$project_id" \
  --member="serviceAccount:${worker_sa}" \
  --role="$document_reader_role" \
  --quiet >/dev/null
gcloud iam service-accounts add-iam-policy-binding "$api_sa" \
  --project="$project_id" \
  --member="serviceAccount:${api_sa}" \
  --role="$document_signer_role" \
  --quiet >/dev/null

create_service_account() {
  account_id=$1
  display_name=$2
  if ! gcloud iam service-accounts describe \
    "${account_id}@${project_id}.iam.gserviceaccount.com" \
    --project="$project_id" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$account_id" \
      --project="$project_id" \
      --display-name="$display_name" \
      --quiet >/dev/null
  fi
}

create_service_account caffemate-scheduler 'CaffeMate Scheduler caller'

common_database_env="INSTANCE_CONNECTION_NAME=${instance_connection_name},DB_USER=caffemate_app,DB_NAME=caffemate,CLOUD_SQL_IP_TYPE=PUBLIC"

# A Cloud Run service URL is stable after creation and is the exact audience used by
# the Worker ID token. Reuse it on normal deployments. A brand-new service needs one
# follow-up update after Cloud Run assigns its canonical URL.
existing_api_url=$(gcloud run services describe caffemate-api \
  --project="$project_id" \
  --region="$region" \
  --format='value(status.url)' 2>/dev/null || true)
api_audience_env=''
if [ -n "$existing_api_url" ]; then
  api_audience_env=",CONTROL_API_AUDIENCE=${existing_api_url}"
fi

gcloud run deploy caffemate-api \
  --project="$project_id" \
  --region="$region" \
  --image="$image" \
  --service-account="$api_sa" \
  --set-cloudsql-instances="$instance_connection_name" \
  --set-env-vars="${common_database_env},FIREBASE_PROJECT_ID=${project_id},CORS_ALLOWED_ORIGINS=https://caffemate-web-hfgnuuc55q-du.a.run.app;https://caffemate-web-424808310695.asia-northeast3.run.app,CAFFEMATE_POLICY_SNAPSHOT_ID=policy-v1,WORKER_SERVICE_ACCOUNT_EMAIL=${worker_sa},AGENT_RUNTIME_PROJECT_ID=${project_id},AGENT_RUNTIME_RESOURCE_ID=${agent_runtime_resource_id},MCP_BASE_URL=${mcp_url},MCP_AUDIENCE=${mcp_url},DOCUMENT_BUCKET=${document_bucket},DOCUMENT_SIGNING_SERVICE_ACCOUNT_EMAIL=${api_sa}${api_audience_env}" \
  --set-secrets='DB_PASS=caffemate-db-password:latest,AGENT_RUNTIME_USER_HMAC_SECRET=caffemate-agent-runtime-user-hmac:latest,MCP_SCOPE_HMAC_SECRET=caffemate-mcp-scope-hmac:latest' \
  --port=8080 \
  --timeout=600 \
  --ingress=all \
  --default-url \
  --invoker-iam-check \
  --allow-unauthenticated \
  --cpu=1 \
  --memory=512Mi \
  --min=0 \
  --max=10 \
  --labels="source-revision=${source_revision},managed-by=caffemate-deploy" \
  --quiet >/dev/null

api_url=$(gcloud run services describe caffemate-api \
  --project="$project_id" \
  --region="$region" \
  --format='value(status.url)')

if [ -z "$existing_api_url" ]; then
  gcloud run services update caffemate-api \
    --project="$project_id" \
    --region="$region" \
    --update-env-vars="CONTROL_API_AUDIENCE=${api_url}" \
    --quiet >/dev/null
fi

gcloud run services add-iam-policy-binding caffemate-api \
  --project="$project_id" \
  --region="$region" \
  --member='allUsers' \
  --role='roles/run.invoker' \
  --quiet >/dev/null

gcloud run deploy caffemate-worker \
  --project="$project_id" \
  --region="$region" \
  --image="$image" \
  --service-account="$worker_sa" \
  --command=uvicorn \
  --args=worker.main:app,--host,0.0.0.0,--port,8080 \
  --set-cloudsql-instances="$instance_connection_name" \
  --set-env-vars="${common_database_env},WORKER_ID=caffemate-worker,AGENT_RUNTIME_PROJECT_ID=${project_id},AGENT_RUNTIME_RESOURCE_ID=${agent_runtime_resource_id},DOCUMENT_BUCKET=${document_bucket}" \
  --set-secrets='DB_PASS=caffemate-db-password:latest' \
  --port=8080 \
  --timeout=600 \
  --ingress=internal \
  --cpu=1 \
  --memory=512Mi \
  --min=0 \
  --max=10 \
  --labels="source-revision=${source_revision},managed-by=caffemate-deploy" \
  --quiet >/dev/null

worker_url=$(gcloud run services describe caffemate-worker \
  --project="$project_id" \
  --region="$region" \
  --format='value(status.url)')

gcloud run services add-iam-policy-binding caffemate-api \
  --project="$project_id" \
  --region="$region" \
  --member="serviceAccount:${worker_sa}" \
  --role='roles/run.invoker' \
  --quiet >/dev/null

gcloud run services add-iam-policy-binding caffemate-worker \
  --project="$project_id" \
  --region="$region" \
  --member="serviceAccount:${scheduler_sa}" \
  --role='roles/run.invoker' \
  --quiet >/dev/null

scheduler_uri="${worker_url}/internal/v1/agent-sessions:cleanup"
if gcloud scheduler jobs describe caffemate-agent-session-cleanup \
  --project="$project_id" \
  --location="$region" >/dev/null 2>&1; then
  gcloud scheduler jobs update http caffemate-agent-session-cleanup \
    --project="$project_id" \
    --location="$region" \
    --schedule='* * * * *' \
    --time-zone='Asia/Seoul' \
    --uri="$scheduler_uri" \
    --http-method=POST \
    --update-headers='Content-Type=application/json' \
    --message-body='{"limit":20}' \
    --oidc-service-account-email="$scheduler_sa" \
    --oidc-token-audience="$worker_url" \
    --attempt-deadline=30s \
    --quiet >/dev/null
else
  gcloud scheduler jobs create http caffemate-agent-session-cleanup \
    --project="$project_id" \
    --location="$region" \
    --schedule='* * * * *' \
    --time-zone='Asia/Seoul' \
    --uri="$scheduler_uri" \
    --http-method=POST \
    --headers='Content-Type=application/json' \
    --message-body='{"limit":20}' \
    --oidc-service-account-email="$scheduler_sa" \
    --oidc-token-audience="$worker_url" \
    --attempt-deadline=30s \
    --quiet >/dev/null
fi

printf '%s\n' 'API, Worker and Agent cleanup Scheduler deployment completed; run the verifier.'
