#!/bin/sh

verified_build_id_for_image() {
  expected_image=$1
  expected_digest=$2
  expected_revision=$3
  expected_build_sa=$4
  expected_repository=${5:-https://github.com/ai-bootcamp-team4/CaffeMate.git}

  builds=$(gcloud builds list \
    --project="$project_id" \
    --region="$region" \
    --filter='status=SUCCESS' \
    --limit=200 \
    --format=json)
  BUILDS_JSON="$builds" \
  EXPECTED_IMAGE="$expected_image" \
  EXPECTED_DIGEST="$expected_digest" \
  EXPECTED_REVISION="$expected_revision" \
  EXPECTED_BUILD_SA="$expected_build_sa" \
  EXPECTED_REPOSITORY="$expected_repository" \
    python3 - <<'PY'
import json
import os
import sys

matches = []
for build in json.loads(os.environ["BUILDS_JSON"]):
    if build.get("substitutions", {}).get("_SOURCE_REVISION") != os.environ["EXPECTED_REVISION"]:
        continue
    if build.get("serviceAccount") != os.environ["EXPECTED_BUILD_SA"]:
        continue
    if build.get("source"):
        continue
    if build.get("availableSecrets"):
        continue
    options = build.get("options", {})
    if options.get("env") or options.get("secretEnv") or options.get("volumes"):
        continue
    steps = build.get("steps", [])
    checkout_index = next(
        (index for index, step in enumerate(steps) if step.get("id") == "checkout-reviewed-source"),
        None,
    )
    checkout = steps[checkout_index] if checkout_index is not None else None
    if checkout is None or checkout_index != 0:
        continue
    if (
        checkout.get("name") != "gcr.io/cloud-builders/git"
        or checkout.get("entrypoint") != "sh"
        or checkout.get("env")
        or checkout.get("secretEnv")
        or checkout.get("volumes")
        or checkout.get("dir")
    ):
        continue
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
    checkout_args = checkout.get("args", [])
    if (
        not isinstance(checkout_args, list)
        or len(checkout_args) != 2
        or checkout_args[0] != "-ceu"
        or not isinstance(checkout_args[1], str)
        or checkout_args[1].strip() != expected_checkout_script
    ):
        continue
    expected_image = os.environ["EXPECTED_IMAGE"]
    if "/caffemate-agents/caffemate-agent-runtime:" in expected_image:
        component = "agent-runtime"
        build_step_id = "build-agent-runtime-image"
        expected_dockerfile = "/workspace/source/agents/Dockerfile.runtime"
    elif "/caffemate-backend/mcp:" in expected_image:
        component = "mcp"
        build_step_id = "build-mcp-image"
        expected_dockerfile = "/workspace/source/deploy/mcp.Dockerfile"
    else:
        continue
    build_index = next(
        (index for index, step in enumerate(steps) if step.get("id") == build_step_id),
        None,
    )
    if build_index != 1:
        continue
    build_step = steps[build_index]
    if build_step.get("name") != "gcr.io/cloud-builders/docker":
        continue
    expected_build_env = [] if component == "agent-runtime" else ["DOCKER_BUILDKIT=1"]
    if (
        build_step.get("env", []) != expected_build_env
        or build_step.get("secretEnv")
        or build_step.get("volumes")
        or build_step.get("dir")
        or build_step.get("entrypoint")
    ):
        continue
    args = build_step.get("args", [])
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        continue
    expected_args = [
        "build",
        "--file",
        expected_dockerfile,
        "--tag",
        expected_image,
        "/workspace/source",
    ]
    if args != expected_args:
        continue
    if component == "agent-runtime":
        if len(steps) != 2:
            continue
    else:
        if len(steps) != 3:
            continue
        push_step = steps[2]
        if (
            push_step.get("id") != "push-mcp-image"
            or push_step.get("name") != "gcr.io/cloud-builders/docker"
            or push_step.get("args") != ["push", expected_image]
            or push_step.get("env")
            or push_step.get("secretEnv")
            or push_step.get("volumes")
            or push_step.get("dir")
            or push_step.get("entrypoint")
        ):
            continue
    for image in build.get("results", {}).get("images", []):
        if (
            image.get("name") == os.environ["EXPECTED_IMAGE"]
            and image.get("digest") == os.environ["EXPECTED_DIGEST"]
        ):
            matches.append(build.get("id"))
if not matches or not matches[0]:
    print("a successful source-bound Cloud Build is required", file=sys.stderr)
    raise SystemExit(1)
print(matches[0])
PY
}
