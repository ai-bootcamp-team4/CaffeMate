#!/usr/bin/env bash
set -euo pipefail

project_id="${CAFFEMATE_GCP_PROJECT_ID:-proj-aj20-211200020328}"
region="asia-northeast3"
template_id="${CAFFEMATE_MODEL_ARMOR_TEMPLATE_ID:-caffemate-sdp-inspect-v1}"
api_service="${CAFFEMATE_API_SERVICE:-caffemate-api}"
api_sa="${CAFFEMATE_API_SERVICE_ACCOUNT:-caffemate-api-runtime@${project_id}.iam.gserviceaccount.com}"
template_resource="projects/${project_id}/locations/${region}/templates/${template_id}"
endpoint="https://modelarmor.${region}.rep.googleapis.com/v1"

gcloud services enable modelarmor.googleapis.com --project="$project_id"
access_token="$(gcloud auth print-access-token)"
response_file="$(mktemp)"
trap 'rm -f "$response_file"' EXIT
status="$(curl -sS -o "$response_file" -w '%{http_code}' \
  -H "Authorization: Bearer ${access_token}" \
  "${endpoint}/${template_resource}")"

if [[ "$status" == "404" ]]; then
  jq -n '{
    "filterConfig": {
      "sdpSettings": {"basicConfig": {"filterEnforcement": "ENABLED"}}
    },
    "templateMetadata": {
      "enforcementType": "INSPECT_ONLY",
      "logTemplateOperations": true,
      "logSanitizeOperations": false
    }
  }' | curl -fsS -X POST \
    -H "Authorization: Bearer ${access_token}" \
    -H 'Content-Type: application/json' \
    --data-binary @- \
    "${endpoint}/projects/${project_id}/locations/${region}/templates?templateId=${template_id}" \
    >"$response_file"
elif [[ "$status" != "200" ]]; then
  jq '{error: .error.status, code: .error.code}' "$response_file" >&2 || true
  exit 1
fi

jq -e '
  .name == "'"$template_resource"'" and
  .filterConfig.sdpSettings.basicConfig.filterEnforcement == "ENABLED" and
  .templateMetadata.enforcementType == "INSPECT_ONLY" and
  .templateMetadata.logSanitizeOperations != true
' "$response_file" >/dev/null

gcloud projects add-iam-policy-binding "$project_id" \
  --member="serviceAccount:${api_sa}" \
  --role="roles/modelarmor.user" \
  --condition=None \
  --quiet >/dev/null

gcloud run services update "$api_service" \
  --project="$project_id" \
  --region="$region" \
  --update-env-vars="MODEL_ARMOR_TEMPLATE=${template_resource}" \
  --quiet >/dev/null

printf '%s\n' "$template_resource"
