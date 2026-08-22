#!/bin/sh

verified_build_id_for_image() {
  expected_image=$1
  expected_digest=$2
  expected_revision=$3
  expected_build_sa=$4
  expected_repository=${5:-https://github.com/ai-bootcamp-team4/CaffeMate.git}

  builds_file=$(mktemp "${TMPDIR:-/tmp}/caffemate-builds.XXXXXX")
  if ! gcloud builds list \
    --project="$project_id" \
    --region="$region" \
    --filter='status=SUCCESS' \
    --limit=200 \
    --format=json >"$builds_file"; then
    rm -f "$builds_file"
    return 1
  fi
  if BUILDS_FILE="$builds_file" \
    EXPECTED_IMAGE="$expected_image" \
    EXPECTED_DIGEST="$expected_digest" \
    EXPECTED_REVISION="$expected_revision" \
    EXPECTED_BUILD_SA="$expected_build_sa" \
    EXPECTED_REPOSITORY="$expected_repository" \
    python3 - <<'PY'
import json
import os
import sys


def step_is_clean(step, *, name, args, env=None, entrypoint=None):
    return (
        step.get("name") == name
        and step.get("args", []) == args
        and step.get("env", []) == (env or [])
        and step.get("entrypoint") == entrypoint
        and not step.get("secretEnv")
        and not step.get("volumes")
        and not step.get("dir")
    )


def checkout_is_exact(step):
    expected_checkout_script = "\n".join(
        [
            "git init /workspace/source",
            (
                "git -C /workspace/source remote add origin "
                f"'{os.environ['EXPECTED_REPOSITORY']}'"
            ),
            (
                "git -C /workspace/source fetch --depth=1 origin "
                f"'{os.environ['EXPECTED_REVISION']}'"
            ),
            "git -C /workspace/source checkout --detach FETCH_HEAD",
            (
                'test "$(git -C /workspace/source rev-parse HEAD)" = '
                f"'{os.environ['EXPECTED_REVISION']}'"
            ),
        ]
    )
    args = step.get("args", [])
    return (
        step.get("id") == "checkout-reviewed-source"
        and step.get("name") == "gcr.io/cloud-builders/git"
        and step.get("entrypoint") == "sh"
        and step.get("env", []) == []
        and not step.get("secretEnv")
        and not step.get("volumes")
        and not step.get("dir")
        and isinstance(args, list)
        and len(args) == 2
        and args[0] == "-ceu"
        and isinstance(args[1], str)
        and args[1].strip() == expected_checkout_script
    )


def mcp_build_shape_is_exact(steps, selected_image):
    if len(steps) != 5:
        return False
    if "/caffemate-backend/mcp:" in selected_image:
        mcp_image = selected_image
        preflight_image = selected_image.replace(
            "/caffemate-backend/mcp:",
            "/caffemate-backend/agent-release-preflight:",
            1,
        )
    elif "/caffemate-backend/agent-release-preflight:" in selected_image:
        preflight_image = selected_image
        mcp_image = selected_image.replace(
            "/caffemate-backend/agent-release-preflight:",
            "/caffemate-backend/mcp:",
            1,
        )
    else:
        return False

    dockerfile = "/workspace/source/deploy/mcp.Dockerfile"
    expected = [
        (
            "build-mcp-image",
            [
                "build",
                "--target",
                "runtime",
                "--file",
                dockerfile,
                "--tag",
                mcp_image,
                "/workspace/source",
            ],
            ["DOCKER_BUILDKIT=1"],
        ),
        (
            "build-agent-release-preflight-image",
            [
                "build",
                "--target",
                "release-preflight",
                "--file",
                dockerfile,
                "--tag",
                preflight_image,
                "/workspace/source",
            ],
            ["DOCKER_BUILDKIT=1"],
        ),
        ("push-mcp-image", ["push", mcp_image], []),
        (
            "push-agent-release-preflight-image",
            ["push", preflight_image],
            [],
        ),
    ]
    for step, (step_id, args, env) in zip(steps[1:], expected, strict=True):
        if step.get("id") != step_id:
            return False
        if not step_is_clean(
            step,
            name="gcr.io/cloud-builders/docker",
            args=args,
            env=env,
        ):
            return False
    return True


matches = []
with open(os.environ["BUILDS_FILE"], encoding="utf-8") as builds_handle:
    builds = json.load(builds_handle)

for build in builds:
    if build.get("substitutions", {}).get("_SOURCE_REVISION") != os.environ["EXPECTED_REVISION"]:
        continue
    if build.get("serviceAccount") != os.environ["EXPECTED_BUILD_SA"]:
        continue
    if build.get("source") or build.get("availableSecrets"):
        continue
    options = build.get("options", {})
    if options.get("env") or options.get("secretEnv") or options.get("volumes"):
        continue

    steps = build.get("steps", [])
    if not steps or not checkout_is_exact(steps[0]):
        continue

    selected_image = os.environ["EXPECTED_IMAGE"]
    if "/caffemate-agents/caffemate-agent-runtime:" in selected_image:
        if len(steps) != 2:
            continue
        runtime_args = [
            "build",
            "--file",
            "/workspace/source/agents/Dockerfile.runtime",
            "--tag",
            selected_image,
            "/workspace/source",
        ]
        if steps[1].get("id") != "build-agent-runtime-image":
            continue
        if not step_is_clean(
            steps[1],
            name="gcr.io/cloud-builders/docker",
            args=runtime_args,
        ):
            continue
    elif (
        "/caffemate-backend/mcp:" in selected_image
        or "/caffemate-backend/agent-release-preflight:" in selected_image
    ):
        if not mcp_build_shape_is_exact(steps, selected_image):
            continue
    else:
        continue

    for image in build.get("results", {}).get("images", []):
        if (
            image.get("name") == selected_image
            and image.get("digest") == os.environ["EXPECTED_DIGEST"]
        ):
            matches.append(build.get("id"))

if not matches or not matches[0]:
    print("a successful source-bound Cloud Build is required", file=sys.stderr)
    raise SystemExit(1)
print(matches[0])
PY
  then
    provenance_status=0
  else
    provenance_status=$?
  fi
  rm -f "$builds_file"
  return "$provenance_status"
}
