#!/usr/bin/env bash
set -euo pipefail

repo_dir="${1:-/workspace/repo}"
output_dir="${2:-/workspace}"
requested_scope="${3:-auto}"

case "$requested_scope" in
  auto | all | web | backend | none) ;;
  *)
    echo "unsupported deploy scope: $requested_scope" >&2
    exit 2
    ;;
esac

deploy_web=false
deploy_backend=false
changed_files_path="${output_dir}/changed-files.txt"
runtime_files_path="${output_dir}/runtime-files.txt"

mkdir -p "$output_dir"

if [ "$requested_scope" = "all" ] || [ "$requested_scope" = "web" ]; then
  deploy_web=true
fi
if [ "$requested_scope" = "all" ] || [ "$requested_scope" = "backend" ]; then
  deploy_backend=true
fi

if [ "$requested_scope" = "auto" ]; then
  revision="$(git -C "$repo_dir" rev-parse HEAD)"
  if git -C "$repo_dir" cat-file -e "${revision}^" 2>/dev/null; then
    # Compare the deployed tree to its first parent. Plain diff-tree omits paths
    # for merge commits, which can silently classify a --no-ff main merge as a
    # no-op even when the merged branch changes runtime inputs.
    git -C "$repo_dir" diff-tree --no-commit-id --name-only -r \
      "${revision}^1" "$revision" \
      | sort -u > "$changed_files_path"
  else
    git -C "$repo_dir" ls-tree -r --name-only "$revision" \
      | sort -u > "$changed_files_path"
    deploy_web=true
    deploy_backend=true
  fi

  grep -Ev '(^|/)(tests?/|[^/]+\.test\.(ts|tsx)$)' "$changed_files_path" \
    > "$runtime_files_path" || true

  if grep -Eq \
    '^(src/|public/|deploy/nginx/|Dockerfile$|index\.html$|package(-lock)?\.json$|tsconfig(\.[^.]+)?\.json$|vite\.config\.[^/]+$)' \
    "$runtime_files_path"; then
    deploy_web=true
  fi

  if grep -Eq \
    '^(api/app/|api/migrations/|api/pyproject\.toml$|api/uv\.lock$|worker/|agents/release-manifest\.json$|agents/fixtures/|docs/contracts/|deploy/backend\.Dockerfile$)' \
    "$runtime_files_path"; then
    deploy_backend=true
  fi
else
  : > "$changed_files_path"
  : > "$runtime_files_path"
fi

printf '%s\n' "$deploy_web" > "${output_dir}/deploy-web"
printf '%s\n' "$deploy_backend" > "${output_dir}/deploy-backend"

echo "deploy scope: requested=${requested_scope} web=${deploy_web} backend=${deploy_backend}"
if [ -s "$changed_files_path" ]; then
  echo "changed files:"
  sed 's/^/  - /' "$changed_files_path"
fi
