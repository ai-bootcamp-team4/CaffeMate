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
remote_main=$(git ls-remote origin refs/heads/main | awk '{print $1}')
[ "$remote_main" = "$source_revision" ] || {
  printf '%s\n' 'only the immutable origin/main candidate can be approved' >&2
  exit 2
}

tagged_image="${region}-docker.pkg.dev/${project_id}/caffemate-agents/caffemate-agent-runtime:${source_revision}"
approved_tag="${region}-docker.pkg.dev/${project_id}/caffemate-agents/caffemate-agent-runtime:approved-${source_revision}"
build_sa="projects/${project_id}/serviceAccounts/caffemate-backend-build@${project_id}.iam.gserviceaccount.com"
image=$(gcloud artifacts docker images describe "$tagged_image" \
  --project="$project_id" --format='value(image_summary.fully_qualified_digest)')
digest=${image##*@}
build_id=$(verified_build_id_for_image \
  "$tagged_image" "$digest" "$source_revision" "$build_sa")
if gcloud artifacts docker images describe "$approved_tag" --project="$project_id" >/dev/null 2>&1; then
  existing=$(gcloud artifacts docker images describe "$approved_tag" \
    --project="$project_id" --format='value(image_summary.fully_qualified_digest)')
  [ "$existing" = "$image" ] || {
    printf '%s\n' 'approval tag already points at a different digest' >&2
    exit 1
  }
else
  gcloud artifacts docker tags add "$image" "$approved_tag" \
    --project="$project_id" --quiet >/dev/null
fi
printf 'Agent Runtime release approved: source=%s build=%s image=%s\n' \
  "$source_revision" "$build_id" "$image"
