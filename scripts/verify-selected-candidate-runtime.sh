#!/bin/sh
set -eu

project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
region=${CAFFEMATE_GCP_REGION:-asia-northeast3}
job_name=${CAFFEMATE_SELECTED_CANDIDATE_JOB:-caffemate-selected-candidate-canary}

if [ -z "$project_id" ]; then
  printf '%s\n' 'CAFFEMATE_GCP_PROJECT_ID is required' >&2
  exit 2
fi

api_service_json=$(gcloud run services describe caffemate-api \
  --project="$project_id" --region="$region" --format=json)
worker_service_json=$(gcloud run services describe caffemate-worker \
  --project="$project_id" --region="$region" --format=json)

read_service_value() {
  service_json=$1
  expression=$2
  SERVICE_JSON="$service_json" python3 -c \
    "import json,os; data=json.loads(os.environ['SERVICE_JSON']); print(${expression})"
}

api_revision=$(read_service_value "$api_service_json" \
  "data['status']['latestReadyRevisionName']")
worker_revision=$(read_service_value "$worker_service_json" \
  "data['status']['latestReadyRevisionName']")
api_revision_json=$(gcloud run revisions describe "$api_revision" \
  --project="$project_id" --region="$region" --format=json)
worker_revision_json=$(gcloud run revisions describe "$worker_revision" \
  --project="$project_id" --region="$region" --format=json)

# A Cloud Run service template may retain the submitted tag. The latest Ready
# revision is the operational source of truth for the immutable resolved digest.
api_image=$(read_service_value "$api_revision_json" \
  "data['status']['imageDigest']")
worker_image=$(read_service_value "$worker_revision_json" \
  "data['status']['imageDigest']")
source_revision=$(read_service_value "$api_revision_json" \
  "data['metadata']['labels']['source-revision']")
worker_source_revision=$(read_service_value "$worker_revision_json" \
  "data['metadata']['labels']['source-revision']")

case "$api_image" in
  *@sha256:*) ;;
  *) printf '%s\n' 'FAIL deployed API image is not digest pinned' >&2; exit 1 ;;
esac
[ "$api_image" = "$worker_image" ] || {
  printf '%s\n' 'FAIL API and Worker image digests differ' >&2
  exit 1
}
[ "$source_revision" = "$worker_source_revision" ] || {
  printf '%s\n' 'FAIL API and Worker source labels differ' >&2
  exit 1
}

service_env=$(read_service_value "$api_service_json" \
  "{row['name']: row.get('value', '') for row in data['spec']['template']['spec']['containers'][0].get('env', [])}")
configured_instance=$(SERVICE_ENV="$service_env" python3 -c \
  "import ast,os; print(ast.literal_eval(os.environ['SERVICE_ENV']).get('INSTANCE_CONNECTION_NAME', ''))")
configured_db_user=$(SERVICE_ENV="$service_env" python3 -c \
  "import ast,os; print(ast.literal_eval(os.environ['SERVICE_ENV']).get('DB_USER', ''))")
configured_db_name=$(SERVICE_ENV="$service_env" python3 -c \
  "import ast,os; print(ast.literal_eval(os.environ['SERVICE_ENV']).get('DB_NAME', ''))")
configured_db_ip_type=$(SERVICE_ENV="$service_env" python3 -c \
  "import ast,os; print(ast.literal_eval(os.environ['SERVICE_ENV']).get('CLOUD_SQL_IP_TYPE', ''))")
configured_mcp_url=$(SERVICE_ENV="$service_env" python3 -c \
  "import ast,os; print(ast.literal_eval(os.environ['SERVICE_ENV']).get('MCP_BASE_URL', ''))")
configured_mcp_audience=$(SERVICE_ENV="$service_env" python3 -c \
  "import ast,os; print(ast.literal_eval(os.environ['SERVICE_ENV']).get('MCP_AUDIENCE', ''))")
configured_policy=$(SERVICE_ENV="$service_env" python3 -c \
  "import ast,os; print(ast.literal_eval(os.environ['SERVICE_ENV']).get('CAFFEMATE_POLICY_SNAPSHOT_ID', ''))")

for required_value in \
  "$configured_instance" "$configured_db_user" "$configured_db_name" \
  "$configured_db_ip_type" "$configured_mcp_url" "$configured_mcp_audience" \
  "$configured_policy"; do
  [ -n "$required_value" ] || {
    printf '%s\n' 'FAIL deployed API is missing selected-candidate canary configuration' >&2
    exit 1
  }
done
[ "$configured_mcp_url" = "$configured_mcp_audience" ] || {
  printf '%s\n' 'FAIL deployed MCP URL and audience differ' >&2
  exit 1
}

api_sa="caffemate-api-runtime@${project_id}.iam.gserviceaccount.com"
configure_job() {
  action=$1
  gcloud run jobs "$action" "$job_name" \
    --project="$project_id" --region="$region" \
    --image="$api_image" --service-account="$api_sa" \
    --set-cloudsql-instances="$configured_instance" \
    --set-env-vars="INSTANCE_CONNECTION_NAME=${configured_instance},DB_USER=${configured_db_user},DB_NAME=${configured_db_name},CLOUD_SQL_IP_TYPE=${configured_db_ip_type},MCP_BASE_URL=${configured_mcp_url},MCP_AUDIENCE=${configured_mcp_audience},CAFFEMATE_POLICY_SNAPSHOT_ID=${configured_policy}" \
    --set-secrets='DB_PASS=caffemate-db-password:latest,MCP_SCOPE_HMAC_SECRET=caffemate-mcp-scope-hmac:latest' \
    --command=caffemate-api \
    --args='verify-selected-candidate,--timeout-seconds=1200,--poll-interval-seconds=3' \
    --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=45m \
    --cpu=1 --memory=512Mi \
    --labels="source-revision=${source_revision},managed-by=caffemate-verify" \
    --quiet >/dev/null
}

if gcloud run jobs describe "$job_name" \
  --project="$project_id" --region="$region" >/dev/null 2>&1; then
  configure_job update
else
  configure_job create
fi

job_image=$(gcloud run jobs describe "$job_name" \
  --project="$project_id" --region="$region" \
  --format='value(spec.template.spec.template.spec.containers[0].image)')
job_source_revision=$(gcloud run jobs describe "$job_name" \
  --project="$project_id" --region="$region" \
  --format='value(metadata.labels.source-revision)')
[ "$job_image" = "$api_image" ] || {
  printf '%s\n' 'FAIL selected-candidate canary does not use the deployed backend digest' >&2
  exit 1
}
[ "$job_source_revision" = "$source_revision" ] || {
  printf '%s\n' 'FAIL selected-candidate canary source label differs from backend' >&2
  exit 1
}
printf 'PASS selected-candidate canary pinned to backend source %s and digest image\n' "$source_revision"

started_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
status_file=$(mktemp)
wait_log=$(mktemp)
cleanup() {
  rm -f "$status_file" "$wait_log"
}
trap cleanup EXIT HUP INT TERM

(
  if gcloud run jobs execute "$job_name" \
    --project="$project_id" --region="$region" --wait --quiet \
    >"$wait_log" 2>&1; then
    printf '0\n' >"$status_file"
  else
    printf '%s\n' "$?" >"$status_file"
  fi
) &
wait_pid=$!

# FIRST_PROPOSAL and selective recompute are asynchronous. Drain the existing
# outbox while this single-purpose canary is waiting instead of running the full
# release verifier.
attempt=0
while [ ! -s "$status_file" ] && [ "$attempt" -lt 540 ]; do
  gcloud scheduler jobs run caffemate-outbox-drain \
    --project="$project_id" --location="$region" --quiet >/dev/null
  attempt=$((attempt + 1))
  sleep 5
done

if [ ! -s "$status_file" ]; then
  kill "$wait_pid" >/dev/null 2>&1 || true
  wait "$wait_pid" >/dev/null 2>&1 || true
  printf '%s\n' 'FAIL selected-candidate canary did not finish before harness timeout' >&2
  exit 1
fi
wait "$wait_pid" || true
job_exit=$(cat "$status_file")
if [ "$job_exit" != '0' ]; then
  sed -n '1,160p' "$wait_log" >&2
  printf '%s\n' 'FAIL selected-candidate canary Cloud Run Job' >&2
  exit 1
fi

reports='[]'
log_attempt=0
while [ "$log_attempt" -lt 12 ]; do
  reports=$(gcloud logging read \
    "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"${job_name}\" AND timestamp>=\"${started_at}\" AND jsonPayload.status=\"verified\"" \
    --project="$project_id" --limit=1 --order=desc --format=json)
  report_count=$(printf '%s' "$reports" | python3 -c \
    'import json,sys; print(len(json.load(sys.stdin)))')
  [ "$report_count" -ge 1 ] && break
  log_attempt=$((log_attempt + 1))
  sleep 5
done

SELECTED_CANDIDATE_REPORTS="$reports" python3 - <<'PY'
import json
import os

rows = json.loads(os.environ["SELECTED_CANDIDATE_REPORTS"])
assert len(rows) == 1, f"expected one selected-candidate report, got {len(rows)}"
report = rows[0]["jsonPayload"]

assert report["status"] == "verified", report
assert report.get("source_workflow_run_id"), report
assert report.get("selected_candidate_id"), report
assert report.get("selected_case_type") in {"INDEPENDENT", "FRANCHISE"}, report
assert report.get("property_input_id"), report
assert report.get("recompute_workflow_run_id"), report
assert report["source_workflow_run_id"] != report["recompute_workflow_run_id"], report
assert report.get("result_freshness") == "CURRENT", report

recomputed = set(report.get("recomputed_stage_codes", []))
assert {
    "CALCULATE_GATE_RANK",
    "CANDIDATE_AUDIT",
    "COMMIT_RESULT",
} <= recomputed, report
assert report.get("reused_stage_count", 0) > 0, report
assert report.get("decision_delta_present") is True, report
assert report.get("changed_cost_fields"), report

# These fields prove the selected-candidate preparation guide did not merely
# render empty procedure placeholders: at least one official RAG source must be
# joined to an evidence record and a rendered procedure step.
assert report.get("rag_source_count", 0) > 0, report
assert report.get("rag_evidence_count", 0) > 0, report
assert report.get("rag_procedure_step_count", 0) > 0, report
assert report.get("rag_source_refs"), report

print("PASS FIRST_PROPOSAL result was selected and received real property terms")
print("PASS selected candidate was selectively recomputed with a decision and cost delta")
print("PASS official procedure guidance is grounded by Advanced RAG evidence")
print(json.dumps(report, ensure_ascii=False, sort_keys=True))
PY

printf '%s\n' 'PASS selected-candidate two-stage runtime canary'
