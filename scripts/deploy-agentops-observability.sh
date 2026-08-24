#!/usr/bin/env bash
set -euo pipefail

# User intent: create observability resources without deploying application code.
project_id="${1:-${GOOGLE_CLOUD_PROJECT:-}}"
if [[ -z "${project_id}" ]]; then
  echo "usage: $0 PROJECT_ID" >&2
  exit 2
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dashboard_file="${repo_dir}/deploy/monitoring/caffemate-agentops-dashboard.json"

gcloud services enable cloudtrace.googleapis.com monitoring.googleapis.com logging.googleapis.com \
  --project="${project_id}" --quiet

for account_id in caffemate-api-runtime caffemate-worker-runtime; do
  gcloud projects add-iam-policy-binding "${project_id}" \
    --member="serviceAccount:${account_id}@${project_id}.iam.gserviceaccount.com" \
    --role=roles/cloudtrace.agent --condition=None --quiet >/dev/null
done

runtime_resource="$(jq -r '.runtime.resource_name' "${repo_dir}/agents/release-manifest.json")"
case "${runtime_resource}" in
  "projects/${project_id}/locations/asia-northeast3/reasoningEngines/"*) ;;
  *) echo "Agent Runtime release manifest is outside the target project" >&2; exit 1 ;;
esac
access_token="$(gcloud auth print-access-token)"
runtime_identity="$(curl --fail --silent --show-error \
  --header "Authorization: Bearer ${access_token}" \
  "https://asia-northeast3-aiplatform.googleapis.com/v1/${runtime_resource}" | \
  jq -r '.spec.effectiveIdentity')"
case "${runtime_identity}" in
  principal://*) runtime_member="${runtime_identity}" ;;
  agents.*) runtime_member="principal://${runtime_identity}" ;;
  *) echo "Agent Runtime effective identity is unavailable" >&2; exit 1 ;;
esac
gcloud projects add-iam-policy-binding "${project_id}" \
  --member="${runtime_member}" --role=roles/cloudtrace.agent \
  --condition=None --quiet >/dev/null

ensure_counter_metric() {
  local name="$1"
  local filter="$2"
  local description="$3"
  if gcloud logging metrics describe "${name}" --project="${project_id}" >/dev/null 2>&1; then
    gcloud logging metrics update "${name}" --project="${project_id}" \
      --log-filter="${filter}" --description="${description}" --quiet
  else
    gcloud logging metrics create "${name}" --project="${project_id}" \
      --log-filter="${filter}" --description="${description}" --quiet
  fi
}

ensure_distribution_metric() {
  local name="$1"
  local config_file="$2"
  if gcloud logging metrics describe "${name}" --project="${project_id}" >/dev/null 2>&1; then
    echo "metric_exists=${name}"
  else
    gcloud logging metrics create "${name}" --project="${project_id}" \
      --config-from-file="${config_file}" --quiet
  fi
}

ensure_counter_metric \
  caffemate_agent_invocations \
  'jsonPayload.event="CAFFEMATE_AGENT_INVOCATION" OR jsonPayload.event="VERTEX_AGENT_GENERATION"' \
  'CaffeMate typed Agent invocations; no user or document content.'
ensure_distribution_metric \
  caffemate_model_latency_ms \
  "${repo_dir}/deploy/monitoring/caffemate-model-latency-metric.json"
ensure_distribution_metric \
  caffemate_model_tokens \
  "${repo_dir}/deploy/monitoring/caffemate-model-tokens-metric.json"

existing_id="$(gcloud monitoring dashboards list --project="${project_id}" --format=json | \
  python3 -c 'import json,sys; print(next((v["name"] for v in json.load(sys.stdin) if v.get("displayName") == "CaffeMate AgentOps"), ""))')"
if [[ -n "${existing_id}" ]]; then
  dashboard_name="$(gcloud monitoring dashboards update "${existing_id}" \
    --config-from-file="${dashboard_file}" --project="${project_id}" --format='value(name)')"
else
  dashboard_name="$(gcloud monitoring dashboards create \
    --config-from-file="${dashboard_file}" --project="${project_id}" --format='value(name)')"
fi

dashboard_id="${dashboard_name##*/}"
gcloud monitoring dashboards describe "${dashboard_name}" --project="${project_id}" \
  --format=json >/dev/null
echo "dashboard_name=${dashboard_name}"
echo "dashboard_url=https://console.cloud.google.com/monitoring/dashboards/builder/${dashboard_id}?project=${project_id}"
echo "trace_explorer_url=https://console.cloud.google.com/traces/explorer?project=${project_id}"
