#!/bin/sh
set -eu

escape_json() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/</\\u003c/g'
}

control_api_base_url=$(escape_json "${CONTROL_API_BASE_URL:-}")
firebase_api_key=$(escape_json "${FIREBASE_API_KEY:-}")
firebase_auth_domain=$(escape_json "${FIREBASE_AUTH_DOMAIN:-}")
firebase_project_id=$(escape_json "${FIREBASE_PROJECT_ID:-}")
firebase_app_id=$(escape_json "${FIREBASE_APP_ID:-}")

{
  printf 'window.__CAFFEMATE_CONFIG__ = {'
  printf 'CONTROL_API_BASE_URL:"%s",' "$control_api_base_url"
  printf 'FIREBASE_API_KEY:"%s",' "$firebase_api_key"
  printf 'FIREBASE_AUTH_DOMAIN:"%s",' "$firebase_auth_domain"
  printf 'FIREBASE_PROJECT_ID:"%s",' "$firebase_project_id"
  printf 'FIREBASE_APP_ID:"%s"' "$firebase_app_id"
  printf '}\n'
} > /usr/share/nginx/html/runtime-config.js
