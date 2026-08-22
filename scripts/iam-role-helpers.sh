#!/bin/sh

ensure_project_custom_role() {
  role_id=$1
  role_title=$2
  role_description=$3
  role_permissions=$4

  if gcloud iam roles describe "$role_id" --project="$project_id" >/dev/null 2>&1; then
    gcloud iam roles update "$role_id" \
      --project="$project_id" \
      --title="$role_title" \
      --description="$role_description" \
      --permissions="$role_permissions" \
      --stage=BETA \
      --quiet >/dev/null
  else
    gcloud iam roles create "$role_id" \
      --project="$project_id" \
      --title="$role_title" \
      --description="$role_description" \
      --permissions="$role_permissions" \
      --stage=BETA \
      --quiet >/dev/null
  fi
}

remove_project_role_binding() {
  member=$1
  role=$2
  binding=$(gcloud projects get-iam-policy "$project_id" \
    --flatten='bindings[].members' \
    --filter="bindings.role=${role} AND bindings.members=${member}" \
    --format='value(bindings.members)')
  if [ -z "$binding" ]; then
    return 0
  fi
  gcloud projects remove-iam-policy-binding "$project_id" \
    --member="$member" \
    --role="$role" \
    --condition=None \
    --quiet >/dev/null
  binding=$(gcloud projects get-iam-policy "$project_id" \
    --flatten='bindings[].members' \
    --filter="bindings.role=${role} AND bindings.members=${member}" \
    --format='value(bindings.members)')
  [ -z "$binding" ] || {
    printf 'role %s remains bound to %s\n' "$role" "$member" >&2
    return 1
  }
}
