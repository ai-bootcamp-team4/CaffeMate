from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from app.agents.boundary import validate_agent_boundary
from app.agents.protocols import AgentRuntime
from app.agents.task_factory import AgentTaskFactory
from app.domain.errors import ContractValidationError, ExternalExecutionUnavailableError
from app.domain.models import CafeTypePreference
from app.workflows.models import StageControl, StageDisposition
from app.workflows.stage_context import StageContext


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
        build_tasks: Callable[[AgentTaskFactory, StageContext], list[dict[str, Any]]],
        task_factory: AgentTaskFactory | None = None,
    ) -> None:
        self._runtime = runtime
        self._task_type = task_type
        self._dependency_code = dependency_code
        self._dependency_key = dependency_key
        self._candidate_collection = candidate_collection
        self._output_key = output_key
        self._build_tasks = build_tasks
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
            build_tasks=lambda factory, context: factory.build_independent_proposal_tasks(
                context
            ),
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
            build_tasks=lambda factory, context: factory.build_franchise_proposal_tasks(
                context
            ),
            task_factory=task_factory,
        )

    def execute(self, context: StageContext) -> dict[str, object]:
        prepared = self._prepared_input(context)
        proposal_input = prepared["proposal_input"]
        candidates = proposal_input.get(self._candidate_collection)
        if not isinstance(candidates, list):
            raise ContractValidationError(f"{self._task_type} candidate input is invalid")
        if not candidates:
            no_candidate_reasons = self._reason_codes(prepared) or [
                "NO_ELIGIBLE_PROPOSAL_INPUT"
            ]
            return self._result(
                control=StageControl(reason_codes=no_candidate_reasons),
                status="ABSTAIN",
                candidate_proposals=[],
                evidence_refs=[],
                missing_claim_ids=[],
                reason_codes=no_candidate_reasons,
                warnings=[],
                agent_traces=[],
                proposal_input=proposal_input,
            )

        tasks = self._build_tasks(self._task_factory, context)
        results, failures = self._invoke_parallel(tasks)
        if not results:
            unavailable_reasons = ["PROPOSAL_RUNTIME_UNAVAILABLE_SEED_FALLBACK"]
            return self._result(
                control=StageControl(reason_codes=unavailable_reasons),
                status="NEEDS_EVIDENCE",
                candidate_proposals=self._fallback_proposals(candidates),
                evidence_refs=sorted(
                    {
                        evidence_ref
                        for candidate in candidates
                        for evidence_ref in candidate.get("evidence_refs", [])
                        if isinstance(evidence_ref, str)
                    }
                ),
                missing_claim_ids=[],
                reason_codes=unavailable_reasons,
                warnings=["Agent Runtime 응답을 받지 못해 검증된 기본 모델로 계속합니다."],
                agent_traces=[self._trace(task) for task in tasks],
                proposal_input=proposal_input,
            )

        proposals: list[dict[str, Any]] = []
        statuses: list[str] = []
        evidence_refs: set[str] = set()
        missing_claim_ids: set[str] = set()
        reason_codes: set[str] = set()
        warnings: set[str] = set()
        traces: list[dict[str, Any]] = []
        invalid_output_count = 0
        for task, result in results:
            boundary = validate_agent_boundary(
                task=task,
                result=result,
                current_head=context.lease.head,
            )
            if not boundary.accepted:
                invalid_output_count += 1
                traces.append(self._trace(task))
                continue
            status = result["status"]
            if status == "INVALID":
                invalid_output_count += 1
                traces.append(self._trace(task))
                continue
            payload = result["payload"] if isinstance(result["payload"], dict) else {}
            candidate_proposals = payload.get("candidate_proposals", [])
            if not isinstance(candidate_proposals, list):
                invalid_output_count += 1
                traces.append(self._trace(task))
                continue
            if status not in {"ABSTAIN", "NEEDS_HUMAN"}:
                proposals.extend(candidate_proposals)
            statuses.append(status)
            evidence_refs.update(result["evidence_refs"])
            missing_claim_ids.update(result["missing_claim_ids"])
            reason_codes.update(result["reason_codes"])
            warnings.update(result["warnings"])
            traces.append(self._trace(task))

        if failures:
            reason_codes.add("PROPOSAL_PARTIAL_RUNTIME_FAILURE")
        if invalid_output_count:
            reason_codes.add("PROPOSAL_PARTIAL_AGENT_OUTPUT_INVALID")
        status = self._aggregate_status(
            statuses,
            has_failures=bool(failures) or invalid_output_count > 0,
        )
        control = self._stage_control(context, status, sorted(reason_codes))
        return self._result(
            control=control,
            status=status,
            candidate_proposals=proposals,
            evidence_refs=sorted(evidence_refs),
            missing_claim_ids=sorted(missing_claim_ids),
            reason_codes=sorted(reason_codes),
            warnings=sorted(warnings),
            agent_traces=traces,
            proposal_input=proposal_input,
        )

    def _invoke_parallel(
        self, tasks: list[dict[str, Any]]
    ) -> tuple[
        list[tuple[dict[str, Any], dict[str, Any]]],
        list[ExternalExecutionUnavailableError],
    ]:
        if not tasks:
            return [], []
        with ThreadPoolExecutor(
            max_workers=len(tasks), thread_name_prefix="caffemate-proposal"
        ) as executor:
            futures: list[Future[dict[str, Any]]] = [
                executor.submit(self._runtime.invoke, task) for task in tasks
            ]
            results: list[tuple[dict[str, Any], dict[str, Any]]] = []
            failures: list[ExternalExecutionUnavailableError] = []
            for task, future in zip(tasks, futures, strict=True):
                try:
                    results.append((task, future.result()))
                except ExternalExecutionUnavailableError as error:
                    failures.append(error)
        return results, failures

    def _fallback_proposals(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        proposals: list[dict[str, Any]] = []
        for candidate in candidates:
            proposal_id = candidate.get("proposal_id")
            display_name = candidate.get("display_name")
            if not isinstance(proposal_id, str) or not isinstance(display_name, str):
                continue
            if self._task_type == "PROPOSE_INDEPENDENT":
                source_id = candidate.get("model_id")
                evidence_refs: list[str] = []
                assumption_refs = [
                    value
                    for value in candidate.get("support_refs", [])
                    if isinstance(value, str)
                ]
                missing_fields: list[str] = []
                warning = "실제 점포 조건을 입력하면 비용과 적합성을 다시 계산합니다."
                case_type = "INDEPENDENT"
            else:
                source_id = candidate.get("brand_id")
                evidence_refs = [
                    value
                    for value in candidate.get("evidence_refs", [])
                    if isinstance(value, str)
                ]
                assumption_refs = []
                missing_fields = [
                    value
                    for value in candidate.get("missing_fields", [])
                    if isinstance(value, str)
                ]
                warning = "본사 출점 가능 여부와 최신 가맹 조건을 확인해야 합니다."
                case_type = "FRANCHISE"
            if not isinstance(source_id, str):
                continue
            proposals.append(
                {
                    "proposal_id": proposal_id,
                    "case_type": case_type,
                    "display_name": display_name,
                    "seed_or_brand_id": source_id,
                    "adjusted_parameters": [],
                    "claim_refs": [],
                    "evidence_refs": evidence_refs,
                    "assumption_refs": assumption_refs,
                    "missing_fields": missing_fields,
                    "warnings": [warning],
                }
            )
        return proposals

    @staticmethod
    def _aggregate_status(statuses: list[str], *, has_failures: bool) -> str:
        if has_failures or "NEEDS_EVIDENCE" in statuses:
            return "NEEDS_EVIDENCE"
        if "COMPLETE" in statuses:
            return "COMPLETE"
        if "NEEDS_HUMAN" in statuses:
            return "NEEDS_HUMAN"
        return "ABSTAIN"

    @staticmethod
    def _trace(task: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": task["task_id"],
            "invocation_id": task["invocation_id"],
            "input_digest": task["input_digest"],
            "prompt_version": task["prompt_version"],
            "output_schema_id": task["output_schema_id"],
        }

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
        agent_traces: list[dict[str, Any]],
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
                "agent_trace": agent_traces[0] if agent_traces else None,
                "agent_traces": agent_traces,
                "proposal_input": proposal_input,
            },
        }
