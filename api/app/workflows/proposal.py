from collections.abc import Callable
from typing import Any

from app.agents.boundary import validate_agent_boundary
from app.agents.task_factory import AgentTaskFactory
from app.domain.errors import ContractValidationError, ExternalExecutionUnavailableError
from app.domain.models import CafeTypePreference
from app.workflows.evidence_plan import AgentRuntime
from app.workflows.models import StageControl, StageDisposition
from app.workflows.stage_context import StageContext

PROPOSAL_RUNTIME_MAX_ATTEMPTS = 3


class ProposalStageHandler:
    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        task_type: str,
        dependency_code: str,
        dependency_key: str,
        candidate_collection: str,
        output_key: str,
        build_task: Callable[[AgentTaskFactory, StageContext], dict[str, Any]],
        task_factory: AgentTaskFactory | None = None,
    ) -> None:
        self._runtime = runtime
        self._task_type = task_type
        self._dependency_code = dependency_code
        self._dependency_key = dependency_key
        self._candidate_collection = candidate_collection
        self._output_key = output_key
        self._build_task = build_task
        self._task_factory = task_factory or AgentTaskFactory()

    @classmethod
    def independent(
        cls,
        runtime: AgentRuntime,
        *,
        task_factory: AgentTaskFactory | None = None,
    ) -> "ProposalStageHandler":
        return cls(
            runtime,
            task_type="PROPOSE_INDEPENDENT",
            dependency_code="INDEPENDENT_SEED",
            dependency_key="independent_seed",
            candidate_collection="model_seeds",
            output_key="independent_proposal",
            build_task=lambda factory, context: factory.build_independent_proposal(context),
            task_factory=task_factory,
        )

    @classmethod
    def franchise(
        cls,
        runtime: AgentRuntime,
        *,
        task_factory: AgentTaskFactory | None = None,
    ) -> "ProposalStageHandler":
        return cls(
            runtime,
            task_type="PROPOSE_FRANCHISE",
            dependency_code="FRANCHISE_ELIGIBILITY",
            dependency_key="franchise_eligibility",
            candidate_collection="franchise_universe",
            output_key="franchise_proposal",
            build_task=lambda factory, context: factory.build_franchise_proposal(context),
            task_factory=task_factory,
        )

    def execute(self, context: StageContext) -> dict[str, object]:
        prepared = self._prepared_input(context)
        proposal_input = prepared["proposal_input"]
        candidates = proposal_input.get(self._candidate_collection)
        if not isinstance(candidates, list):
            raise ContractValidationError(f"{self._task_type} candidate input is invalid")
        if not candidates:
            reason_codes = self._reason_codes(prepared) or ["NO_ELIGIBLE_PROPOSAL_INPUT"]
            return self._result(
                control=StageControl(reason_codes=reason_codes),
                status="ABSTAIN",
                candidate_proposals=[],
                evidence_refs=[],
                missing_claim_ids=[],
                reason_codes=reason_codes,
                warnings=[],
                agent_trace=None,
                proposal_input=proposal_input,
            )

        task = self._build_task(self._task_factory, context)
        try:
            result = self._runtime.invoke(task)
        except ExternalExecutionUnavailableError:
            if context.lease.attempt < PROPOSAL_RUNTIME_MAX_ATTEMPTS:
                raise
            reason_codes = ["PROPOSAL_RUNTIME_UNAVAILABLE"]
            return self._result(
                control=StageControl(reason_codes=reason_codes),
                status="ABSTAIN",
                candidate_proposals=[],
                evidence_refs=[],
                missing_claim_ids=[],
                reason_codes=reason_codes,
                warnings=[],
                agent_trace={
                    "task_id": task["task_id"],
                    "invocation_id": task["invocation_id"],
                    "input_digest": task["input_digest"],
                    "prompt_version": task["prompt_version"],
                    "output_schema_id": task["output_schema_id"],
                },
                proposal_input=proposal_input,
            )
        boundary = validate_agent_boundary(
            task=task,
            result=result,
            current_head=context.lease.head,
        )
        if not boundary.accepted:
            codes = ",".join(error.code for error in boundary.errors)
            raise ContractValidationError(f"{self._task_type} boundary rejected: {codes}")
        status = result["status"]
        if status == "INVALID":
            raise ContractValidationError(f"{self._task_type} Agent rejected its input")
        payload = result["payload"] if isinstance(result["payload"], dict) else {}
        proposals = payload.get("candidate_proposals", [])
        if not isinstance(proposals, list):
            raise ContractValidationError(f"{self._task_type} proposals are invalid")
        if status in {"ABSTAIN", "NEEDS_HUMAN"}:
            proposals = []
        control = self._stage_control(context, status, result["reason_codes"])
        return self._result(
            control=control,
            status=status,
            candidate_proposals=proposals,
            evidence_refs=result["evidence_refs"],
            missing_claim_ids=result["missing_claim_ids"],
            reason_codes=result["reason_codes"],
            warnings=result["warnings"],
            agent_trace={
                "task_id": task["task_id"],
                "invocation_id": task["invocation_id"],
                "input_digest": task["input_digest"],
                "prompt_version": task["prompt_version"],
                "output_schema_id": task["output_schema_id"],
            },
            proposal_input=proposal_input,
        )

    def _prepared_input(self, context: StageContext) -> dict[str, Any]:
        dependency = context.dependency_results.get(self._dependency_code)
        value = dependency.get(self._dependency_key) if dependency else None
        if not isinstance(value, dict) or not isinstance(value.get("proposal_input"), dict):
            raise ContractValidationError(f"{self._task_type} requires prepared input")
        return value

    @staticmethod
    def _reason_codes(prepared: dict[str, Any]) -> list[str]:
        values = prepared.get("reason_codes", [])
        return [value for value in values if isinstance(value, str)]

    def _stage_control(
        self,
        context: StageContext,
        status: str,
        reason_codes: list[str],
    ) -> StageControl:
        if status not in {"COMPLETE", "NEEDS_EVIDENCE", "NEEDS_HUMAN", "ABSTAIN"}:
            raise ContractValidationError(f"Unsupported {self._task_type} status: {status}")
        if status == "NEEDS_HUMAN" and self._is_only_requested_branch(context):
            return StageControl(
                disposition=StageDisposition.WAITING_FOR_HUMAN,
                reason_codes=reason_codes,
            )
        return StageControl(reason_codes=reason_codes)

    def _is_only_requested_branch(self, context: StageContext) -> bool:
        preference = context.state.founder.cafe_type_preference
        return (
            self._task_type == "PROPOSE_INDEPENDENT"
            and preference == CafeTypePreference.INDEPENDENT_ONLY
        ) or (
            self._task_type == "PROPOSE_FRANCHISE"
            and preference == CafeTypePreference.FRANCHISE_ONLY
        )

    def _result(
        self,
        *,
        control: StageControl,
        status: str,
        candidate_proposals: list[dict[str, Any]],
        evidence_refs: list[str],
        missing_claim_ids: list[str],
        reason_codes: list[str],
        warnings: list[str],
        agent_trace: dict[str, Any] | None,
        proposal_input: dict[str, Any],
    ) -> dict[str, object]:
        return {
            "stage_control": control.model_dump(mode="json"),
            self._output_key: {
                "status": status,
                "candidate_proposals": candidate_proposals,
                "evidence_refs": evidence_refs,
                "missing_claim_ids": missing_claim_ids,
                "reason_codes": reason_codes,
                "warnings": warnings,
                "agent_trace": agent_trace,
                "proposal_input": proposal_input,
            },
        }
