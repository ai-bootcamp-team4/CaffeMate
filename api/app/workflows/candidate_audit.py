from typing import Any

from app.agents.boundary import validate_agent_boundary
from app.agents.protocols import AgentRuntime
from app.agents.task_factory import AgentTaskFactory
from app.domain.errors import ContractValidationError, ExternalExecutionUnavailableError
from app.workflows.models import StageControl
from app.workflows.stage_context import StageContext

CANDIDATE_AUDIT_RUNTIME_MAX_ATTEMPTS = 3


class CandidateAuditStageHandler:
    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        task_factory: AgentTaskFactory | None = None,
    ) -> None:
        self._runtime = runtime
        self._task_factory = task_factory or AgentTaskFactory()

    def execute(self, context: StageContext) -> dict[str, object]:
        calculated = self._calculated(context)
        candidates = calculated.get("candidates")
        if not isinstance(candidates, list):
            raise ContractValidationError("CANDIDATE_AUDIT candidates are invalid")
        if not candidates:
            return self._result(
                candidates=[],
                audit_status="UNAVAILABLE",
                agent_status="ABSTAIN",
                candidate_audits=[],
                global_findings=[],
                reason_codes=["NO_CANDIDATES_TO_AUDIT"],
                agent_trace=None,
            )

        task = self._task_factory.build_candidate_audit(context)
        try:
            result = self._runtime.invoke(task)
        except ExternalExecutionUnavailableError as error:
            if getattr(error, "runtime_code", None) == "RUNTIME_AGENT_OUTPUT_INVALID":
                return self._result(
                    candidates=task["payload"]["candidates"],
                    audit_status="UNAVAILABLE",
                    agent_status="ABSTAIN",
                    candidate_audits=[],
                    global_findings=[],
                    reason_codes=["CANDIDATE_AUDIT_AGENT_OUTPUT_INVALID"],
                    agent_trace=self._trace(task),
                )
            if context.lease.attempt < CANDIDATE_AUDIT_RUNTIME_MAX_ATTEMPTS:
                raise
            return self._result(
                candidates=task["payload"]["candidates"],
                audit_status="UNAVAILABLE",
                agent_status="ABSTAIN",
                candidate_audits=[],
                global_findings=[],
                reason_codes=["CANDIDATE_AUDIT_RUNTIME_UNAVAILABLE"],
                agent_trace=self._trace(task),
            )

        boundary = validate_agent_boundary(
            task=task,
            result=result,
            current_head=context.lease.head,
        )
        if not boundary.accepted:
            return self._unavailable(task, "CANDIDATE_AUDIT_AGENT_OUTPUT_INVALID")
        status = result["status"]
        if status == "INVALID":
            return self._unavailable(task, "CANDIDATE_AUDIT_AGENT_OUTPUT_INVALID")
        if status != "COMPLETE":
            return self._result(
                candidates=task["payload"]["candidates"],
                audit_status="UNAVAILABLE",
                agent_status=status,
                candidate_audits=[],
                global_findings=[],
                reason_codes=result["reason_codes"],
                agent_trace=self._trace(task),
            )

        payload = result["payload"]
        if not isinstance(payload, dict):
            return self._unavailable(task, "CANDIDATE_AUDIT_AGENT_OUTPUT_INVALID")
        audits = payload.get("candidate_audits")
        global_findings = payload.get("global_findings")
        if not isinstance(audits, list) or not isinstance(global_findings, list):
            return self._unavailable(task, "CANDIDATE_AUDIT_AGENT_OUTPUT_INVALID")
        if any(value.get("status") == "INVALID_INPUT" for value in audits):
            return self._unavailable(task, "CANDIDATE_AUDIT_AGENT_OUTPUT_INVALID")
        candidates_by_id = {
            value["candidate_id"]: value for value in task["payload"]["candidates"]
        }
        requires_human = any(
            self._requires_human(value, candidates_by_id) for value in audits
        )
        reason_codes = list(result["reason_codes"])
        if requires_human:
            reason_codes = sorted(set(reason_codes + ["CANDIDATE_AUDIT_REQUIRES_HUMAN"]))
        return self._result(
            candidates=task["payload"]["candidates"],
            audit_status="REQUIRES_HUMAN" if requires_human else "PASSED",
            agent_status=status,
            candidate_audits=audits,
            global_findings=global_findings,
            reason_codes=reason_codes,
            agent_trace=self._trace(task),
        )

    @staticmethod
    def _calculated(context: StageContext) -> dict[str, Any]:
        dependency = context.dependency_results.get("CALCULATE_GATE_RANK")
        value = dependency.get("calculate_gate_rank") if dependency else None
        if not isinstance(value, dict):
            raise ContractValidationError(
                "CANDIDATE_AUDIT requires CALCULATE_GATE_RANK output"
            )
        return value

    @staticmethod
    def _requires_human(
        audit: object,
        candidates_by_id: dict[str, dict[str, Any]],
    ) -> bool:
        if not isinstance(audit, dict):
            return True
        if audit.get("status") == "REQUIRES_HUMAN":
            return True
        candidate_id = audit.get("candidate_id")
        candidate = (
            candidates_by_id.get(candidate_id)
            if isinstance(candidate_id, str)
            else None
        )
        if (
            audit.get("status") == "REQUIRES_EVIDENCE"
            and candidate is not None
            and candidate.get("review_status") == "REVIEW_RECOMMENDED"
        ):
            return True
        for finding in audit.get("findings", []):
            if not isinstance(finding, dict):
                return True
            if finding.get("severity") in {"HIGH", "CRITICAL"}:
                return True
            if finding.get("disposition") == "REQUIRE_HUMAN":
                return True
        return False

    @staticmethod
    def _trace(task: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": task["task_id"],
            "invocation_id": task["invocation_id"],
            "input_digest": task["input_digest"],
            "prompt_version": task["prompt_version"],
            "output_schema_id": task["output_schema_id"],
        }

    @classmethod
    def _unavailable(cls, task: dict[str, Any], reason_code: str) -> dict[str, object]:
        return cls._result(
            candidates=task["payload"]["candidates"],
            audit_status="UNAVAILABLE",
            agent_status="ABSTAIN",
            candidate_audits=[],
            global_findings=[],
            reason_codes=[reason_code],
            agent_trace=cls._trace(task),
        )

    @staticmethod
    def _result(
        *,
        candidates: list[dict[str, Any]],
        audit_status: str,
        agent_status: str,
        candidate_audits: list[dict[str, Any]],
        global_findings: list[str],
        reason_codes: list[str],
        agent_trace: dict[str, Any] | None,
    ) -> dict[str, object]:
        return {
            "stage_control": StageControl(reason_codes=reason_codes).model_dump(
                mode="json"
            ),
            "candidate_audit": {
                "status": audit_status,
                "agent_status": agent_status,
                "candidates": candidates,
                "candidate_audits": candidate_audits,
                "global_findings": global_findings,
                "reason_codes": reason_codes,
                "agent_trace": agent_trace,
            },
        }
