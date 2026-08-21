from typing import Any

import pytest
from pydantic import ValidationError

from app.domain.errors import ContractValidationError
from app.results.models import ResultBundlePayload


def candidate(
    *,
    candidate_id: str = "candidate-1",
    rank: int | None = 1,
    primary: bool = True,
    review_status: str = "REVIEW_RECOMMENDED",
) -> dict[str, Any]:
    return {
        "schema_version": "2.0.0",
        "candidate_id": candidate_id,
        "project_id": "project-1",
        "state_version": 1,
        "case_type": "INDEPENDENT",
        "display_name": "소형 개인카페",
        "review_status": review_status,
        "reason_codes": ["CURRENT_CONSTRAINTS_SATISFIED"],
        "summary": "다음 검토 후보",
        "rank": rank,
        "rank_basis": "ECONOMIC_AND_FOUNDER_FIT",
        "is_primary_next_review": primary,
        "franchise": None,
        "independent_model": {"model_id": "independent-v1", "adjusted_fields": []},
        "evidence_refs": ["evidence-1"],
        "assumption_refs": [],
        "financial_summary": {
            "initial_cash": {
                "currency": "KRW",
                "low": 40_000_000,
                "base": 45_000_000,
                "high": 50_000_000,
                "provenance_refs": ["evidence-1"],
            },
            "monthly_fixed_cost": {
                "currency": "KRW",
                "low": 5_000_000,
                "base": 6_000_000,
                "high": 7_000_000,
                "provenance_refs": ["evidence-1"],
            },
            "unknown_cost_fields": [],
        },
        "missing_fields": [],
        "risks": [],
        "counterfactuals": [
            {
                "variable": "rent",
                "condition": "월세 상승",
                "decision_impact": "재검토",
            }
        ],
        "next_actions": ["점포 확인"],
    }


def bundle(*candidates: dict[str, Any]) -> ResultBundlePayload:
    return ResultBundlePayload(
        candidates=list(candidates),
        primary_candidate_id="candidate-1",
        audit_status="PASSED",
    )


def test_bundle_requires_contiguous_ordered_rank_and_exactly_one_primary() -> None:
    with pytest.raises(ValidationError):
        bundle(
            candidate(),
            candidate(candidate_id="candidate-2", rank=3, primary=False),
        )
    with pytest.raises(ValidationError):
        bundle(candidate(primary=False))


def test_excluded_candidate_cannot_enter_visible_bundle() -> None:
    with pytest.raises(ValidationError):
        bundle(candidate(rank=None, primary=False, review_status="EXCLUDED"))


def test_candidate_contract_and_authoritative_head_are_both_required() -> None:
    value = bundle(candidate())
    value.validate_contracts(project_id="project-1", state_version=1)

    with pytest.raises(ContractValidationError):
        bundle(candidate() | {"unexpected": True}).validate_contracts(
            project_id="project-1",
            state_version=1,
        )
    with pytest.raises(ValueError):
        value.validate_contracts(project_id="project-2", state_version=1)
