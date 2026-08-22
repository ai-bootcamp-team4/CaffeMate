#!/bin/sh
set -eu

project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
region=${CAFFEMATE_GCP_REGION:-asia-northeast3}
source_revision=${CAFFEMATE_SOURCE_REVISION:-}
instance_id=${CAFFEMATE_DB_INSTANCE_ID:-caffemate-postgres}

if [ -z "$project_id" ] || [ -z "$source_revision" ]; then
  printf '%s\n' 'CAFFEMATE_GCP_PROJECT_ID and CAFFEMATE_SOURCE_REVISION are required' >&2
  exit 2
fi
if [ "$region" != 'asia-northeast3' ] || [ "${#source_revision}" -ne 40 ]; then
  printf '%s\n' 'canonical region and full commit SHA are required' >&2
  exit 2
fi

active_project=$(gcloud config get-value project 2>/dev/null)
if [ "$active_project" != "$project_id" ]; then
  printf 'active gcloud project %s does not match requested project %s\n' \
    "$active_project" "$project_id" >&2
  exit 2
fi

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
topic_id='caffemate-workflow-stage-ready'
subscription_id='caffemate-workflow-stage-worker'
topic_resource="projects/${project_id}/topics/${topic_id}"
subscription_resource="projects/${project_id}/subscriptions/${subscription_id}"
api_sa="caffemate-api-runtime@${project_id}.iam.gserviceaccount.com"
worker_sa="caffemate-worker-runtime@${project_id}.iam.gserviceaccount.com"
push_sa="caffemate-pubsub-push@${project_id}.iam.gserviceaccount.com"
scheduler_sa="caffemate-scheduler@${project_id}.iam.gserviceaccount.com"
mcp_url=$(gcloud run services describe caffemate-mcp \
  --project="$project_id" --region="$region" --format='value(status.url)')
if [ -z "$mcp_url" ]; then
  printf '%s\n' 'private MCP service must be deployed before Control API' >&2
  exit 1
fi

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

create_service_account caffemate-pubsub-push 'CaffeMate Pub Sub push caller'
create_service_account caffemate-scheduler 'CaffeMate Scheduler caller'

if ! gcloud pubsub topics describe "$topic_id" \
  --project="$project_id" >/dev/null 2>&1; then
  gcloud pubsub topics create "$topic_id" --project="$project_id" --quiet >/dev/null
fi

gcloud pubsub topics add-iam-policy-binding "$topic_id" \
  --project="$project_id" \
  --member="serviceAccount:${worker_sa}" \
  --role='roles/pubsub.publisher' \
  --quiet >/dev/null

common_database_env="INSTANCE_CONNECTION_NAME=${instance_connection_name},DB_USER=caffemate_app,DB_NAME=caffemate,CLOUD_SQL_IP_TYPE=PUBLIC"

gcloud run deploy caffemate-api \
  --project="$project_id" \
  --region="$region" \
  --image="$image" \
  --service-account="$api_sa" \
  --set-cloudsql-instances="$instance_connection_name" \
  --set-env-vars="${common_database_env},FIREBASE_PROJECT_ID=${project_id},CAFFEMATE_POLICY_SNAPSHOT_ID=policy-v1,WORKER_SERVICE_ACCOUNT_EMAIL=${worker_sa},MCP_BASE_URL=${mcp_url},MCP_AUDIENCE=${mcp_url}" \
  --set-secrets='DB_PASS=caffemate-db-password:latest,AGENT_RUNTIME_USER_HMAC_SECRET=caffemate-agent-runtime-user-hmac:latest,MCP_SCOPE_HMAC_SECRET=caffemate-mcp-scope-hmac:latest' \
  --port=8080 \
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
  --set-env-vars="${common_database_env},CONTROL_API_URL=${api_url},CONTROL_API_AUDIENCE=${api_url},WORKER_ID=caffemate-worker,PUBSUB_SUBSCRIPTION=${subscription_resource},WORKFLOW_STAGE_TOPIC_RESOURCE=${topic_resource}" \
  --set-secrets='DB_PASS=caffemate-db-password:latest' \
  --port=8080 \
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

for caller in "$worker_sa" "$push_sa" "$scheduler_sa"; do
  target='caffemate-api'
  if [ "$caller" != "$worker_sa" ]; then target='caffemate-worker'; fi
  gcloud run services add-iam-policy-binding "$target" \
    --project="$project_id" \
    --region="$region" \
    --member="serviceAccount:${caller}" \
    --role='roles/run.invoker' \
    --quiet >/dev/null
done

project_number=$(gcloud projects describe "$project_id" --format='value(projectNumber)')
pubsub_agent="service-${project_number}@gcp-sa-pubsub.iam.gserviceaccount.com"
gcloud iam service-accounts add-iam-policy-binding "$push_sa" \
  --project="$project_id" \
  --member="serviceAccount:${pubsub_agent}" \
  --role='roles/iam.serviceAccountTokenCreator' \
  --quiet >/dev/null

if gcloud pubsub subscriptions describe "$subscription_id" \
  --project="$project_id" >/dev/null 2>&1; then
  gcloud pubsub subscriptions update "$subscription_id" \
    --project="$project_id" \
    --push-endpoint="${worker_url}/internal/v1/pubsub/workflow-stages" \
    --push-auth-service-account="$push_sa" \
    --push-auth-token-audience="$worker_url" \
    --ack-deadline=120 \
    --min-retry-delay=10s \
    --max-retry-delay=300s \
    --quiet >/dev/null
else
  gcloud pubsub subscriptions create "$subscription_id" \
    --project="$project_id" \
    --topic="$topic_id" \
    --push-endpoint="${worker_url}/internal/v1/pubsub/workflow-stages" \
    --push-auth-service-account="$push_sa" \
    --push-auth-token-audience="$worker_url" \
    --ack-deadline=120 \
    --min-retry-delay=10s \
    --max-retry-delay=300s \
    --expiration-period=never \
    --quiet >/dev/null
fi

scheduler_uri="${worker_url}/internal/v1/outbox:publish"
if gcloud scheduler jobs describe caffemate-outbox-drain \
  --project="$project_id" \
  --location="$region" >/dev/null 2>&1; then
  gcloud scheduler jobs update http caffemate-outbox-drain \
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
  gcloud scheduler jobs create http caffemate-outbox-drain \
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

printf '%s\n' 'API, Worker, Pub/Sub and Scheduler runtime deployment completed; run the verifier.'
