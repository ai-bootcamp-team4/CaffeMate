import hashlib
import json
from pathlib import Path
from typing import Any

import rfc8785

_DIGEST_FIELDS = (
    "schema_version",
    "task_id",
    "agent_name",
    "task_type",
    "workflow_run_id",
    "stage_run_id",
    "venture_project_id",
    "head_fence",
    "prompt_version",
    "input_schema_id",
    "output_schema_id",
    "input_artifacts",
    "runtime_tool_policy",
    "tool_manifest_digest",
    "available_tool_catalog",
    "payload",
)


def _input_digest(task: dict[str, Any]) -> str:
    projection = {field: task[field] for field in _DIGEST_FIELDS}
    digest = hashlib.sha256(rfc8785.dumps(projection)).hexdigest()
    return f"sha256:{digest}"


def test_agent_fixture_input_digests_match_backend_rfc8785() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    fixture_matrix = json.loads(
        (repo_root / "agents/fixtures/task-matrix.json").read_text(encoding="utf-8")
    )

    for fixture in fixture_matrix["cases"]:
        task = fixture["task"]
        assert task["input_digest"] == _input_digest(task), fixture["id"]