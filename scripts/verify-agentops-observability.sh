#!/usr/bin/env bash
set -euo pipefail

project_id="${1:-${GOOGLE_CLOUD_PROJECT:-}}"
if [[ -z "${project_id}" ]]; then
  echo "usage: $0 PROJECT_ID" >&2
  exit 2
fi

dashboard_name="$(gcloud monitoring dashboards list --project="${project_id}" --format=json | \
  python3 -c 'import json,sys; print(next((v["name"] for v in json.load(sys.stdin) if v.get("displayName") == "CaffeMate AgentOps"), ""))')"
if [[ -z "${dashboard_name}" ]]; then
  echo "CaffeMate AgentOps dashboard is missing" >&2
  exit 1
fi

dashboard_json="$(gcloud monitoring dashboards describe "${dashboard_name}" \
  --project="${project_id}" --format=json)"
python3 -c '
import json, sys
value = json.load(sys.stdin)
text = json.dumps(value)
assert value["displayName"] == "CaffeMate AgentOps"
assert "never records raw user text" in text
assert "caffemate_agent_invocations" in text
assert "caffemate_model_latency_ms" in text
assert "RAG signal contract" in text
' <<<"${dashboard_json}"

dashboard_id="${dashboard_name##*/}"
echo "dashboard_name=${dashboard_name}"
echo "dashboard_url=https://console.cloud.google.com/monitoring/dashboards/builder/${dashboard_id}?project=${project_id}"
echo "trace_explorer_url=https://console.cloud.google.com/traces/explorer?project=${project_id}"
