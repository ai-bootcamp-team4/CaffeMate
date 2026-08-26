from datetime import UTC, datetime
from typing import Any

import pytest

from app.domain.errors import (
    ContractValidationError,
    ExternalExecutionUnavailableError,
    ResultExplanationPreconditionError,
)
from app.results.explanation import ResultExplanationService
from app.results.models import AuditStatus, ResultFreshness, ResultView
from app.workflows.models import HeadFence

HEAD = HeadFence(
    workflow_generation=1,
    state_version=3,
    founder_snapshot_id="founder-3",
    area_snapshot_id="area-3",
    evidence_snapshot_id="evidence-3",
    policy_snapshot_id="policy-v1",
    index_generation_id="index-1",
    seed_registry_id="seed-1",
)


def current_result() -> ResultView:
    return ResultView(
        result_bundle_id="result-1",
        project_id="project-1",
        workflow_run_id="workflow-1",
        head=HEAD,
        candidates=[
            {
                "candidate_id": "candidate-1",
                "display_name": "소형 포장 중심 개인카페",
                "case_type": "INDEPENDENT",
                "review_status": "CONDITIONAL_REVIEW",
                "summary": "현재 자금에 가장 가까운 검토 후보입니다.",
                "reason_codes": ["CAPITAL_FIT_CLOSEST"],
                "rank": 1,
                "is_primary_next_review": True,
                "market_signals": [
                    {
                        "signal_type": "CAFE_COUNT",
                        "value": 139,
                        "unit": "STORES",
                        "data_date": "2026-03-31",
                        "source_title": "서울시 상권분석서비스",
                        "source_ref": "https://data.seoul.go.kr/cafes",
                        "evidence_id": "evidence-cafe-count",
                        "caveat": "행정동 집계이며 개별 점포 경쟁력을 뜻하지 않습니다.",
                    }
                ],
                "official_documents": [],
                "financial_summary": {
                    "initial_cash": {"low": 45_000_000, "base": 58_000_000, "high": 75_000_000},
                    "monthly_fixed_cost": {"low": 3_000_000, "base": 4_200_000, "high": 5_500_000},
                    "unknown_cost_fields": ["권리금"],
                },
                "missing_fields": [
                    {
                        "field": "권리금",
                        "impact": "초기 필요자금이 달라집니다.",
                        "next_check": "실제 점포 조건을 확인합니다.",
                    }
                ],
                "risks": [],
                "counterfactuals": [
                    {
                        "variable": "월세",
                        "condition": "월세가 15% 낮아짐",
                        "decision_impact": "자금 적합도가 개선됩니다.",
                    }
                ],
                "next_actions": ["실제 점포 조건 확인"],
            }
        ],
        primary_candidate_id="candidate-1",
        audit_status=AuditStatus.PASSED,
        created_at=datetime(2026, 8, 24, tzinfo=UTC),
        freshness=ResultFreshness.CURRENT,
        stale_head_dimensions=[],
        current_head=HEAD,
        invalidation_reason_codes=[],
    )


class FakeResults:
    def get_current(self, **_: object) -> ResultView:
        return current_result()


class FakeRuntime:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.tasks: list[dict[str, Any]] = []

    def invoke(self, task: dict[str, Any]) -> dict[str, Any]:
        self.tasks.append(task)
        return {
            "schema_version": "1.0.0",
            "task_id": task["task_id"],
            "invocation_id": task["invocation_id"],
            "agent_name": task["agent_name"],
            "task_type": task["task_type"],
            "workflow_run_id": task["workflow_run_id"],
            "stage_run_id": task["stage_run_id"],
            "venture_project_id": task["venture_project_id"],
            "head_fence_seen": task["head_fence"],
            "input_digest": task["input_digest"],
            "output_schema_id": task["output_schema_id"],
            "status": "COMPLETE",
            "payload": self.payload,
            "evidence_refs": self.payload["evidence_refs"],
            "missing_claim_ids": [],
            "reason_codes": [],
            "warnings": [],
        }


class AbstainingRuntime(FakeRuntime):
    def invoke(self, task: dict[str, Any]) -> dict[str, Any]:
        result = super().invoke(task)
        result.update(
            {
                "status": "ABSTAIN",
                "payload": None,
                "evidence_refs": [],
                "reason_codes": ["MODEL_RESPONSE_INCOMPLETE"],
            }
        )
        return result


def test_explains_only_from_the_current_result_and_enriches_allowed_sources() -> None:
    runtime = FakeRuntime(
        {
            "intent": "WHY_RECOMMENDED",
            "conclusion": "현재 자금에 가장 가까운 후보이지만 실제 점포 확인이 필요합니다.",
            "reasons": ["세 후보 중 초기 필요자금 하한이 가장 낮습니다."],
            "evidence_refs": ["evidence-cafe-count"],
            "unknowns": ["권리금"],
            "decision_change_conditions": ["월세가 15% 낮아지면 자금 적합도가 개선됩니다."],
            "suggested_action": "NONE",
        }
    )
    service = ResultExplanationService(FakeResults(), runtime, new_id=lambda: "explanation-1")

    answer = service.explain(
        project_id="project-1",
        user_id="user-1",
        result_bundle_id="result-1",
        candidate_id="candidate-1",
        question="왜 이 후보가 1순위예요?",
    )

    assert answer.explanation_id == "explanation-1"
    assert answer.state_changed is False
    assert answer.evidence[0].source_title == "서울시 상권분석서비스"
    assert answer.evidence[0].source_ref == "https://data.seoul.go.kr/cafes"
    assert runtime.tasks[0]["task_type"] == "RESULT_EXPLAIN"
    assert runtime.tasks[0]["runtime_tool_policy"] == "NO_DIRECT_TOOL_CALLS"
    assert runtime.tasks[0]["payload"]["question"] == "왜 이 후보가 1순위예요?"


def test_explains_the_same_result_after_candidate_selection_makes_state_stale() -> None:
    result = current_result().model_copy(
        update={
            "freshness": ResultFreshness.STALE,
            "stale_head_dimensions": [
                "state_version",
                "founder_snapshot_id",
                "area_snapshot_id",
            ],
            "current_head": HEAD.model_copy(
                update={
                    "state_version": 4,
                    "founder_snapshot_id": "founder-4",
                    "area_snapshot_id": "area-4",
                }
            ),
        }
    )

    class SelectedResultReader:
        def get_current(self, **_: object) -> ResultView:
            return result

    runtime = FakeRuntime(
        {
            "intent": "WHY_RECOMMENDED",
            "conclusion": "선택한 결과의 근거를 설명합니다.",
            "reasons": ["표시된 후보와 근거를 기준으로 설명했습니다."],
            "evidence_refs": ["evidence-cafe-count"],
            "unknowns": ["권리금"],
            "decision_change_conditions": [],
            "suggested_action": "NONE",
        }
    )
    service = ResultExplanationService(SelectedResultReader(), runtime)

    answer = service.explain(
        project_id="project-1",
        user_id="user-1",
        result_bundle_id="result-1",
        candidate_id="candidate-1",
        question="선택한 뒤에도 왜 이 안을 먼저 보나요?",
    )

    assert answer.result_bundle_id == "result-1"
    assert runtime.tasks[0]["payload"]["question"] == "선택한 뒤에도 왜 이 안을 먼저 보나요?"


def test_treats_an_incomplete_explanation_agent_result_as_runtime_unavailable() -> None:
    runtime = AbstainingRuntime(
        {
            "intent": "WHY_RECOMMENDED",
            "conclusion": "사용되지 않는 응답입니다.",
            "reasons": [],
            "evidence_refs": [],
            "unknowns": [],
            "decision_change_conditions": [],
            "suggested_action": "NONE",
        }
    )
    service = ResultExplanationService(FakeResults(), runtime)

    with pytest.raises(
        ExternalExecutionUnavailableError,
        match="did not return an answer",
    ):
        service.explain(
            project_id="project-1",
            user_id="user-1",
            result_bundle_id="result-1",
            candidate_id="candidate-1",
            question="왜 이 후보가 1순위예요?",
        )


def test_rejects_a_bundle_that_is_not_the_result_currently_shown_by_the_server() -> None:
    runtime = FakeRuntime(
        {
            "intent": "WHY_RECOMMENDED",
            "conclusion": "호출되면 안 되는 응답입니다.",
            "reasons": [],
            "evidence_refs": [],
            "unknowns": [],
            "decision_change_conditions": [],
            "suggested_action": "NONE",
        }
    )
    service = ResultExplanationService(FakeResults(), runtime)

    with pytest.raises(
        ResultExplanationPreconditionError,
        match="no longer current",
    ):
        service.explain(
            project_id="project-1",
            user_id="user-1",
            result_bundle_id="older-result",
            candidate_id="candidate-1",
            question="왜 이 후보가 1순위예요?",
        )

    assert runtime.tasks == []


def test_rejects_an_evidence_reference_that_is_not_in_the_result() -> None:
    runtime = FakeRuntime(
        {
            "intent": "SOURCE",
            "conclusion": "출처를 확인할 수 없습니다.",
            "reasons": [],
            "evidence_refs": ["invented-evidence"],
            "unknowns": [],
            "decision_change_conditions": [],
            "suggested_action": "NONE",
        }
    )
    service = ResultExplanationService(FakeResults(), runtime)

    with pytest.raises(ContractValidationError, match="outside the current result"):
        service.explain(
            project_id="project-1",
            user_id="user-1",
            result_bundle_id="result-1",
            candidate_id="candidate-1",
            question="이 숫자의 출처가 뭐예요?",
        )


def test_allows_comparison_evidence_from_another_candidate_in_the_same_result() -> None:
    result = current_result()
    result.candidates.append(
        {
            "candidate_id": "candidate-2",
            "display_name": "좌석 중심 개인카페",
            "case_type": "INDEPENDENT",
            "review_status": "CONDITIONAL_REVIEW",
            "summary": "좌석 운영비 확인이 필요한 비교 후보입니다.",
            "rank": 2,
            "market_signals": [
                {
                    "signal_type": "FOOT_TRAFFIC",
                    "value": 25000,
                    "unit": "VISITS",
                    "data_date": "2026-03-31",
                    "source_title": "서울시 상권분석서비스",
                    "source_ref": "https://data.seoul.go.kr/foot-traffic",
                    "evidence_id": "evidence-foot-traffic",
                    "caveat": "분기 추정치이며 개별 점포 방문자 수가 아닙니다.",
                }
            ],
        }
    )

    class ComparisonResults:
        def get_current(self, **_: object) -> ResultView:
            return result

    runtime = FakeRuntime(
        {
            "intent": "COMPARE",
            "conclusion": "좌석 중심 후보는 유동인구 근거를 함께 확인해야 합니다.",
            "reasons": ["두 후보의 운영 구조와 상권 근거가 다릅니다."],
            "evidence_refs": ["evidence-foot-traffic"],
            "unknowns": [],
            "decision_change_conditions": [],
            "suggested_action": "NONE",
        }
    )
    service = ResultExplanationService(ComparisonResults(), runtime)

    answer = service.explain(
        project_id="project-1",
        user_id="user-1",
        result_bundle_id="result-1",
        candidate_id="candidate-1",
        question="두 후보는 무엇이 달라요?",
    )

    assert answer.evidence[0].evidence_id == "evidence-foot-traffic"
    assert answer.evidence[0].source_ref == "https://data.seoul.go.kr/foot-traffic"
