from typing import Any

from app.agents.boundary import validate_agent_boundary
from app.agents.protocols import AgentRuntime
from app.agents.task_factory import AgentTaskFactory
from app.domain.errors import ContractValidationError
from app.workflows.models import StageControl, StageDisposition
from app.workflows.stage_context import StageContext


class EvidenceAssessStageHandler:
    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        task_factory: AgentTaskFactory | None = None,
    ) -> None:
        self._runtime = runtime
        self._task_factory = task_factory or AgentTaskFactory()

    def execute(self, context: StageContext) -> dict[str, object]:
        retrieval = self._retrieval(context)
        task = self._task_factory.build_evidence_assess(context)
        result = self._runtime.invoke(task)
        boundary = validate_agent_boundary(
            task=task,
            result=result,
            current_head=context.lease.head,
        )
        if not boundary.accepted:
            codes = ",".join(error.code for error in boundary.errors)
            raise ContractValidationError(f"EVIDENCE_ASSESS boundary rejected: {codes}")
        status = result["status"]
        if status == "INVALID":
            raise ContractValidationError("EVIDENCE_ASSESS Agent rejected its input")

        reason_codes = list(result["reason_codes"])
        failed_actions = retrieval.get("failed_actions", [])
        if not isinstance(failed_actions, list):
            raise ContractValidationError("Evidence Retrieval failures are invalid")
        failed_claim_ids: set[str] = set()
        for value in failed_actions:
            if not isinstance(value, dict):
                continue
            claim_id = value.get("claim_id")
            if isinstance(claim_id, str):
                failed_claim_ids.add(claim_id)
        agent_missing_claim_ids = {
            value for value in result["missing_claim_ids"] if isinstance(value, str)
        }
        payload = result["payload"] if isinstance(result["payload"], dict) else {}
        payload_missing_claim_ids = {
            value for value in payload.get("missing_claims", []) if isinstance(value, str)
        }
        all_claim_ids: set[str] = set()
        for value in task["payload"]["claims"]:
            if isinstance(value, dict):
                claim_id = value.get("claim_id")
                if isinstance(claim_id, str):
                    all_claim_ids.add(claim_id)
        assessed_claim_ids: set[str] = set()
        for value in payload.get("assessments", []):
            if isinstance(value, dict):
                claim_id = value.get("claim_id")
                if isinstance(claim_id, str):
                    assessed_claim_ids.add(claim_id)
        missing_ids = (
            agent_missing_claim_ids | payload_missing_claim_ids | failed_claim_ids
        )
        if status == "ABSTAIN":
            missing_ids.update(all_claim_ids)
        uncovered_claim_ids = (
            all_claim_ids - assessed_claim_ids - missing_ids
            if status == "COMPLETE"
            else set()
        )
        if uncovered_claim_ids:
            missing_ids.update(uncovered_claim_ids)
            reason_codes.append("ASSESSMENT_COVERAGE_MISSING")
        missing_claim_ids = sorted(missing_ids)
        if failed_actions and "MCP_ACTION_FAILED" not in reason_codes:
            reason_codes.append("MCP_ACTION_FAILED")
        return {
            "stage_control": self._stage_control(
                status=status,
                reason_codes=reason_codes,
            ).model_dump(mode="json"),
            "evidence_assessment": {
                "status": status,
                "claims": task["payload"]["claims"],
                "assessments": payload.get("assessments", []),
                "missing_claims": payload.get("missing_claims", []),
                "conflict_proposals": payload.get("conflict_proposals", []),
                "evidence_refs": result["evidence_refs"],
                "missing_claim_ids": missing_claim_ids,
                "reason_codes": reason_codes,
                "warnings": result["warnings"],
                "failed_actions": failed_actions,
                "retrieval_completeness": retrieval.get("completeness"),
                "executed_actions": retrieval["executed_actions"],
                "agent_trace": {
                    "task_id": task["task_id"],
                    "invocation_id": task["invocation_id"],
                    "input_digest": task["input_digest"],
                    "prompt_version": task["prompt_version"],
                    "output_schema_id": task["output_schema_id"],
                },
            },
        }

    @staticmethod
    def _retrieval(context: StageContext) -> dict[str, Any]:
        dependency = context.dependency_results.get("EVIDENCE_RETRIEVAL")
        value = dependency.get("evidence_retrieval") if dependency else None
        if not isinstance(value, dict):
            raise ContractValidationError(
                "EVIDENCE_ASSESS requires Evidence Retrieval results"
            )
        return value

    @staticmethod
    def _stage_control(*, status: str, reason_codes: list[str]) -> StageControl:
        if status == "NEEDS_HUMAN":
            return StageControl(
                disposition=StageDisposition.WAITING_FOR_HUMAN,
                reason_codes=reason_codes,
            )
        if status in {"COMPLETE", "NEEDS_EVIDENCE", "ABSTAIN"}:
            return StageControl()
        raise ContractValidationError(f"Unsupported EVIDENCE_ASSESS status: {status}")
