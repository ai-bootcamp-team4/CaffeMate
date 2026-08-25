from typing import Any

from app.decisions.models import (
    DecisionInput,
    DecisionRole,
    ResolutionAction,
    ResolutionActionType,
    ResolutionStatus,
    VerificationRequirement,
)
from app.domain.errors import ContractValidationError
from app.finance.models import CostCategory, CostLine, FinanceInput, ValueProvenance

_PROPERTY_CATEGORIES = {
    CostCategory.DEPOSIT,
    CostCategory.ACQUISITION_OR_PREMIUM,
    CostCategory.MONTHLY_OCCUPANCY,
}




def project_gate_results(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    capital_gate = candidate.get("capital_gate")
    if not isinstance(capital_gate, dict):
        return []
    trace = capital_gate.get("trace")
    return [trace] if isinstance(trace, dict) else []


def project_calculated_finance_decision_inputs(
    *,
    finance_input: dict[str, Any],
    evidence_records: list[dict[str, Any]],
    case_type: str,
) -> list[dict[str, Any]]:
    try:
        parsed = FinanceInput.model_validate(finance_input)
    except ValueError as error:
        raise ContractValidationError("Calculated candidate finance input is invalid") from error
    return project_finance_decision_inputs(
        initial_cost_lines=parsed.initial_cost_lines,
        monthly_fixed_cost_lines=parsed.monthly_fixed_cost_lines,
        evidence_records=evidence_records,
        case_type=case_type,
    )


def project_finance_decision_inputs(
    *,
    initial_cost_lines: list[CostLine],
    monthly_fixed_cost_lines: list[CostLine],
    evidence_records: list[dict[str, Any]],
    case_type: str,
) -> list[dict[str, Any]]:
    evidence_by_id = {
        str(record["evidence_id"]): record
        for record in evidence_records
        if isinstance(record, dict) and isinstance(record.get("evidence_id"), str)
    }
    values: list[DecisionInput] = []
    for line in [*initial_cost_lines, *monthly_fixed_cost_lines]:
        action = _resolution_action(line, case_type=case_type)
        source = evidence_by_id.get(line.evidence_ref or "")
        source_metadata = source.get("source") if isinstance(source, dict) else None
        anchor = source.get("original_anchor") if isinstance(source, dict) else None
        values.append(
            DecisionInput(
                field=line.field_id,
                value_range_krw=line.amount,
                provenance=line.provenance,
                resolution_status=_resolution_status(line, action),
                decision_role=DecisionRole.FINANCE_INPUT,
                source_title=(
                    str(source_metadata.get("title"))
                    if isinstance(source_metadata, dict)
                    and isinstance(source_metadata.get("title"), str)
                    else _synthetic_source_title(line)
                ),
                source_ref=(
                    str(source_metadata.get("source_ref"))
                    if isinstance(source_metadata, dict)
                    and isinstance(source_metadata.get("source_ref"), str)
                    else None
                ),
                data_date=(
                    str(source_metadata.get("published_or_data_date"))
                    if isinstance(source_metadata, dict)
                    and isinstance(source_metadata.get("published_or_data_date"), str)
                    else None
                ),
                geographic_scope=(
                    source.get("geographic_scope")
                    if isinstance(source, dict)
                    and isinstance(source.get("geographic_scope"), dict)
                    else None
                ),
                source_anchor=(
                    str(anchor.get("locator"))
                    if isinstance(anchor, dict) and isinstance(anchor.get("locator"), str)
                    else None
                ),
                applied_to=_applied_to(line.category),
                replaceable_by=(
                    [] if action.action_type == ResolutionActionType.NONE else [action.action_type]
                ),
                resolution_action=action,
                limitation_code=_limitation_code(line),
            )
        )
    return [value.model_dump(mode="json") for value in values]


def project_verification_requirements(
    *,
    case_type: str,
    franchise_availability: str | None,
) -> list[dict[str, Any]]:
    if case_type != "FRANCHISE" or franchise_availability not in {
        "HQ_CONFIRMATION_REQUIRED",
        "UNKNOWN",
    }:
        return []
    requirement = VerificationRequirement(
        requirement_id="FRANCHISE_AREA_APPROVAL",
        status=ResolutionStatus.EXTERNAL_CONFIRMATION_REQUIRED,
        decision_role=DecisionRole.VERIFICATION_ONLY,
        resolver="FRANCHISE_HQ",
        reason_code="FRANCHISE_AREA_AVAILABILITY_UNCONFIRMED",
        required_evidence=["DATED_HQ_WRITTEN_CONFIRMATION"],
        resolution_action=ResolutionAction(
            action_type=ResolutionActionType.EXTERNAL_CONFIRMATION,
            target_fields=["franchise.area_availability"],
            accepted_document_types=[],
        ),
        why_caffemate_cannot_resolve=(
            "특정 후보 주소의 출점 승인 여부는 해당 프랜차이즈 본사가 결정합니다."
        ),
    )
    return [requirement.model_dump(mode="json")]


def _resolution_status(
    line: CostLine,
    action: ResolutionAction,
) -> ResolutionStatus:
    return {
        ValueProvenance.FACT: ResolutionStatus.RESOLVED_FACT,
        ValueProvenance.USER_INPUT: ResolutionStatus.RESOLVED_USER_CONFIRMED,
        ValueProvenance.BENCHMARK: ResolutionStatus.RESOLVED_BENCHMARK,
        ValueProvenance.ASSUMPTION: ResolutionStatus.ASSUMED,
        ValueProvenance.DERIVED: ResolutionStatus.RESOLVED_DERIVED,
        ValueProvenance.UNKNOWN: (
            ResolutionStatus.DOCUMENT_REQUIRED
            if action.action_type == ResolutionActionType.DOCUMENT_INTAKE
            else ResolutionStatus.INPUT_REQUIRED
        ),
    }[line.provenance]


def _resolution_action(line: CostLine, *, case_type: str) -> ResolutionAction:
    if line.provenance in {ValueProvenance.FACT, ValueProvenance.USER_INPUT}:
        return ResolutionAction(action_type=ResolutionActionType.NONE)
    if line.category in _PROPERTY_CATEGORIES:
        targets = {
            CostCategory.DEPOSIT: ["property.deposit_krw"],
            CostCategory.ACQUISITION_OR_PREMIUM: ["property.key_money_krw"],
            CostCategory.MONTHLY_OCCUPANCY: [
                "property.monthly_rent_krw",
                "property.management_fee_krw",
            ],
        }[line.category]
        return ResolutionAction(
            action_type=ResolutionActionType.PROPERTY_TERMS,
            target_fields=targets,
        )
    if line.category == CostCategory.CONSTRUCTION:
        return ResolutionAction(
            action_type=ResolutionActionType.DOCUMENT_INTAKE,
            target_fields=["finance.CONSTRUCTION"],
            accepted_document_types=["INTERIOR_QUOTE"],
        )
    if line.category == CostCategory.EQUIPMENT:
        return ResolutionAction(
            action_type=ResolutionActionType.DOCUMENT_INTAKE,
            target_fields=["finance.EQUIPMENT"],
            accepted_document_types=["EQUIPMENT_QUOTE"],
        )
    if line.category == CostCategory.FRANCHISE_INITIAL_FEES and case_type == "FRANCHISE":
        return ResolutionAction(
            action_type=ResolutionActionType.DOCUMENT_INTAKE,
            target_fields=["finance.FRANCHISE_INITIAL_FEES"],
            accepted_document_types=["FRANCHISE_DISCLOSURE", "FRANCHISE_AGREEMENT"],
        )
    return ResolutionAction(action_type=ResolutionActionType.NONE)


def _applied_to(category: CostCategory) -> list[str]:
    if category in {
        CostCategory.MONTHLY_OCCUPANCY,
        CostCategory.MONTHLY_LABOR,
        CostCategory.MONTHLY_OTHER_FIXED,
    }:
        return [
            "MONTHLY_FIXED_COST",
            "BREAK_EVEN_MONTHLY_SALES",
            "REQUIRED_DAILY_ORDERS",
            "RANK",
        ]
    return ["INITIAL_CASH", "CAPITAL_GATE", "RANK"]


def _synthetic_source_title(line: CostLine) -> str | None:
    if line.provenance == ValueProvenance.USER_INPUT:
        return "사용자 확인 점포 조건"
    if line.provenance == ValueProvenance.ASSUMPTION:
        return "등록 창업안 가정"
    if line.provenance == ValueProvenance.DERIVED:
        return "결정론적 계산"
    return None


def _limitation_code(line: CostLine) -> str | None:
    if line.provenance == ValueProvenance.ASSUMPTION:
        return "REPLACE_WITH_CASE_DATA"
    if line.provenance == ValueProvenance.UNKNOWN:
        return "VALUE_NOT_RESOLVED"
    return None
