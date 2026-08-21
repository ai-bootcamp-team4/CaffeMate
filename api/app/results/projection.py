from decimal import Decimal
from typing import Any

from app.contracts.schema_registry import CandidateContractValidator, ContractRegistry
from app.domain.errors import ContractValidationError


def project_candidate_results(
    candidates: list[dict[str, Any]],
    *,
    project_id: str,
    state_version: int,
    evidence_records: list[dict[str, Any]],
    contracts: CandidateContractValidator | None = None,
) -> list[dict[str, Any]]:
    validator = contracts or ContractRegistry()
    evidence_by_id = _evidence_by_id(evidence_records, project_id)
    projected = [
        _project_candidate(
            candidate,
            project_id=project_id,
            state_version=state_version,
            evidence_by_id=evidence_by_id,
        )
        for candidate in candidates
    ]
    for candidate in projected:
        validator.validate_candidate_result(candidate)
    return projected


def _project_candidate(
    candidate: dict[str, Any],
    *,
    project_id: str,
    state_version: int,
    evidence_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    decision = _required_mapping(candidate, "decision")
    finance = _required_mapping(candidate, "finance")
    finance_input = _required_mapping(candidate, "finance_input")
    proposal = _required_mapping(candidate, "proposal")
    case_type = candidate.get("case_type")
    if case_type not in {"INDEPENDENT", "FRANCHISE"}:
        raise ContractValidationError("Calculated candidate case type is invalid")

    review_status = decision.get("review_status")
    reason_codes = _strings(decision.get("reason_codes"))
    if not reason_codes:
        raise ContractValidationError("Calculated candidate requires decision reasons")
    initial_refs = _cost_refs(finance_input.get("initial_cost_lines"))
    monthly_refs = _cost_refs(finance_input.get("monthly_fixed_cost_lines"))
    eligibility_refs = _strings(candidate.get("franchise_eligibility_evidence_refs"))
    proposal_evidence_refs = _strings(proposal.get("evidence_refs"))
    calculation_refs = _strings(candidate.get("calculation_evidence_refs"))
    grounded_refs, calculation_assumption_refs = _classify_refs(
        initial_refs
        + monthly_refs
        + eligibility_refs
        + proposal_evidence_refs
        + calculation_refs,
        evidence_by_id,
    )
    if not set(eligibility_refs).issubset(grounded_refs):
        raise ContractValidationError(
            "Franchise eligibility requires grounded Evidence records"
        )
    unknown_fields = _strings(finance.get("unknown_cost_fields"))
    material_missing = _strings(candidate.get("material_missing_fields"))
    rank = decision.get("rank")
    rank_basis = decision.get("rank_basis")

    source_id = candidate.get("source_id")
    if not isinstance(source_id, str) or not source_id:
        raise ContractValidationError("Calculated candidate source id is invalid")
    adjusted_fields = sorted(
        {
            value["field_path"]
            for value in proposal.get("adjusted_parameters", [])
            if isinstance(value, dict)
            and isinstance(value.get("field_path"), str)
            and value["field_path"]
        }
    )
    franchise = None
    independent_model = None
    if case_type == "FRANCHISE":
        franchise = {
            "brand_id": source_id,
            "eligibility": "VERIFIED",
            "availability_status": candidate.get("franchise_availability"),
            "eligibility_evidence_refs": eligibility_refs,
            "disclosure_evidence_refs": sorted(
                set(proposal_evidence_refs) - set(eligibility_refs)
            ),
        }
    else:
        independent_model = {
            "model_id": source_id,
            "adjusted_fields": adjusted_fields,
        }

    projected = {
        "schema_version": "2.0.0",
        "candidate_id": candidate.get("candidate_id"),
        "project_id": project_id,
        "state_version": state_version,
        "case_type": case_type,
        "display_name": candidate.get("display_name"),
        "review_status": review_status,
        "reason_codes": reason_codes,
        "summary": _summary(review_status),
        "rank": rank,
        "rank_basis": rank_basis,
        "is_primary_next_review": decision.get("is_primary_next_review"),
        "franchise": franchise,
        "independent_model": independent_model,
        "evidence_refs": sorted(grounded_refs),
        "assumption_refs": sorted(
            set(_strings(proposal.get("assumption_refs")))
            | calculation_assumption_refs
        ),
        "financial_summary": {
            "initial_cash": _money_summary(
                finance.get("initial_cash"),
                initial_refs,
            ),
            "monthly_fixed_cost": _money_summary(
                finance.get("monthly_fixed_cost"),
                monthly_refs,
            ),
            "break_even_monthly_sales_krw": finance.get(
                "break_even_monthly_sales_krw"
            ),
            "required_daily_orders": _decimal_number(
                finance.get("required_daily_orders")
            ),
            "unknown_cost_fields": unknown_fields,
        },
        "missing_fields": [
            {
                "field": field,
                "impact": "후보의 비용 또는 현실성 판단을 확정할 수 없습니다.",
                "next_check": f"{field} 값을 공식 자료나 실제 견적으로 확인합니다.",
            }
            for field in material_missing
        ],
        "risks": [
            {
                "risk_id": value.get("risk_id"),
                "severity": value.get("severity"),
                "summary": "현재 근거에서 추가 검토가 필요한 위험 신호입니다.",
                "evidence_refs": [],
            }
            for value in candidate.get("risks", [])
            if isinstance(value, dict)
        ],
        "counterfactuals": [
            _counterfactual(value)
            for value in candidate.get("counterfactuals", [])
            if isinstance(value, dict)
        ],
        "next_actions": _next_actions(review_status, material_missing),
    }
    return projected


def _required_mapping(value: dict[str, Any], key: str) -> dict[str, Any]:
    child = value.get(key)
    if not isinstance(child, dict):
        raise ContractValidationError(f"Calculated candidate {key} is invalid")
    return child


def _evidence_by_id(
    records: list[dict[str, Any]],
    project_id: str,
) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for record in records:
        evidence_id = record.get("evidence_id")
        if (
            not isinstance(evidence_id, str)
            or not evidence_id
            or record.get("project_id") != project_id
        ):
            raise ContractValidationError("Candidate Evidence record is invalid")
        previous = values.get(evidence_id)
        if previous is not None and previous != record:
            raise ContractValidationError("Candidate Evidence id is conflicting")
        values[evidence_id] = record
    return values


def _classify_refs(
    references: list[str],
    evidence_by_id: dict[str, dict[str, Any]],
) -> tuple[set[str], set[str]]:
    grounded: set[str] = set()
    assumptions: set[str] = set()
    for reference in references:
        record = evidence_by_id.get(reference)
        if record is None:
            raise ContractValidationError(
                f"Candidate used an unavailable Evidence ref: {reference}"
            )
        if record.get("value_kind") in {"DECLARED_ASSUMPTION", "UNKNOWN"}:
            assumptions.add(reference)
        else:
            grounded.add(reference)
    return grounded, assumptions


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({item for item in value if isinstance(item, str) and item})


def _cost_refs(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted(
        {
            item["evidence_ref"]
            for item in value
            if isinstance(item, dict)
            and isinstance(item.get("evidence_ref"), str)
            and item["evidence_ref"]
        }
    )


def _money_summary(value: object, provenance_refs: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractValidationError("Calculated candidate money range is invalid")
    low = value.get("low")
    base = value.get("base")
    high = value.get("high")
    known = all(
        isinstance(item, int) and not isinstance(item, bool)
        for item in (low, base, high)
    )
    return {
        "currency": "KRW",
        "low": low if known else None,
        "base": base if known else None,
        "high": high if known else None,
        "provenance_refs": provenance_refs if known else [],
    }


def _decimal_number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal | str | int | float) and not isinstance(value, bool):
        return float(value)
    raise ContractValidationError("Required daily orders is invalid")


def _summary(review_status: object) -> str:
    return {
        "REVIEW_RECOMMENDED": "현재 확인된 조건에서 다음 검토를 진행할 수 있는 후보입니다.",
        "CONDITIONAL_REVIEW": "중요한 누락값을 확인하면서 검토할 조건부 후보입니다.",
        "EXCLUDED": "확인된 필수 조건 위반으로 현재 결과 순위에서 제외된 후보입니다.",
    }.get(str(review_status), "현재 후보 판단을 확인해야 합니다.")


def _counterfactual(value: dict[str, Any]) -> dict[str, str]:
    kind = value.get("kind")
    if kind == "INITIAL_CASH_REDUCTION_TO_CLEAR_HARD_GATE":
        amount = value.get("amount_krw")
        return {
            "variable": "initial_cash_krw",
            "condition": f"초기 필요 현금을 {amount}원 이상 줄입니다.",
            "decision_impact": "자금 필수 조건의 제외 판단을 다시 계산할 수 있습니다.",
        }
    if kind == "INITIAL_CASH_INCREASE_BEFORE_GATE_CHANGES":
        amount = value.get("amount_krw")
        return {
            "variable": "initial_cash_krw",
            "condition": f"초기 필요 현금이 현재 범위에서 {amount}원 이상 증가합니다.",
            "decision_impact": "자금 조건의 통과 여부가 바뀔 수 있습니다.",
        }
    fields = ", ".join(_strings(value.get("field_ids"))) or "unknown_cost_fields"
    return {
        "variable": fields,
        "condition": "누락된 비용을 공식 자료나 실제 견적으로 확인합니다.",
        "decision_impact": "자금 조건과 후보 상태를 다시 계산할 수 있습니다.",
    }


def _next_actions(review_status: object, missing_fields: list[str]) -> list[str]:
    if missing_fields:
        return [f"{field} 값을 확인합니다." for field in missing_fields[:3]]
    if review_status == "EXCLUDED":
        return ["확인된 제외 사유와 판단 반전 조건을 검토합니다."]
    return ["실제 점포와 견적 자료를 추가해 후보를 구체화합니다."]
