import json
from collections.abc import Callable
from typing import Any, Protocol
from uuid import uuid4

from app.agents.task_factory import AgentTaskFactory
from app.contracts.schema_registry import AgentContractValidator, ContractRegistry
from app.domain.errors import (
    ContractValidationError,
    ExternalExecutionUnavailableError,
    ResultExplanationPreconditionError,
)
from app.results.explanation_models import ExplanationEvidence, ResultExplanation
from app.results.models import ResultFreshness, ResultView


class ResultReader(Protocol):
    def get_current(self, *, project_id: str, user_id: str) -> ResultView: ...


class ExplanationRuntime(Protocol):
    def invoke(self, task: dict[str, Any]) -> dict[str, Any]: ...


class ResultExplanationService:
    def __init__(
        self,
        results: ResultReader,
        runtime: ExplanationRuntime,
        *,
        task_factory: AgentTaskFactory | None = None,
        contracts: AgentContractValidator | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._results = results
        self._runtime = runtime
        self._task_factory = task_factory or AgentTaskFactory()
        self._contracts = contracts or ContractRegistry()
        self._new_id = new_id or (lambda: str(uuid4()))

    def explain(
        self,
        *,
        project_id: str,
        user_id: str,
        result_bundle_id: str,
        candidate_id: str | None,
        question: str,
    ) -> ResultExplanation:
        """사용자 의도: 결과를 이해하도록 돕되 프로젝트 State는 절대 수정하지 않는다."""
        result = self._results.get_current(project_id=project_id, user_id=user_id)
        if result.freshness != ResultFreshness.CURRENT:
            raise ResultExplanationPreconditionError("Explanation requires a current result")
        if result.result_bundle_id != result_bundle_id:
            raise ResultExplanationPreconditionError("Explanation result is no longer current")

        selected_id = candidate_id or result.primary_candidate_id
        selected = next(
            (
                candidate
                for candidate in result.candidates
                if candidate.get("candidate_id") == selected_id
            ),
            None,
        )
        if selected is None or not isinstance(selected_id, str):
            raise ResultExplanationPreconditionError("Explanation candidate is not in the result")

        evidence_catalog: dict[str, dict[str, Any]] = {}
        ordered_candidates = [selected] + [
            candidate
            for candidate in result.candidates
            if candidate.get("candidate_id") != selected_id
        ]
        for candidate in ordered_candidates:
            for evidence_id, evidence in self._evidence_catalog(candidate).items():
                evidence_catalog.setdefault(evidence_id, evidence)
        evidence_catalog = dict(list(evidence_catalog.items())[:24])
        explanation_id = self._new_id()
        task = self._task_factory.build_result_explain(
            project_id=project_id,
            workflow_run_id=result.workflow_run_id,
            explanation_id=explanation_id,
            head=result.head,
            payload={
                "question": question.strip(),
                "result_bundle_id": result.result_bundle_id,
                "selected_candidate": self._selected_projection(selected),
                "comparison_candidates": [
                    self._comparison_projection(candidate) for candidate in result.candidates
                ],
                "evidence_catalog": [
                    {key: value for key, value in item.items() if key != "source_ref"}
                    for item in evidence_catalog.values()
                ],
            },
        )
        agent_result = self._runtime.invoke(task)
        self._contracts.validate_agent_task_result(agent_result)
        payload = agent_result.get("payload")
        if agent_result.get("status") != "COMPLETE" or not isinstance(payload, dict):
            raise ResultExplanationPreconditionError("Explanation agent did not return an answer")

        payload_refs = payload.get("evidence_refs")
        envelope_refs = agent_result.get("evidence_refs")
        if not isinstance(payload_refs, list) or payload_refs != envelope_refs:
            raise ContractValidationError("Explanation evidence references do not match")
        unknown_refs = [ref for ref in payload_refs if ref not in evidence_catalog]
        if unknown_refs:
            raise ContractValidationError("Explanation cited evidence outside the current result")

        return ResultExplanation(
            explanation_id=explanation_id,
            result_bundle_id=result.result_bundle_id,
            candidate_id=selected_id,
            intent=str(payload["intent"]),
            conclusion=str(payload["conclusion"]),
            reasons=[str(value) for value in payload["reasons"]],
            evidence=[ExplanationEvidence(**evidence_catalog[ref]) for ref in payload_refs],
            unknowns=[str(value) for value in payload["unknowns"]],
            decision_change_conditions=[
                str(value) for value in payload["decision_change_conditions"]
            ],
            suggested_action=str(payload["suggested_action"]),
            state_changed=False,
        )

    @staticmethod
    def _selected_projection(candidate: dict[str, Any]) -> dict[str, Any]:
        return {
            "candidate_id": candidate["candidate_id"],
            "display_name": candidate.get("display_name") or "이름 없는 후보",
            "case_type": candidate.get("case_type") or "INDEPENDENT",
            "review_status": candidate.get("review_status") or "UNKNOWN",
            "summary": candidate.get("summary") or "현재 결과 요약이 없습니다.",
            "reason_codes": candidate.get("reason_codes") or [],
            "financial_summary": candidate.get("financial_summary") or {},
            "missing_fields": candidate.get("missing_fields") or [],
            "risks": candidate.get("risks") or [],
            "counterfactuals": candidate.get("counterfactuals") or [],
            "next_actions": candidate.get("next_actions") or [],
        }

    @staticmethod
    def _comparison_projection(candidate: dict[str, Any]) -> dict[str, Any]:
        return {
            "candidate_id": candidate["candidate_id"],
            "display_name": candidate.get("display_name") or "이름 없는 후보",
            "case_type": candidate.get("case_type") or "INDEPENDENT",
            "review_status": candidate.get("review_status") or "UNKNOWN",
            "summary": candidate.get("summary") or "현재 결과 요약이 없습니다.",
            "rank": candidate.get("rank"),
        }

    @staticmethod
    def _evidence_catalog(candidate: dict[str, Any]) -> dict[str, dict[str, Any]]:
        catalog: dict[str, dict[str, Any]] = {}
        for signal in candidate.get("market_signals") or []:
            evidence_id = signal.get("evidence_id")
            if not isinstance(evidence_id, str) or not evidence_id:
                continue
            catalog[evidence_id] = {
                "evidence_id": evidence_id,
                "label": str(signal.get("signal_type") or "상권 근거"),
                "value": ResultExplanationService._display_value(
                    signal.get("value"), signal.get("unit")
                ),
                "source_title": signal.get("source_title"),
                "source_ref": signal.get("source_ref"),
                "data_date": signal.get("data_date"),
                "caveat": signal.get("caveat"),
            }
        for document in candidate.get("official_documents") or []:
            for evidence_id in document.get("evidence_refs") or []:
                if not isinstance(evidence_id, str) or not evidence_id:
                    continue
                catalog[evidence_id] = {
                    "evidence_id": evidence_id,
                    "label": str(document.get("title") or "공식 문서"),
                    "value": str(document.get("excerpt") or "") or None,
                    "source_title": str(document.get("title") or "공식 문서"),
                    "source_ref": document.get("source_ref"),
                    "data_date": document.get("data_date"),
                    "caveat": None,
                }
        return dict(list(catalog.items())[:24])

    @staticmethod
    def _display_value(value: Any, unit: Any) -> str | None:
        if value is None:
            return None
        rendered = (
            json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        )
        return f"{rendered} {unit}".strip() if unit else rendered


class UnavailableResultExplanationService:
    def explain(self, **_: object) -> ResultExplanation:
        raise ExternalExecutionUnavailableError("Result explanation is unavailable")
