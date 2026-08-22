#!/bin/sh
set -eu

project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
region=${CAFFEMATE_GCP_REGION:-asia-northeast3}

if [ -z "$project_id" ]; then
  printf '%s\n' 'CAFFEMATE_GCP_PROJECT_ID is required' >&2
  exit 2
fi

if [ "$region" != 'asia-northeast3' ]; then
  printf 'refusing non-canonical region: %s\n' "$region" >&2
  exit 2
fi

active_project=$(gcloud config get-value project 2>/dev/null)
if [ "$active_project" != "$project_id" ]; then
  printf 'active gcloud project %s does not match requested project %s\n' \
    "$active_project" "$project_id" >&2
  exit 2
fi

create_service_account() {
  account_id=$1
  display_name=$2
  email="${account_id}@${project_id}.iam.gserviceaccount.com"

  if ! gcloud iam service-accounts describe "$email" \
    --project="$project_id" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$account_id" \
      --project="$project_id" \
      --display-name="$display_name" \
      --quiet >/dev/null
  fi
}

create_secret() {
  secret_id=$1
  if ! gcloud secrets describe "$secret_id" \
    --project="$project_id" >/dev/null 2>&1; then
    gcloud secrets create "$secret_id" \
      --project="$project_id" \
      --replication-policy=user-managed \
      --locations="$region" \
      --quiet >/dev/null
  fi

  enabled_count=$(gcloud secrets versions list "$secret_id" \
    --project="$project_id" \
    --filter='state=enabled' \
    --format='value(name)' | wc -l | tr -d ' ')
  if [ "$enabled_count" -eq 0 ]; then
    openssl rand -hex 48 | tr -d '\n' | gcloud secrets versions add "$secret_id" \
      --project="$project_id" \
      --data-file=- \
      --quiet >/dev/null
  fi
}

grant_secret_access() {
  secret_id=$1
  service_account_email=$2
  if gcloud secrets get-iam-policy "$secret_id" \
    --project="$project_id" \
    --flatten='bindings[].members' \
    --filter="bindings.role=roles/secretmanager.secretAccessor AND bindings.members=serviceAccount:${service_account_email}" \
    --format='value(bindings.role)' | grep -Fx 'roles/secretmanager.secretAccessor' >/dev/null; then
    return
  fi
  gcloud secrets add-iam-policy-binding "$secret_id" \
    --project="$project_id" \
    --member="serviceAccount:${service_account_email}" \
    --role='roles/secretmanager.secretAccessor' \
    --quiet >/dev/null
}

grant_project_role() {
  service_account_email=$1
  role=$2
  if gcloud projects get-iam-policy "$project_id" \
    --flatten='bindings[].members' \
    --filter="bindings.role=${role} AND bindings.members=serviceAccount:${service_account_email}" \
    --format='value(bindings.role)' | grep -Fx "$role" >/dev/null; then
    return
  fi
  gcloud projects add-iam-policy-binding "$project_id" \
    --member="serviceAccount:${service_account_email}" \
    --role="$role" \
    --condition=None \
    --quiet >/dev/null
}

grant_service_account_user() {
  runtime_service_account_email=$1
  member_service_account_email=$2
  if gcloud iam service-accounts get-iam-policy "$runtime_service_account_email" \
    --project="$project_id" \
    --flatten='bindings[].members' \
    --filter="bindings.role=roles/iam.serviceAccountUser AND bindings.members=serviceAccount:${member_service_account_email}" \
    --format='value(bindings.role)' | grep -Fx 'roles/iam.serviceAccountUser' >/dev/null; then
    return
  fi
  gcloud iam service-accounts add-iam-policy-binding "$runtime_service_account_email" \
    --project="$project_id" \
    --member="serviceAccount:${member_service_account_email}" \
    --role='roles/iam.serviceAccountUser' \
    --quiet >/dev/null
}

if ! gcloud artifacts repositories describe caffemate-backend \
  --project="$project_id" \
  --location="$region" >/dev/null 2>&1; then
  gcloud artifacts repositories create caffemate-backend \
    --project="$project_id" \
    --location="$region" \
    --repository-format=docker \
    --description='CaffeMate backend immutable images' \
    --immutable-tags \
    --quiet >/dev/null
fi

create_service_account caffemate-backend-build 'CaffeMate backend Cloud Build'
create_service_account caffemate-api-runtime 'CaffeMate Control API runtime'
create_service_account caffemate-worker-runtime 'CaffeMate Worker runtime'
create_service_account caffemate-migrate 'CaffeMate database migration job'
create_service_account caffemate-mcp-runtime 'CaffeMate private MCP runtime'

build_sa="caffemate-backend-build@${project_id}.iam.gserviceaccount.com"
api_sa="caffemate-api-runtime@${project_id}.iam.gserviceaccount.com"
worker_sa="caffemate-worker-runtime@${project_id}.iam.gserviceaccount.com"
migrate_sa="caffemate-migrate@${project_id}.iam.gserviceaccount.com"
mcp_sa="caffemate-mcp-runtime@${project_id}.iam.gserviceaccount.com"

for role in \
  roles/artifactregistry.writer \
  roles/logging.logWriter \
  roles/run.admin; do
  grant_project_role "$build_sa" "$role"
done

cloud_build_source_bucket="gs://${project_id}_cloudbuild"
if gcloud storage buckets describe "$cloud_build_source_bucket" >/dev/null 2>&1; then
  if ! gcloud storage buckets get-iam-policy "$cloud_build_source_bucket" --format=json \
    | BUILD_SA="$build_sa" python3 -c '
import json, os, sys
member = "serviceAccount:" + os.environ["BUILD_SA"]
bindings = json.load(sys.stdin).get("bindings", [])
raise SystemExit(0 if any(
    item.get("role") == "roles/storage.objectViewer" and member in item.get("members", [])
    for item in bindings
) else 1)
'; then
    gcloud storage buckets add-iam-policy-binding "$cloud_build_source_bucket" \
      --member="serviceAccount:${build_sa}" \
      --role='roles/storage.objectViewer' \
      --quiet >/dev/null
  fi
fi

for runtime_sa in "$api_sa" "$worker_sa" "$migrate_sa"; do
  grant_service_account_user "$runtime_sa" "$build_sa"
done

# verify_id_token(check_revoked=True) reads the Firebase user record to reject
# disabled users and revoked sessions. Keep this runtime permission read-only.
grant_project_role "$api_sa" roles/firebaseauth.viewer

create_secret caffemate-db-password
create_secret caffemate-agent-runtime-user-hmac
create_secret caffemate-mcp-scope-hmac

grant_secret_access caffemate-db-password "$api_sa"
grant_secret_access caffemate-db-password "$worker_sa"
grant_secret_access caffemate-db-password "$migrate_sa"
grant_secret_access caffemate-agent-runtime-user-hmac "$api_sa"
grant_secret_access caffemate-mcp-scope-hmac "$api_sa"
grant_secret_access caffemate-mcp-scope-hmac "$mcp_sa"

printf '%s\n' 'CaffeMate backend foundation bootstrap completed; run the verifier for read-back evidence.'
