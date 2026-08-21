import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import rfc8785

from app.contracts.schema_registry import AgentContractValidator, ContractRegistry
from app.domain.errors import ContractValidationError
from app.workflows.stage_context import StageContext

DIGEST_FIELDS = (
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


def compute_agent_input_digest(task: dict[str, Any]) -> str:
    projection = {field: task[field] for field in DIGEST_FIELDS}
    return f"sha256:{hashlib.sha256(rfc8785.dumps(projection)).hexdigest()}"


class AgentTaskFactory:
    def __init__(
        self,
        *,
        now: Callable[[], datetime] | None = None,
        new_invocation_id: Callable[[], str] | None = None,
        contracts: AgentContractValidator | None = None,
        repository_root: Path | None = None,
    ) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self._new_invocation_id = new_invocation_id or (lambda: str(uuid4()))
        self._contracts = contracts or ContractRegistry()
        root = repository_root or Path(__file__).resolve().parents[3]
        self._release = self._load_json(root / "agents" / "release-manifest.json")
        self._mcp_manifest = self._load_json(
            root / "docs" / "contracts" / "mcp-tool-manifest.json"
        )

    def build_evidence_plan(self, context: StageContext) -> dict[str, Any]:
        dependency = context.dependency_results.get("CLAIM_PLAN")
        payload = dependency.get("claim_plan") if dependency else None
        if not isinstance(payload, dict):
            raise ContractValidationError("EVIDENCE_PLAN requires a Claim Plan")
        registry = self._release["tasks"]["EVIDENCE_PLAN"]
        catalog = [
            {
                "tool_name": tool["name"],
                "tool_version": tool["version"],
                "input_schema_id": tool["input_schema_id"],
                "output_schema_id": tool["output_schema_id"],
            }
            for tool in self._mcp_manifest["tools"]
        ]
        deadline = self._now() + timedelta(seconds=20)
        task: dict[str, Any] = {
            "schema_version": "1.0.0",
            "task_id": f"task-{context.lease.stage_run_id}",
            "invocation_id": self._new_invocation_id(),
            "agent_name": registry["agent_name"],
            "task_type": "EVIDENCE_PLAN",
            "workflow_run_id": context.lease.workflow_run_id,
            "stage_run_id": context.lease.stage_run_id,
            "transport_attempt": context.lease.attempt,
            "repair_attempt": 0,
            "venture_project_id": context.project_id,
            "head_fence": context.lease.head.model_dump(mode="json"),
            "prompt_version": registry["prompt_version"],
            "input_schema_id": registry["input_schema_id"],
            "output_schema_id": registry["output_schema_id"],
            "input_artifacts": [],
            "input_digest": "",
            "deadline_at": deadline.isoformat().replace("+00:00", "Z"),
            "runtime_tool_policy": "NO_DIRECT_TOOL_CALLS",
            "tool_manifest_digest": self._release["mcp_manifest_digest"],
            "available_tool_catalog": catalog,
            "payload": payload,
        }
        task["input_digest"] = compute_agent_input_digest(task)
        self._contracts.validate_agent_task(task)
        return task

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"Expected JSON object: {path.name}")
        return value
