#!/bin/sh
set -eu

project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
region=${CAFFEMATE_GCP_REGION:-asia-northeast3}

if [ -z "$project_id" ]; then
  printf '%s\n' 'CAFFEMATE_GCP_PROJECT_ID is required' >&2
  exit 2
fi

if [ "$region" != 'asia-northeast3' ]; then
  printf 'unexpected CaffeMate region: %s\n' "$region" >&2
  exit 1
fi

failures=0

check() {
  label=$1
  shift
  if "$@" >/dev/null 2>&1; then
    printf 'PASS %s\n' "$label"
  else
    printf 'FAIL %s\n' "$label" >&2
    failures=$((failures + 1))
  fi
}

check_repository() {
  format=$(gcloud artifacts repositories describe caffemate-backend \
    --project="$project_id" \
    --location="$region" \
    --format='value(format)' 2>/dev/null || true)
  immutable=$(gcloud artifacts repositories describe caffemate-backend \
    --project="$project_id" \
    --location="$region" \
    --format='value(dockerConfig.immutableTags)' 2>/dev/null || true)
  [ "$format" = 'DOCKER' ] && [ "$immutable" = 'True' ]
}

check_enabled_secret_version() {
  secret_id=$1
  count=$(gcloud secrets versions list "$secret_id" \
    --project="$project_id" \
    --filter='state=enabled' \
    --format='value(name)' 2>/dev/null | wc -l | tr -d ' ')
  [ "$count" -eq 1 ]
}

check_secret_member() {
  secret_id=$1
  service_account_email=$2
  gcloud secrets get-iam-policy "$secret_id" \
    --project="$project_id" \
    --flatten='bindings[].members' \
    --filter="bindings.role=roles/secretmanager.secretAccessor AND bindings.members=serviceAccount:${service_account_email}" \
    --format='value(bindings.role)' | grep -Fx 'roles/secretmanager.secretAccessor'
}

check_project_role() {
  service_account_email=$1
  role=$2
  gcloud projects get-iam-policy "$project_id" \
    --flatten='bindings[].members' \
    --filter="bindings.role=${role} AND bindings.members=serviceAccount:${service_account_email}" \
    --format='value(bindings.role)' | grep -Fx "$role"
}

check_service_account_user() {
  runtime_service_account_email=$1
  member_service_account_email=$2
  gcloud iam service-accounts get-iam-policy "$runtime_service_account_email" \
    --project="$project_id" \
    --flatten='bindings[].members' \
    --filter="bindings.role=roles/iam.serviceAccountUser AND bindings.members=serviceAccount:${member_service_account_email}" \
    --format='value(bindings.role)' | grep -Fx 'roles/iam.serviceAccountUser'
}

check 'immutable Docker repository caffemate-backend' check_repository

for account_id in \
  caffemate-backend-build \
  caffemate-api-runtime \
  caffemate-worker-runtime \
  caffemate-migrate \
  caffemate-mcp-runtime; do
  check "service account ${account_id}" \
    gcloud iam service-accounts describe \
      "${account_id}@${project_id}.iam.gserviceaccount.com" \
      --project="$project_id"
done

for secret_id in \
  caffemate-db-password \
  caffemate-agent-runtime-user-hmac \
  caffemate-mcp-scope-hmac; do
  check "secret ${secret_id} has exactly one enabled version" \
    check_enabled_secret_version "$secret_id"
done

api_sa="caffemate-api-runtime@${project_id}.iam.gserviceaccount.com"
worker_sa="caffemate-worker-runtime@${project_id}.iam.gserviceaccount.com"
migrate_sa="caffemate-migrate@${project_id}.iam.gserviceaccount.com"
mcp_sa="caffemate-mcp-runtime@${project_id}.iam.gserviceaccount.com"
build_sa="caffemate-backend-build@${project_id}.iam.gserviceaccount.com"

for role in \
  roles/artifactregistry.writer \
  roles/logging.logWriter \
  roles/run.admin; do
  check "backend build has ${role}" check_project_role "$build_sa" "$role"
done

check 'backend build can read the Cloud Build source bucket' sh -c \
  "gcloud storage buckets get-iam-policy 'gs://${project_id}_cloudbuild' --format=json | BUILD_SA='$build_sa' python3 -c 'import json,os,sys; member=\"serviceAccount:\"+os.environ[\"BUILD_SA\"]; bindings=json.load(sys.stdin).get(\"bindings\", []); raise SystemExit(0 if any(item.get(\"role\")==\"roles/storage.objectViewer\" and member in item.get(\"members\", []) for item in bindings) else 1)'"

for runtime_sa in "$api_sa" "$worker_sa" "$migrate_sa"; do
  check "backend build can deploy as ${runtime_sa}" \
    check_service_account_user "$runtime_sa" "$build_sa"
done

check 'API can read Firebase Authentication users for revocation checks' \
  check_project_role "$api_sa" roles/firebaseauth.viewer

check 'API can access database password' \
  check_secret_member caffemate-db-password "$api_sa"
check 'Worker can access database password' \
  check_secret_member caffemate-db-password "$worker_sa"
check 'migration job can access database password' \
  check_secret_member caffemate-db-password "$migrate_sa"
check 'API can access Agent Runtime user HMAC' \
  check_secret_member caffemate-agent-runtime-user-hmac "$api_sa"
check 'API can access MCP scope HMAC' \
  check_secret_member caffemate-mcp-scope-hmac "$api_sa"
check 'MCP can access MCP scope HMAC' \
  check_secret_member caffemate-mcp-scope-hmac "$mcp_sa"

if [ "$failures" -ne 0 ]; then
  printf 'backend foundation verification failed: %s check(s)\n' "$failures" >&2
  exit 1
fi

printf '%s\n' 'backend foundation verification passed'
