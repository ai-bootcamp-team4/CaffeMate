#!/bin/sh

assert_service_account_permission_state() {
  principal_email=$1
  full_resource_name=$2
  permission=$3
  expected_state=$4

  actual_state=$(gcloud policy-intelligence troubleshoot-policy iam \
    "$full_resource_name" \
    --project="$project_id" \
    --principal-email="$principal_email" \
    --permission="$permission" \
    --format='value(overallAccessState)')
  [ "$actual_state" = "$expected_state" ] || {
    printf '%s has %s=%s; expected %s\n' \
      "$principal_email" "$permission" "$actual_state" "$expected_state" >&2
    return 1
  }
}

assert_service_account_permissions_denied() {
  principal_email=$1
  full_resource_name=$2
  permissions=$3
  old_ifs=$IFS
  IFS=,
  for permission in $permissions; do
    assert_service_account_permission_state \
      "$principal_email" "$full_resource_name" "$permission" 'CANNOT_ACCESS'
  done
  IFS=$old_ifs
}
