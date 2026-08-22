#!/bin/sh
set -eu

manifest='agents/release-manifest.json'
project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
[ -n "$project_id" ] || { printf '%s\n' 'CAFFEMATE_GCP_PROJECT_ID is required' >&2; exit 2; }
[ -f "$manifest" ] || { printf '%s\n' 'agents/release-manifest.json is required' >&2; exit 2; }

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { printf '%s\n' 'deployment must run from a Git worktree' >&2; exit 2; }
source_revision=$(git rev-parse HEAD)
if [ -n "$(git status --porcelain --untracked-files=all)" ]; then
  printf '%s\n' 'deployment requires a clean working tree' >&2
  exit 2
fi

set -- $(python3 - "$manifest" <<'PY'
import json, re, sys
m = json.load(open(sys.argv[1]))
r = m['runtime']
match = re.fullmatch(r'projects/([^/]+)/locations/([^/]+)/reasoningEngines/([^/]+)', r['resource_name'])
if not match:
    raise SystemExit('invalid runtime resource pin')
print(match.group(1), match.group(2), match.group(3), r['source_revision'], r['image_uri'])
PY
)
manifest_project=$1
region=$2
runtime_id=$3
pinned_revision=$4
pinned_image=$5
[ "$manifest_project" = "$project_id" ] || { printf '%s\n' 'release Runtime belongs to another project' >&2; exit 2; }
if [ "$(gcloud config get-value project 2>/dev/null)" != "$project_id" ]; then
  printf '%s\n' 'active gcloud project does not match requested project' >&2
  exit 2
fi

tagged_image="${region}-docker.pkg.dev/${project_id}/caffemate-agents/caffemate-agent-runtime:${source_revision}"
build_sa="projects/${project_id}/serviceAccounts/caffemate-backend-build@${project_id}.iam.gserviceaccount.com"
if ! gcloud artifacts docker images describe "$tagged_image" --project="$project_id" >/dev/null 2>&1; then
  gcloud builds submit . --project="$project_id" --region="$region" \
    --config=agents/cloudbuild.runtime.yaml --substitutions="_IMAGE=${tagged_image}" \
    --service-account="$build_sa" --quiet
fi
image=$(gcloud artifacts docker images describe "$tagged_image" --project="$project_id" --format='value(image_summary.fully_qualified_digest)')
case "$image" in "${region}-docker.pkg.dev/${project_id}/caffemate-agents/caffemate-agent-runtime@sha256:"*) ;; *) printf '%s\n' 'Runtime image digest is unavailable' >&2; exit 1;; esac

if [ "$source_revision" != "$pinned_revision" ] || [ "$image" != "$pinned_image" ]; then
  printf 'RUNTIME_RELEASE_PIN_REQUIRED source_revision=%s image_uri=%s\n' "$source_revision" "$image" >&2
  exit 3
fi

runtime_spec=$(docker compose -p caffemate-agent-runtime-deploy -f compose.agent.yaml run --rm agent-check sh -lc \
  'npm ci >/dev/null 2>&1 && npm run --silent agent:control -- runtime-spec --json')
payload=$(mktemp)
operation=$(mktemp)
runtime_json=$(mktemp)
trap 'rm -f "$payload" "$operation" "$runtime_json"' EXIT
python3 - "$payload" "$runtime_spec" "$project_id" "$region" "$runtime_id" "$pinned_revision" "$pinned_image" <<'PY'
import json, sys
path, spec_text, project, region, runtime_id, revision, image = sys.argv[1:]
spec = json.loads(spec_text)
if not spec.get('ok'):
    raise SystemExit('runtime-spec failed')
methods = spec['data']['classMethods']
if not any(row.get('name') == 'async_get_release_identity' for row in methods):
    raise SystemExit('async_get_release_identity missing')
json.dump({
    'name': f'projects/{project}/locations/{region}/reasoningEngines/{runtime_id}',
    'spec': {
        'containerSpec': {'imageUri': image},
        'classMethods': methods,
    },
    'labels': {
        'app': 'caffemate',
        'component': 'agent-runtime',
        'source-revision': revision,
    },
}, open(path, 'w'))
PY

token=$(gcloud auth print-access-token)
base="https://${region}-aiplatform.googleapis.com"
resource="projects/${project_id}/locations/${region}/reasoningEngines/${runtime_id}"
curl -fsS -X PATCH \
  -H "Authorization: Bearer ${token}" -H 'Content-Type: application/json' \
  --data-binary "@${payload}" \
  "${base}/v1/${resource}?updateMask=spec.containerSpec,spec.classMethods,labels" >"$operation"
operation_name=$(python3 - "$operation" <<'PY'
import json, sys
value = json.load(open(sys.argv[1]))
name = value.get('name')
if not isinstance(name, str) or '/operations/' not in name:
    raise SystemExit('Runtime PATCH did not return an operation')
print(name)
PY
)

attempt=0
while :; do
  attempt=$((attempt + 1))
  op=$(curl -fsS -H "Authorization: Bearer ${token}" "${base}/v1/${operation_name}")
  state=$(python3 - "$op" <<'PY'
import json, sys
value=json.loads(sys.argv[1])
if value.get('error'):
    print('ERROR')
elif value.get('done'):
    print('DONE')
else:
    print('RUNNING')
PY
)
  printf 'Runtime operation heartbeat attempt=%s state=%s\n' "$attempt" "$state"
  [ "$state" != 'ERROR' ] || { printf '%s\n' "$op" >&2; exit 1; }
  [ "$state" != 'DONE' ] || break
  [ "$attempt" -lt 90 ] || { printf '%s\n' 'Runtime operation did not finish within the bounded deploy window' >&2; exit 1; }
  sleep 10
done

curl -fsS -H "Authorization: Bearer ${token}" "${base}/v1/${resource}" >"$runtime_json"
python3 - "$runtime_json" "$pinned_image" <<'PY'
import json, sys
runtime=json.load(open(sys.argv[1]))
image=sys.argv[2]
assert runtime.get('spec', {}).get('containerSpec', {}).get('imageUri') == image
methods=runtime.get('spec', {}).get('classMethods', [])
assert any(row.get('name') == 'async_get_release_identity' and row.get('api_mode') == 'async' for row in methods)
print('AGENT_RUNTIME_AUTHORITATIVE_READBACK_OK')
PY

printf '%s' "$token" | docker compose -p caffemate-agent-runtime-deploy -f compose.agent.yaml run --rm -T \
  -e "CAFFEMATE_GCP_PROJECT_ID=${project_id}" agent-check sh -lc \
  'npm ci >/dev/null 2>&1 && npm run --silent agent:control -- gcp-preflight --json --access-token-stdin'
