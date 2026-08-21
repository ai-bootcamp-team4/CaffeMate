#!/bin/sh
set -eu

project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
region=${CAFFEMATE_GCP_REGION:-asia-northeast3}
instance_id=${CAFFEMATE_DB_INSTANCE_ID:-caffemate-postgres}
database_name=${CAFFEMATE_DB_NAME:-caffemate}
database_user=${CAFFEMATE_DB_USER:-caffemate_app}
database_tier=${CAFFEMATE_DB_TIER:-db-g1-small}
password_secret=${CAFFEMATE_DB_PASSWORD_SECRET:-caffemate-db-password}

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

gcloud services enable sqladmin.googleapis.com \
  --project="$project_id" \
  --quiet >/dev/null

if ! gcloud sql instances describe "$instance_id" \
  --project="$project_id" >/dev/null 2>&1; then
  gcloud sql instances create "$instance_id" \
    --project="$project_id" \
    --region="$region" \
    --database-version=POSTGRES_16 \
    --edition=enterprise \
    --tier="$database_tier" \
    --availability-type=zonal \
    --assign-ip \
    --storage-type=SSD \
    --storage-size=10GB \
    --storage-auto-increase \
    --backup-start-time=18:00 \
    --enable-point-in-time-recovery \
    --retained-backups-count=7 \
    --retained-transaction-log-days=7 \
    --enable-password-policy \
    --deletion-protection \
    --quiet >/dev/null
fi

if ! gcloud sql databases describe "$database_name" \
  --instance="$instance_id" \
  --project="$project_id" >/dev/null 2>&1; then
  gcloud sql databases create "$database_name" \
    --instance="$instance_id" \
    --project="$project_id" \
    --quiet >/dev/null
fi

database_password=$(gcloud secrets versions access latest \
  --secret="$password_secret" \
  --project="$project_id")

if gcloud sql users list \
  --instance="$instance_id" \
  --project="$project_id" \
  --filter="name=${database_user}" \
  --format='value(name)' | grep -Fx "$database_user" >/dev/null; then
  gcloud sql users set-password "$database_user" \
    --instance="$instance_id" \
    --project="$project_id" \
    --password="$database_password" \
    --quiet >/dev/null
else
  gcloud sql users create "$database_user" \
    --instance="$instance_id" \
    --project="$project_id" \
    --password="$database_password" \
    --quiet >/dev/null
fi
unset database_password

for account_id in caffemate-api-runtime caffemate-worker-runtime caffemate-migrate; do
  member="serviceAccount:${account_id}@${project_id}.iam.gserviceaccount.com"
  if gcloud projects get-iam-policy "$project_id" \
    --flatten='bindings[].members' \
    --filter="bindings.role=roles/cloudsql.client AND bindings.members=${member}" \
    --format='value(bindings.role)' | grep -Fx 'roles/cloudsql.client' >/dev/null; then
    continue
  fi
  gcloud projects add-iam-policy-binding "$project_id" \
    --member="$member" \
    --role='roles/cloudsql.client' \
    --condition=None \
    --quiet >/dev/null
done

printf '%s\n' 'CaffeMate Cloud SQL bootstrap completed; run the verifier for read-back evidence.'
