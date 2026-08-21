#!/bin/sh
set -eu

project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
region=${CAFFEMATE_GCP_REGION:-asia-northeast3}
instance_id=${CAFFEMATE_DB_INSTANCE_ID:-caffemate-postgres}
database_name=${CAFFEMATE_DB_NAME:-caffemate}
database_user=${CAFFEMATE_DB_USER:-caffemate_app}

if [ -z "$project_id" ]; then
  printf '%s\n' 'CAFFEMATE_GCP_PROJECT_ID is required' >&2
  exit 2
fi

failures=0

assert_equal() {
  label=$1
  expected=$2
  actual=$3
  if [ "$actual" = "$expected" ]; then
    printf 'PASS %s\n' "$label"
  else
    printf 'FAIL %s: expected %s, got %s\n' "$label" "$expected" "$actual" >&2
    failures=$((failures + 1))
  fi
}

assert_true() {
  label=$1
  shift
  if "$@" >/dev/null 2>&1; then
    printf 'PASS %s\n' "$label"
  else
    printf 'FAIL %s\n' "$label" >&2
    failures=$((failures + 1))
  fi
}

instance_json=$(gcloud sql instances describe "$instance_id" \
  --project="$project_id" \
  --format=json)

read_field() {
  field=$1
  python3 -c 'import json,sys; data=json.load(sys.stdin); value=data'"$field"'; print(str(value))' \
    <<EOF
$instance_json
EOF
}

assert_equal 'instance is runnable' 'RUNNABLE' "$(read_field "['state']")"
assert_equal 'instance region is canonical' "$region" "$(read_field "['region']")"
assert_equal 'database version is PostgreSQL 16' 'POSTGRES_16' "$(read_field "['databaseVersion']")"
assert_equal 'instance edition is Enterprise' 'ENTERPRISE' "$(read_field "['settings']['edition']")"
assert_equal 'instance is zonal' 'ZONAL' "$(read_field "['settings']['availabilityType']")"
assert_equal 'deletion protection is enabled' 'True' "$(read_field "['settings']['deletionProtectionEnabled']")"
assert_equal 'automated backups are enabled' 'True' "$(read_field "['settings']['backupConfiguration']['enabled']")"
assert_equal 'point-in-time recovery is enabled' 'True' "$(read_field "['settings']['backupConfiguration']['pointInTimeRecoveryEnabled']")"
assert_equal 'storage auto growth is enabled' 'True' "$(read_field "['settings']['storageAutoResize']")"

authorized_network_count=$(python3 -c \
  'import json,sys; print(len(json.load(sys.stdin)["settings"]["ipConfiguration"].get("authorizedNetworks", [])))' \
  <<EOF
$instance_json
EOF
)
assert_equal 'public IP has no authorized networks' '0' "$authorized_network_count"

assert_true "database ${database_name} exists" \
  gcloud sql databases describe "$database_name" \
    --instance="$instance_id" \
    --project="$project_id"

assert_true "database user ${database_user} exists" sh -c \
  "gcloud sql users list --instance='$instance_id' --project='$project_id' --filter='name=$database_user' --format='value(name)' | grep -Fx '$database_user'"

for account_id in caffemate-api-runtime caffemate-worker-runtime caffemate-migrate; do
  member="serviceAccount:${account_id}@${project_id}.iam.gserviceaccount.com"
  assert_true "${account_id} has Cloud SQL client" sh -c \
    "gcloud projects get-iam-policy '$project_id' --flatten='bindings[].members' --filter='bindings.role=roles/cloudsql.client AND bindings.members=$member' --format='value(bindings.role)' | grep -Fx roles/cloudsql.client"
done

if [ "$failures" -ne 0 ]; then
  printf 'Cloud SQL verification failed: %s check(s)\n' "$failures" >&2
  exit 1
fi

printf '%s\n' 'Cloud SQL verification passed'
