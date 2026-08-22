#!/bin/sh
set -eu

project_id=${CAFFEMATE_GCP_PROJECT_ID:-}
region=${CAFFEMATE_GCP_REGION:-asia-northeast3}
source_revision=${CAFFEMATE_SOURCE_REVISION:-}

. "$(dirname "$0")/build-provenance-helpers.sh"

if [ -z "$project_id" ] || [ "$region" != 'asia-northeast3' ] || [ "${#source_revision}" -ne 40 ]; then
  printf '%s\n' 'project, Seoul region and full source revision are required' >&2
  exit 2
fi
case "$source_revision" in
  *[!0-9a-f]*)
    printf '%s\n' 'source revision must be lowercase hexadecimal' >&2
    exit 2
    ;;
esac
if [ "$(gcloud config get-value project 2>/dev/null)" != "$project_id" ]; then
  printf '%s\n' 'active gcloud project does not match requested project' >&2
  exit 2
fi
if [ "$(git rev-parse HEAD)" != "$source_revision" ] || [ -n "$(git status --porcelain)" ]; then
  printf '%s\n' 'Agent GCP preflight build requires the exact clean source revision' >&2
  exit 2
fi
remote_main=$(git ls-remote origin refs/heads/main | awk '{print $1}')
if [ "$remote_main" != "$source_revision" ]; then
  printf '%s\n' 'Agent GCP preflight must be built from immutable origin/main' >&2
  exit 2
fi

mcp_tagged_image="${region}-docker.pkg.dev/${project_id}/caffemate-backend/mcp:${source_revision}"
preflight_tagged_image="${region}-docker.pkg.dev/${project_id}/caffemate-backend/agent-release-preflight:${source_revision}"
build_sa="projects/${project_id}/serviceAccounts/caffemate-backend-build@${project_id}.iam.gserviceaccount.com"

image_exists=false
preflight_image_exists=false
if gcloud artifacts docker images describe "$mcp_tagged_image" --project="$project_id" >/dev/null 2>&1; then
  image_exists=true
fi
if gcloud artifacts docker images describe "$preflight_tagged_image" --project="$project_id" >/dev/null 2>&1; then
  preflight_image_exists=true
fi

if [ "$image_exists" = true ] || [ "$preflight_image_exists" = true ]; then
  if [ "$image_exists" != true ] || [ "$preflight_image_exists" != true ]; then
    printf '%s\n' 'immutable source tag set is incomplete; create a new source revision' >&2
    exit 1
  fi
  image=$(gcloud artifacts docker images describe "$mcp_tagged_image" \
    --project="$project_id" --format='value(image_summary.fully_qualified_digest)')
  preflight_image=$(gcloud artifacts docker images describe "$preflight_tagged_image" \
    --project="$project_id" --format='value(image_summary.fully_qualified_digest)')
  digest=${image##*@}
  preflight_digest=${preflight_image##*@}
  build_id=$(verified_build_id_for_image \
    "$mcp_tagged_image" "$digest" "$source_revision" "$build_sa" 2>/dev/null || true)
  preflight_build_id=$(verified_build_id_for_image \
    "$preflight_tagged_image" "$preflight_digest" "$source_revision" "$build_sa" 2>/dev/null || true)
  if [ -z "$build_id" ] || [ -z "$preflight_build_id" ] || [ "$build_id" != "$preflight_build_id" ]; then
    printf '%s\n' 'immutable source tag exists without trusted provenance; create a new source revision' >&2
    exit 1
  fi
  printf 'Agent GCP preflight images already verified: source=%s build=%s\n' \
    "$source_revision" "$build_id"
  exit 0
fi

gcloud builds submit --no-source \
  --project="$project_id" \
  --region="$region" \
  --config=cloudbuild.mcp-image.yaml \
  --substitutions="_IMAGE_TAG=${source_revision},_SOURCE_REVISION=${source_revision}" \
  --service-account="$build_sa" \
  --quiet

image=$(gcloud artifacts docker images describe "$mcp_tagged_image" \
  --project="$project_id" --format='value(image_summary.fully_qualified_digest)')
preflight_image=$(gcloud artifacts docker images describe "$preflight_tagged_image" \
  --project="$project_id" --format='value(image_summary.fully_qualified_digest)')
case "$image" in
  "${region}-docker.pkg.dev/${project_id}/caffemate-backend/mcp@sha256:"*) ;;
  *) printf '%s\n' 'MCP image digest is unavailable' >&2; exit 1 ;;
esac
case "$preflight_image" in
  "${region}-docker.pkg.dev/${project_id}/caffemate-backend/agent-release-preflight@sha256:"*) ;;
  *) printf '%s\n' 'Agent release-preflight image digest is unavailable' >&2; exit 1 ;;
esac
digest=${image##*@}
preflight_digest=${preflight_image##*@}
build_id=$(verified_build_id_for_image \
  "$mcp_tagged_image" "$digest" "$source_revision" "$build_sa")
preflight_build_id=$(verified_build_id_for_image \
  "$preflight_tagged_image" "$preflight_digest" "$source_revision" "$build_sa")
if [ "$build_id" != "$preflight_build_id" ]; then
  printf '%s\n' 'MCP runtime and Agent release-preflight artifacts must come from the same Cloud Build' >&2
  exit 1
fi
printf 'Agent GCP preflight images verified: source=%s build=%s\n' \
  "$source_revision" "$build_id"
