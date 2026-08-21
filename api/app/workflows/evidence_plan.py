from typing import Any, Protocol

from app.agents.boundary import validate_agent_boundary
from app.agents.task_factory import AgentTaskFactory
from app.domain.errors import ContractValidationError
from app.workflows.models import StageControl, StageDisposition
from app.workflows.stage_context import StageContext


class AgentRuntime(Protocol):
    def invoke(self, task: dict[str, Any]) -> dict[str, Any]: ...


class EvidencePlanStageHandler:
    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        task_factory: AgentTaskFactory | None = None,
    ) -> None:
        self._runtime = runtime
        self._task_factory = task_factory or AgentTaskFactory()

    def execute(self, context: StageContext) -> dict[str, object]:
        task = self._task_factory.build_evidence_plan(context)
        result = self._runtime.invoke(task)
        boundary = validate_agent_boundary(
            task=task,
            result=result,
            current_head=context.lease.head,
        )
        if not boundary.accepted:
            codes = ",".join(error.code for error in boundary.errors)
            raise ContractValidationError(f"EVIDENCE_PLAN boundary rejected: {codes}")

        status = result["status"]
        if status == "INVALID":
            raise ContractValidationError("EVIDENCE_PLAN Agent rejected its input")
        control = self._stage_control(status=status, reason_codes=result["reason_codes"])
        return {
            "stage_control": control.model_dump(mode="json"),
            "evidence_plan": {
                "status": status,
                "claims": task["payload"]["claims"],
                "planning_constraints": task["payload"]["planning_constraints"],
                "claim_plans": (result["payload"] or {}).get("claim_plans", []),
                "missing_claim_ids": result["missing_claim_ids"],
                "reason_codes": result["reason_codes"],
                "warnings": result["warnings"],
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
    def _stage_control(*, status: str, reason_codes: list[str]) -> StageControl:
        if status == "COMPLETE":
            return StageControl()
        if status == "NEEDS_HUMAN":
            return StageControl(
                disposition=StageDisposition.WAITING_FOR_HUMAN,
                reason_codes=reason_codes,
            )
        if status in {"NEEDS_EVIDENCE", "ABSTAIN"}:
            return StageControl(
                disposition=StageDisposition.ABSTAIN,
                reason_codes=reason_codes,
            )
        raise ContractValidationError(f"Unsupported EVIDENCE_PLAN status: {status}")
