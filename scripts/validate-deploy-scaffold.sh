#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"

required_files="
Dockerfile
.dockerignore
cloudbuild.yaml
deploy/nginx/default.conf.template
docs/deployment.md
"

for file in $required_files; do
  if [ ! -f "$file" ]; then
    printf 'missing required deployment file: %s\n' "$file" >&2
    exit 1
  fi
done

if grep -F -- '--allow-unauthenticated' cloudbuild.yaml >/dev/null; then
  printf '%s\n' 'trigger builds must not mutate the public access policy' >&2
  exit 1
fi

for key in _REGION _AR_REPOSITORY _IMAGE_NAME _SERVICE_NAME _RUNTIME_SERVICE_ACCOUNT; do
  if ! grep -F "${key}:" cloudbuild.yaml >/dev/null; then
    printf 'missing Cloud Build substitution: %s\n' "$key" >&2
    exit 1
  fi
done

if ! grep -F 'listen ${PORT};' deploy/nginx/default.conf.template >/dev/null; then
  printf '%s\n' 'nginx does not listen on Cloud Run PORT' >&2
  exit 1
fi

if ! grep -F 'location = /_healthz' deploy/nginx/default.conf.template >/dev/null; then
  printf '%s\n' 'nginx health endpoint is missing' >&2
  exit 1
fi

if ! grep -F 'try_files $uri $uri/ /index.html;' deploy/nginx/default.conf.template >/dev/null; then
  printf '%s\n' 'nginx SPA fallback is missing' >&2
  exit 1
fi

if command -v ruby >/dev/null 2>&1; then
  ruby -e 'require "yaml"; YAML.safe_load(File.read("cloudbuild.yaml"), aliases: true)'
else
  printf '%s\n' 'skip: ruby unavailable; cloudbuild.yaml parse not checked'
fi

if [ "${FULL_DOCKER_BUILD:-0}" = "1" ]; then
  if [ ! -f package.json ] || [ ! -f package-lock.json ]; then
    printf '%s\n' 'cannot build: package.json and package-lock.json are required' >&2
    exit 1
  fi
  if ! command -v docker >/dev/null 2>&1; then
    printf '%s\n' 'cannot build: docker is unavailable' >&2
    exit 1
  fi
  if ! command -v curl >/dev/null 2>&1; then
    printf '%s\n' 'cannot verify container HTTP responses: curl is unavailable' >&2
    exit 1
  fi
  docker build --tag caffemate-web:local .

  container_name="caffemate-web-validate-$$"
  cleanup_container() {
    docker rm -f "$container_name" >/dev/null 2>&1 || true
  }
  trap cleanup_container EXIT INT TERM

  docker run --detach --name "$container_name" \
    --publish 127.0.0.1::8080 caffemate-web:local >/dev/null
  port_mapping=$(docker port "$container_name" 8080/tcp)
  host_port=${port_mapping##*:}

  attempt=0
  while ! curl --fail --silent --output /dev/null \
    "http://127.0.0.1:${host_port}/_healthz"; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
      printf '%s\n' 'container health endpoint did not become ready' >&2
      docker logs "$container_name" >&2
      exit 1
    fi
    sleep 1
  done

  curl --fail --silent --output /dev/null \
    "http://127.0.0.1:${host_port}/"
  printf '%s\n' 'local container /_healthz and / returned HTTP 200'
  cleanup_container
  trap - EXIT INT TERM
else
  printf '%s\n' 'skip: set FULL_DOCKER_BUILD=1 after frontend package files are ready'
fi

printf '%s\n' 'deployment scaffold validation passed'
