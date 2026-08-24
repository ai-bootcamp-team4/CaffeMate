#!/bin/sh
set -eu

project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
region=${CAFFEMATE_GCP_REGION:-asia-northeast3}
source_revision=${CAFFEMATE_SOURCE_REVISION:-}
bucket_name=${CAFFEMATE_EVALUATION_BUCKET:-${project_id}-caffemate-evaluation}
pipeline_sa="caffemate-evaluation-pipeline@${project_id}.iam.gserviceaccount.com"
compiled='pipelines/compiled/caffemate-operational-evaluation.json'

if [ -z "$project_id" ] || [ -z "$source_revision" ]; then
  printf '%s\n' 'project id and source revision are required' >&2
  exit 2
fi
[ -f "$compiled" ] || {
  printf '%s\n' 'compiled operational evaluation pipeline is missing' >&2
  exit 2
}
[ "$(git rev-parse HEAD)" = "$source_revision" ] || {
  printf '%s\n' 'checked-out source differs from requested revision' >&2
  exit 2
}
[ -z "$(git status --porcelain)" ] || {
  printf '%s\n' 'pipeline submission requires a clean checkout' >&2
  exit 2
}
[ "$(git ls-remote origin refs/heads/main | awk '{print $1}')" = "$source_revision" ] || {
  printf '%s\n' 'pipeline source must be immutable origin/main' >&2
  exit 2
}

run_stamp=$(date -u '+%Y%m%d%H%M%S')
job_id="caffemate-operational-${run_stamp}"
report_uri="gs://${bucket_name}/reports/${job_id}-${source_revision}.json"
gcs_output="gs://${bucket_name}/pipeline-root/${job_id}"
temp_dir=$(mktemp -d)
trap 'rm -rf "$temp_dir"' EXIT
request_file="${temp_dir}/request.json"
response_file="${temp_dir}/response.json"

COMPILED="$compiled" REQUEST_FILE="$request_file" PROJECT_ID="$project_id" \
REGION="$region" SOURCE_REVISION="$source_revision" REPORT_URI="$report_uri" \
GCS_OUTPUT="$gcs_output" PIPELINE_SA="$pipeline_sa" python3 - <<'PY'
import json
import os
from pathlib import Path

pipeline_spec = json.loads(Path(os.environ["COMPILED"]).read_text(encoding="utf-8"))
request = {
    "displayName": f"CaffeMate operational evaluation {os.environ['SOURCE_REVISION'][:12]}",
    "pipelineSpec": pipeline_spec,
    "runtimeConfig": {
        "gcsOutputDirectory": os.environ["GCS_OUTPUT"],
        "parameterValues": {
            "project_id": os.environ["PROJECT_ID"],
            "region": os.environ["REGION"],
            "live_job_name": "caffemate-live-e2e-evaluation",
            "live_report_uri": os.environ["REPORT_URI"],
        },
    },
    "serviceAccount": os.environ["PIPELINE_SA"],
    "labels": {
        "managed-by": "caffemate-evaluation",
        "source-revision": os.environ["SOURCE_REVISION"][:40],
    },
}
Path(os.environ["REQUEST_FILE"]).write_text(
    json.dumps(request, ensure_ascii=False), encoding="utf-8"
)
PY

endpoint="https://${region}-aiplatform.googleapis.com/v1/projects/${project_id}/locations/${region}/pipelineJobs"
token=$(gcloud auth print-access-token)
curl -fsS \
  -H "Authorization: Bearer ${token}" \
  -H 'Content-Type: application/json' \
  -X POST \
  --data-binary "@${request_file}" \
  "${endpoint}?pipelineJobId=${job_id}" >"$response_file"
pipeline_name=$(RESPONSE_FILE="$response_file" python3 - <<'PY'
import json
import os
from pathlib import Path

response = json.loads(Path(os.environ["RESPONSE_FILE"]).read_text(encoding="utf-8"))
name = response.get("name")
if not isinstance(name, str) or not name:
    raise SystemExit(f"pipeline submission failed: {response}")
print(name)
PY
)

state='PIPELINE_STATE_PENDING'
while [ "$state" != 'PIPELINE_STATE_SUCCEEDED' ]; do
  token=$(gcloud auth print-access-token)
  curl -fsS -H "Authorization: Bearer ${token}" \
    "https://${region}-aiplatform.googleapis.com/v1/${pipeline_name}" >"$response_file"
  state=$(RESPONSE_FILE="$response_file" python3 - <<'PY'
import json
import os
from pathlib import Path

response = json.loads(Path(os.environ["RESPONSE_FILE"]).read_text(encoding="utf-8"))
print(response.get("state", "PIPELINE_STATE_PENDING"))
PY
)
  case "$state" in
    PIPELINE_STATE_SUCCEEDED) ;;
    PIPELINE_STATE_FAILED|PIPELINE_STATE_CANCELLED|PIPELINE_STATE_PAUSED)
      printf 'pipeline ended in %s\n' "$state" >&2
      exit 1
      ;;
    *) sleep 20 ;;
  esac
done

gcloud storage cat "$report_uri" | python3 -c '
import json, sys
report = json.load(sys.stdin)
summary = report["summary"]
assert summary["total_cases"] == 15, report
assert summary["failed_cases"] == 0, report
assert report["passed"] is True, report
passed = summary["passed_cases"]
print(f"PASS live E2E {passed}/15")
'
printf 'pipeline: https://console.cloud.google.com/vertex-ai/pipelines/locations/%s/runs/%s?project=%s\n' \
  "$region" "$job_id" "$project_id"
printf 'report: %s\n' "$report_uri"
