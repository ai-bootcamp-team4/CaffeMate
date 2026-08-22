#!/bin/sh
set -eu

project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
region=${CAFFEMATE_GCP_REGION:-asia-northeast3}
source_revision=${CAFFEMATE_SOURCE_REVISION:-}

. "$(dirname "$0")/build-provenance-helpers.sh"

[ -n "$project_id" ] && [ "$region" = 'asia-northeast3' ] && [ "${#source_revision}" -eq 40 ] || {
  printf '%s\n' 'project, Seoul region and full source revision are required' >&2
  exit 2
}
[ "$(git rev-parse HEAD)" = "$source_revision" ] && [ -z "$(git status --porcelain)" ] || {
  printf '%s\n' 'Agent Runtime candidate build requires the exact clean source revision' >&2
  exit 2
}
remote_main=$(git ls-remote origin refs/heads/main | awk '{print $1}')
[ "$remote_main" = "$source_revision" ] || {
  printf '%s\n' 'Agent Runtime candidate must be built from immutable origin/main' >&2
  exit 2
}

tagged_image="${region}-docker.pkg.dev/${project_id}/caffemate-agents/caffemate-agent-runtime:${source_revision}"
build_sa="projects/${project_id}/serviceAccounts/caffemate-backend-build@${project_id}.iam.gserviceaccount.com"
gcloud builds submit --no-source \
  --project="$project_id" \
  --region="$region" \
  --config=agents/cloudbuild.runtime.yaml \
  --substitutions="_IMAGE=${tagged_image},_SOURCE_REVISION=${source_revision}" \
  --service-account="$build_sa" \
  --quiet
image=$(gcloud artifacts docker images describe "$tagged_image" \
  --project="$project_id" --format='value(image_summary.fully_qualified_digest)')
digest=${image##*@}
build_id=$(verified_build_id_for_image \
  "$tagged_image" "$digest" "$source_revision" "$build_sa")
printf 'Agent Runtime candidate: source=%s build=%s image=%s\n' \
  "$source_revision" "$build_id" "$image"
printf '%s\n' 'Run approve-agent-runtime-release.sh only after reviewing this immutable candidate.'
