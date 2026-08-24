#!/usr/bin/env bash
set -euo pipefail

project_id="${CAFFEMATE_GCP_PROJECT_ID:-proj-aj20-211200020328}"
region="asia-northeast3"
template_id="${CAFFEMATE_MODEL_ARMOR_TEMPLATE_ID:-caffemate-sdp-inspect-v1}"
api_service="${CAFFEMATE_API_SERVICE:-caffemate-api}"
api_sa="${CAFFEMATE_API_SERVICE_ACCOUNT:-caffemate-api-runtime@${project_id}.iam.gserviceaccount.com}"
verification_job="${CAFFEMATE_MODEL_ARMOR_JOB:-caffemate-model-armor-verification}"
template_resource="projects/${project_id}/locations/${region}/templates/${template_id}"
endpoint="https://modelarmor.${region}.rep.googleapis.com/v1"

access_token="$(gcloud auth print-access-token)"
template="$(curl -fsS \
  -H "Authorization: Bearer ${access_token}" \
  "${endpoint}/${template_resource}")"
TEMPLATE="$template" python3 - <<'PY'
import json
import os

template = json.loads(os.environ["TEMPLATE"])
assert template["filterConfig"]["sdpSettings"]["basicConfig"]["filterEnforcement"] == "ENABLED"
assert template["templateMetadata"]["enforcementType"] == "INSPECT_ONLY"
assert template["templateMetadata"]["logSanitizeOperations"] is False
PY

ready_revision="$(gcloud run services describe "$api_service" \
  --project="$project_id" --region="$region" \
  --format='value(status.latestReadyRevisionName)')"
api_image="$(gcloud run revisions describe "$ready_revision" \
  --project="$project_id" --region="$region" \
  --format='value(spec.containers[0].image)')"
image_digest="$(gcloud run revisions describe "$ready_revision" \
  --project="$project_id" --region="$region" \
  --format='value(status.imageDigest)')"
[[ "$image_digest" == sha256:* ]]

service_template="$(gcloud run services describe "$api_service" \
  --project="$project_id" --region="$region" --format=json)"
SERVICE_TEMPLATE="$service_template" TEMPLATE_RESOURCE="$template_resource" python3 - <<'PY'
import json
import os

service = json.loads(os.environ["SERVICE_TEMPLATE"])
containers = service["spec"]["template"]["spec"]["containers"]
env = {item["name"]: item.get("value") for item in containers[0].get("env", [])}
assert env["MODEL_ARMOR_TEMPLATE"] == os.environ["TEMPLATE_RESOURCE"]
PY

gcloud projects get-iam-policy "$project_id" --format=json | \
  API_SA="$api_sa" python3 -c '
import json, os, sys
policy = json.load(sys.stdin)
member = "serviceAccount:" + os.environ["API_SA"]
assert any(
    binding.get("role") == "roles/modelarmor.user" and member in binding.get("members", [])
    for binding in policy.get("bindings", [])
)
'

gcloud run jobs deploy "$verification_job" \
  --project="$project_id" \
  --region="$region" \
  --image="$api_image" \
  --service-account="$api_sa" \
  --command="caffemate-api" \
  --args="verify-model-armor" \
  --set-env-vars="MODEL_ARMOR_TEMPLATE=${template_resource}" \
  --max-retries=0 \
  --quiet >/dev/null

gcloud run jobs execute "$verification_job" \
  --project="$project_id" \
  --region="$region" \
  --wait \
  --quiet >/dev/null

printf '{"status":"verified","template":"%s","api_revision":"%s","image_digest":"%s"}\n' \
  "$template_resource" "$ready_revision" "$image_digest"
