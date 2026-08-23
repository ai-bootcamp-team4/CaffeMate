import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import rfc8785

from app.contracts.schema_registry import AgentContractValidator, ContractRegistry
from app.domain.errors import ContractValidationError
from app.domain.models import VentureState
from app.workflows.models import HeadFence
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

FEEDBACK_ALLOWED_FIELD_PATHS = (
    "/founder/avoidances",
    "/founder/borrowing_intent",
    "/founder/cafe_type_preference",
    "/founder/max_loss_krw",
    "/founder/operation_mode",
    "/founder/own_funds_krw",
    "/founder/preferences",
    "/founder/target_area_input",
)

MAX_EVIDENCE_ASSESS_CANDIDATES_PER_ACTION = 1


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
        self._mcp_manifest = self._load_json(root / "docs" / "contracts" / "mcp-tool-manifest.json")

    def _deadline_for(self, task_type: str) -> datetime:
        registration = self._release.get("tasks", {}).get(task_type)
        seconds = registration.get("deadline_seconds") if isinstance(registration, dict) else None
        if not isinstance(seconds, int) or seconds <= 0:
            raise ContractValidationError(f"{task_type} deadline registry is invalid")
        return self._now() + timedelta(seconds=seconds)

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
        deadline = self._deadline_for("EVIDENCE_PLAN")
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

    def build_intent_delta(
        self,
        *,
        project_id: str,
        workflow_run_id: str,
        preview_id: str,
        head: HeadFence,
        state: VentureState,
        latest_user_input: str,
        current_candidate_refs: list[str],
    ) -> dict[str, Any]:
        if state.project_id != project_id:
            raise ContractValidationError("Feedback State crossed project scope")
        if not latest_user_input.strip():
            raise ContractValidationError("Feedback input must not be empty")
        registry = self._release["tasks"]["INTENT_DELTA"]
        deadline = self._deadline_for("INTENT_DELTA")
        task: dict[str, Any] = {
            "schema_version": "1.0.0",
            "task_id": f"task-feedback-{preview_id}",
            "invocation_id": self._new_invocation_id(),
            "agent_name": registry["agent_name"],
            "task_type": "INTENT_DELTA",
            "workflow_run_id": workflow_run_id,
            "stage_run_id": f"feedback-preview-{preview_id}",
            "transport_attempt": 1,
            "repair_attempt": 0,
            "venture_project_id": project_id,
            "head_fence": head.model_dump(mode="json"),
            "prompt_version": registry["prompt_version"],
            "input_schema_id": registry["input_schema_id"],
            "output_schema_id": registry["output_schema_id"],
            "input_artifacts": [],
            "input_digest": "",
            "deadline_at": deadline.isoformat().replace("+00:00", "Z"),
            "runtime_tool_policy": "NO_DIRECT_TOOL_CALLS",
            "tool_manifest_digest": None,
            "available_tool_catalog": [],
            "payload": {
                "current_state_projection": self._state_projection(state),
                "latest_user_input": latest_user_input.strip(),
                "allowed_field_paths": list(FEEDBACK_ALLOWED_FIELD_PATHS),
                "current_candidate_refs": sorted(set(current_candidate_refs)),
                "operation_id_pool": [
                    f"feedback-{preview_id}-op-{index:02d}" for index in range(1, 21)
                ],
            },
        }
        task["input_digest"] = compute_agent_input_digest(task)
        self._contracts.validate_agent_task(task)
        return task

    def build_document_extract(
        self,
        *,
        project_id: str,
        document_id: str,
        document_revision_id: str,
        document_type: str,
        checksum: str,
        head: HeadFence,
        parser_blocks: list[dict[str, Any]],
        claim_types: list[str],
        batch_index: int,
    ) -> dict[str, Any]:
        if not parser_blocks or len(parser_blocks) > 12:
            raise ContractValidationError("DOCUMENT_EXTRACT requires 1..12 parser blocks")
        registry = self._release["tasks"]["DOCUMENT_EXTRACT"]
        task_id = f"task-document-{document_revision_id}-{batch_index}"
        deadline = self._deadline_for("DOCUMENT_EXTRACT")
        task: dict[str, Any] = {
            "schema_version": "1.0.0",
            "task_id": task_id,
            "invocation_id": self._new_invocation_id(),
            "agent_name": registry["agent_name"],
            "task_type": "DOCUMENT_EXTRACT",
            "workflow_run_id": f"document-{document_revision_id}",
            "stage_run_id": f"document-extract-{document_revision_id}-{batch_index}",
            "transport_attempt": 1,
            "repair_attempt": 0,
            "venture_project_id": project_id,
            "head_fence": head.model_dump(mode="json"),
            "prompt_version": registry["prompt_version"],
            "input_schema_id": registry["input_schema_id"],
            "output_schema_id": registry["output_schema_id"],
            "input_artifacts": [],
            "input_digest": "",
            "deadline_at": deadline.isoformat().replace("+00:00", "Z"),
            "runtime_tool_policy": "NO_DIRECT_TOOL_CALLS",
            "tool_manifest_digest": None,
            "available_tool_catalog": [],
            "payload": {
                "document_revision": {
                    "document_id": document_id,
                    "document_revision_id": document_revision_id,
                    "document_type": document_type,
                    "checksum": f"sha256:{checksum}",
                },
                "extraction_contract": {"claim_types": sorted(set(claim_types))},
                "parser_blocks": parser_blocks,
                "claim_id_pool": [
                    f"doc-claim-{document_revision_id}-{batch_index}-{index:02d}"
                    for index in range(1, 101)
                ],
            },
        }
        task["input_digest"] = compute_agent_input_digest(task)
        self._contracts.validate_agent_task(task)
        return task

    def build_evidence_assess(self, context: StageContext) -> dict[str, Any]:
        dependency = context.dependency_results.get("EVIDENCE_RETRIEVAL")
        retrieval = dependency.get("evidence_retrieval") if dependency else None
        if not isinstance(retrieval, dict):
            raise ContractValidationError("EVIDENCE_ASSESS requires Evidence Retrieval results")
        claims = retrieval.get("claims")
        executed_actions = retrieval.get("executed_actions")
        if not isinstance(claims, list) or not claims:
            raise ContractValidationError("EVIDENCE_ASSESS requires Claims")
        if not isinstance(executed_actions, list):
            raise ContractValidationError("EVIDENCE_ASSESS executed actions are invalid")
        projected_actions = self._project_evidence_assess_actions(executed_actions)
        registry = self._release["tasks"]["EVIDENCE_ASSESS"]
        deadline = self._deadline_for("EVIDENCE_ASSESS")
        task: dict[str, Any] = {
            "schema_version": "1.0.0",
            "task_id": f"task-{context.lease.stage_run_id}",
            "invocation_id": self._new_invocation_id(),
            "agent_name": registry["agent_name"],
            "task_type": "EVIDENCE_ASSESS",
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
            "tool_manifest_digest": None,
            "available_tool_catalog": [],
            "payload": {
                "claims": claims,
                "executed_actions": projected_actions,
            },
        }
        task["input_digest"] = compute_agent_input_digest(task)
        self._contracts.validate_agent_task(task)
        return task

    @staticmethod
    def _project_evidence_assess_actions(
        executed_actions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build the smallest schema-valid semantic assessment projection.

        Retrieval keeps every logical action and the complete MCP result. The Agent
        receives one copy of an identical physical result per Claim, only the
        highest-ranked Evidence candidates, and no provider-specific data rows.
        Evidence Freeze later consumes the original retrieval result, not this
        bounded projection.
        """
        projected: list[dict[str, Any]] = []
        seen_results: set[tuple[str, str, str]] = set()
        for action in executed_actions:
            if not isinstance(action, dict):
                raise ContractValidationError("EVIDENCE_ASSESS executed action is invalid")
            identity = (
                str(action.get("claim_id", "")),
                str(action.get("tool_name", "")),
                str(action.get("request_id", "")),
            )
            if identity in seen_results:
                continue
            seen_results.add(identity)

            bounded = deepcopy(action)
            structured_result = bounded.get("structured_result")
            if not isinstance(structured_result, dict):
                raise ContractValidationError("EVIDENCE_ASSESS structured result is invalid")
            records = structured_result.get("evidence_records")
            source_trace = structured_result.get("source_trace")
            data = structured_result.get("data")
            if not isinstance(records, list) or not isinstance(source_trace, list):
                raise ContractValidationError("EVIDENCE_ASSESS Evidence projection is invalid")
            if not isinstance(data, list):
                raise ContractValidationError("EVIDENCE_ASSESS tool data projection is invalid")
            structured_result["evidence_records"] = records[
                :MAX_EVIDENCE_ASSESS_CANDIDATES_PER_ACTION
            ]
            structured_result["source_trace"] = source_trace[
                :MAX_EVIDENCE_ASSESS_CANDIDATES_PER_ACTION
            ]
            structured_result["data"] = []
            projected.append(bounded)
        return projected

    def build_independent_proposal(self, context: StageContext) -> dict[str, Any]:
        return self._build_proposal(
            context,
            task_type="PROPOSE_INDEPENDENT",
            dependency_code="INDEPENDENT_SEED",
            dependency_key="independent_seed",
            candidate_collection="model_seeds",
        )

    def build_independent_proposal_tasks(self, context: StageContext) -> list[dict[str, Any]]:
        return self._build_proposal_tasks(
            context,
            task_type="PROPOSE_INDEPENDENT",
            dependency_code="INDEPENDENT_SEED",
            dependency_key="independent_seed",
            candidate_collection="model_seeds",
        )

    def build_franchise_proposal(self, context: StageContext) -> dict[str, Any]:
        return self._build_proposal(
            context,
            task_type="PROPOSE_FRANCHISE",
            dependency_code="FRANCHISE_ELIGIBILITY",
            dependency_key="franchise_eligibility",
            candidate_collection="franchise_universe",
        )

    def build_franchise_proposal_tasks(self, context: StageContext) -> list[dict[str, Any]]:
        return self._build_proposal_tasks(
            context,
            task_type="PROPOSE_FRANCHISE",
            dependency_code="FRANCHISE_ELIGIBILITY",
            dependency_key="franchise_eligibility",
            candidate_collection="franchise_universe",
        )

    def build_candidate_audit(
        self,
        context: StageContext,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        registry = self._release["tasks"]["CANDIDATE_AUDIT"]
        deadline = self._deadline_for("CANDIDATE_AUDIT")
        task: dict[str, Any] = {
            "schema_version": "1.0.0",
            "task_id": f"task-{context.lease.stage_run_id}",
            "invocation_id": self._new_invocation_id(),
            "agent_name": registry["agent_name"],
            "task_type": "CANDIDATE_AUDIT",
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
            "tool_manifest_digest": None,
            "available_tool_catalog": [],
            "payload": payload,
        }
        task["input_digest"] = compute_agent_input_digest(task)
        self._contracts.validate_agent_task(task)
        return task

    def _build_proposal(
        self,
        context: StageContext,
        *,
        task_type: str,
        dependency_code: str,
        dependency_key: str,
        candidate_collection: str,
    ) -> dict[str, Any]:
        dependency = context.dependency_results.get(dependency_code)
        prepared = dependency.get(dependency_key) if dependency else None
        proposal_input = prepared.get("proposal_input") if isinstance(prepared, dict) else None
        if not isinstance(proposal_input, dict):
            raise ContractValidationError(f"{task_type} requires prepared candidate input")
        candidates = proposal_input.get(candidate_collection)
        requested_count = proposal_input.get("requested_candidate_count")
        if (
            not isinstance(candidates, list)
            or not candidates
            or not isinstance(requested_count, int)
            or requested_count < 1
        ):
            raise ContractValidationError(f"{task_type} has no eligible candidate input")
        registry = self._release["tasks"][task_type]
        deadline = self._deadline_for(task_type)
        task: dict[str, Any] = {
            "schema_version": "1.0.0",
            "task_id": f"task-{context.lease.stage_run_id}",
            "invocation_id": self._new_invocation_id(),
            "agent_name": registry["agent_name"],
            "task_type": task_type,
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
            "tool_manifest_digest": None,
            "available_tool_catalog": [],
            "payload": proposal_input,
        }
        task["input_digest"] = compute_agent_input_digest(task)
        self._contracts.validate_agent_task(task)
        return task

    def _build_proposal_tasks(
        self,
        context: StageContext,
        *,
        task_type: str,
        dependency_code: str,
        dependency_key: str,
        candidate_collection: str,
    ) -> list[dict[str, Any]]:
        dependency = context.dependency_results.get(dependency_code)
        prepared = dependency.get(dependency_key) if dependency else None
        proposal_input = prepared.get("proposal_input") if isinstance(prepared, dict) else None
        if not isinstance(proposal_input, dict):
            raise ContractValidationError(f"{task_type} requires prepared candidate input")
        candidates = proposal_input.get(candidate_collection)
        requested_count = proposal_input.get("requested_candidate_count")
        if (
            not isinstance(candidates, list)
            or not candidates
            or not isinstance(requested_count, int)
            or requested_count < 1
        ):
            raise ContractValidationError(f"{task_type} has no eligible candidate input")

        tasks: list[dict[str, Any]] = []
        for candidate in candidates[:requested_count]:
            if not isinstance(candidate, dict) or not isinstance(candidate.get("proposal_id"), str):
                raise ContractValidationError(f"{task_type} candidate identity is invalid")
            single_input = deepcopy(proposal_input)
            single_input[candidate_collection] = [deepcopy(candidate)]
            single_input["requested_candidate_count"] = 1
            single_context = context.model_copy(deep=True)
            single_dependency = single_context.dependency_results[dependency_code][dependency_key]
            single_dependency["proposal_input"] = single_input
            task = self._build_proposal(
                single_context,
                task_type=task_type,
                dependency_code=dependency_code,
                dependency_key=dependency_key,
                candidate_collection=candidate_collection,
            )
            suffix = hashlib.sha256(candidate["proposal_id"].encode()).hexdigest()[:12]
            task["task_id"] = f"task-{context.lease.stage_run_id}-{suffix}"
            task["input_digest"] = compute_agent_input_digest(task)
            self._contracts.validate_agent_task(task)
            tasks.append(task)
        return tasks

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"Expected JSON object: {path.name}")
        return value

    @staticmethod
    def _state_projection(state: VentureState) -> dict[str, Any]:
        founder = state.founder
        area = state.area
        return {
            "state_version": state.state_version,
            "founder": {
                "target_area_input": founder.target_area_input,
                "own_funds_krw": founder.own_funds_krw,
                "borrowing_intent": founder.borrowing_intent.value,
                "cafe_type_preference": founder.cafe_type_preference.value,
                "operation_mode": founder.operation_mode.value,
                "preferences": founder.preferences,
                "avoidances": founder.avoidances,
                "max_loss_krw": founder.max_loss_krw,
            },
            "area": {
                "resolution_status": area.resolution_status.value,
                "administrative_code": area.administrative_code,
                "display_name": area.display_name,
                "boundary_version": area.boundary_version,
                "coverage_profile": area.coverage_profile.value,
                "evidence_ids": area.evidence_ids,
                "unavailable_fields": area.unavailable_fields,
            },
            "active_case_id": state.active_case_id,
            "venture_cases": [
                {
                    "case_id": venture_case.case_id,
                    "case_type": venture_case.case_type.value,
                    "maturity": venture_case.maturity.value,
                    "status": venture_case.status.value,
                }
                for venture_case in state.venture_cases
            ],
        }
