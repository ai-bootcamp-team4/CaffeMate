#!/bin/sh
set -eu

project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
region=${CAFFEMATE_GCP_REGION:-asia-northeast3}
source_revision=${CAFFEMATE_SOURCE_REVISION:-}
job_name=${CAFFEMATE_MIGRATION_JOB:-caffemate-migrate}

if [ -z "$project_id" ] || [ -z "$source_revision" ]; then
  printf '%s\n' 'CAFFEMATE_GCP_PROJECT_ID and CAFFEMATE_SOURCE_REVISION are required' >&2
  exit 2
fi

job_json=$(gcloud run jobs describe "$job_name" \
  --project="$project_id" \
  --region="$region" \
  --format=json)

JOB_JSON="$job_json" python3 - "$project_id" "$region" "$source_revision" <<'PY'
import json
import os
import sys

project_id, region, source_revision = sys.argv[1:]
job = json.loads(os.environ["JOB_JSON"])
template = job["spec"]["template"]["spec"]["template"]["spec"]
job_spec = job["spec"]["template"]["spec"]
container = template["containers"][0]
expected_image_prefix = f"{region}-docker.pkg.dev/{project_id}/caffemate-backend/backend@sha256:"

assert job["metadata"]["labels"]["source-revision"] == source_revision
assert template["serviceAccountName"] == f"caffemate-migrate@{project_id}.iam.gserviceaccount.com"
assert container["image"].startswith(expected_image_prefix), container["image"]
assert container["command"] == ["caffemate-api"]
assert container["args"] == ["migrate"]

env = {item["name"]: item for item in container["env"]}
assert env["DB_USER"]["value"] == "caffemate_app"
assert env["DB_NAME"]["value"] == "caffemate"
assert env["CLOUD_SQL_IP_TYPE"]["value"] == "PUBLIC"
assert env["DB_PASS"]["valueFrom"]["secretKeyRef"]["key"] == "latest"
assert env["DB_PASS"]["valueFrom"]["secretKeyRef"]["name"] == "caffemate-db-password"
assert template["maxRetries"] == 0
assert job_spec["taskCount"] == 1
print("PASS migration job configuration and digest-pinned image")
PY

execution_name=$(gcloud run jobs executions list \
  --job="$job_name" \
  --project="$project_id" \
  --region="$region" \
  --sort-by='~metadata.creationTimestamp' \
  --limit=1 \
  --format='value(metadata.name)')

completion=$(gcloud run jobs executions describe "$execution_name" \
  --project="$project_id" \
  --region="$region" \
  --format='value(status.conditions[0].status)')

if [ "$completion" != 'True' ]; then
  printf 'FAIL latest migration verification execution: %s\n' "$completion" >&2
  exit 1
fi

printf 'PASS latest execution %s completed\n' "$execution_name"
printf '%s\n' 'migration job verification passed'
