from decimal import Decimal
from typing import Any

from app.finance.models import CapitalGateStatus, MoneyRange


def money_summary(value: MoneyRange, refs: list[str]) -> dict[str, Any]:
    return {
        "currency": "KRW",
        "low": value.low,
        "base": value.base,
        "high": value.high,
        "provenance_refs": refs if value.base is not None else [],
    }


def decimal_number(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def review_status(status: CapitalGateStatus) -> str:
    return {
        CapitalGateStatus.PASS: "REVIEW_RECOMMENDED",
        CapitalGateStatus.CONDITIONAL: "CONDITIONAL_REVIEW",
        CapitalGateStatus.FAIL: "EXCLUDED",
    }[status]


def candidate_summary(status: CapitalGateStatus) -> str:
    return {
        CapitalGateStatus.PASS: "현재 자금 범위에서 다음 검토 가치가 있는 창업안입니다.",
        CapitalGateStatus.CONDITIONAL: (
            "자금 조달 또는 실제 점포 비용을 확인하면서 검토할 창업안입니다."
        ),
        CapitalGateStatus.FAIL: "현재 확인된 자금 조건으로는 진행하기 어려운 창업안입니다.",
    }[status]


def capital_risks(
    status: CapitalGateStatus,
    evidence_refs: list[str],
) -> list[dict[str, Any]]:
    if status == CapitalGateStatus.PASS:
        return []
    return [
        {
            "risk_id": "CAPITAL_COVERAGE_REQUIRES_CONFIRMATION",
            "severity": "HIGH" if status == CapitalGateStatus.FAIL else "MEDIUM",
            "summary": "실제 점포 비용과 자금 조달 가능 범위를 확인해야 합니다.",
            "evidence_refs": evidence_refs,
        }
    ]


def counterfactuals(reduction: int | None) -> list[dict[str, str]]:
    if reduction is None:
        return [
            {
                "variable": "실제 점포 비용",
                "condition": "실제 견적이 현재 참고 범위보다 낮아지는 경우",
                "decision_impact": "자금 적합성 판단이 좋아질 수 있습니다.",
            }
        ]
    return [
        {
            "variable": "초기 필요자금",
            "condition": f"최소 {reduction:,}원 이상 줄어드는 경우",
            "decision_impact": "현재 자기자금 기준의 제외 판단을 다시 검토합니다.",
        }
    ]


def next_actions(status: CapitalGateStatus) -> list[str]:
    if status == CapitalGateStatus.FAIL:
        return [
            "예산에 가까운 작은 운영안을 비교합니다.",
            "추가 자금 조건을 입력합니다.",
        ]
    return [
        "후보를 선택하고 실제 점포 조건을 입력합니다.",
        "보증금·월세·권리금과 견적을 확인합니다.",
    ]


def candidate_id(*, project_id: str, case_type: str, source_id: str) -> str:
    """Stable across state revisions so selected-case data can be reapplied."""

    return f"{project_id}:{case_type}:{source_id}"
