"""Build deterministic franchise proposal drafts from verified brand inputs."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from app.candidates.models import (
    CandidateDecisionInput,
    FounderBurdenLevel,
    FounderFitStatus,
    FranchiseAvailability,
    RiskSeverity,
    RiskSignal,
)
from app.decisions.projection import (
    project_finance_decision_inputs,
    project_verification_requirements,
)
from app.domain.models import CaseType, FranchiseEligibility, VentureState
from app.finance.calculator import calculate_finance, evaluate_capital_gate
from app.finance.case_facts import FinancialInputResolver, PropertyContext
from app.finance.models import (
    INITIAL_COST_CATEGORIES,
    REGISTERED_MONTHLY_FIXED_COST_CATEGORIES,
    CapitalGateInput,
    CapitalGateStatus,
    FinanceInput,
)
from app.results.projection import project_evidence_for_candidate
from app.workflows.proposal_finance import (
    cost_refs,
    franchise_cost_line,
    franchise_variable_cost_rate_lines,
)
from app.workflows.proposal_presentation import candidate_id as build_candidate_id
from app.workflows.proposal_presentation import (
    counterfactuals,
    decimal_number,
    money_summary,
)


@dataclass(frozen=True)
class CandidateDraft:
    candidate: dict[str, Any]
    decision_input: CandidateDecisionInput


def build_franchise_drafts(
    *,
    state: VentureState,
    evidence_records: list[dict[str, Any]],
    property_context: PropertyContext | None,
    finance_resolver: FinancialInputResolver,
    proposed_ids: set[str] | None,
    franchise_universe: list[dict[str, Any]] | None,
) -> list[CandidateDraft]:
    universe_by_id = {
        value["brand_id"]: value
        for value in (franchise_universe or [])
        if isinstance(value, dict)
        and isinstance(value.get("brand_id"), str)
        and value.get("individual_franchise_eligibility") == "VERIFIED"
    }
    brands = [
        value
        for brand_id, value in universe_by_id.items()
        if proposed_ids is None or brand_id in proposed_ids
    ]
    drafts: list[CandidateDraft] = []
    for brand in brands:
        brand_id = brand["brand_id"]
        name = brand["display_name"]
        (
            brand_evidence_refs,
            brand_signals,
            brand_documents,
            brand_document_gaps,
        ) = project_evidence_for_candidate(
            evidence_records,
            project_id=state.project_id,
            case_type="FRANCHISE",
            source_id=brand_id,
        )
        profile = brand.get("finance_profile")
        if not isinstance(profile, dict):
            raise ValueError(f"Verified franchise has no finance profile: {brand_id}")
        eligibility_refs = [
            value for value in brand.get("evidence_refs", []) if isinstance(value, str)
        ]
        candidate_evidence_refs = sorted(
            set(brand_evidence_refs)
            | set(eligibility_refs)
            | {
                value
                for value in profile.get("evidence_refs", [])
                if isinstance(value, str)
            }
        )
        assumption_ref = f"declared-assumption:{brand_id}:2026-08-24"
        initial_cost_lines = [
            franchise_cost_line(
                brand_id=brand_id,
                category=category,
                finance_profile=profile,
                resolver=finance_resolver,
            )
            for category in sorted(INITIAL_COST_CATEGORIES, key=lambda item: item.value)
        ]
        monthly_fixed_cost_lines = [
            franchise_cost_line(
                brand_id=brand_id,
                category=category,
                finance_profile=profile,
                resolver=finance_resolver,
            )
            for category in sorted(
                REGISTERED_MONTHLY_FIXED_COST_CATEGORIES,
                key=lambda item: item.value,
            )
        ]
        variable_cost_rate_lines = franchise_variable_cost_rate_lines(
            brand_id=brand_id,
            finance_profile=profile,
            resolver=finance_resolver,
        )
        finance = calculate_finance(
            FinanceInput(
                initial_cost_lines=initial_cost_lines,
                monthly_fixed_cost_lines=monthly_fixed_cost_lines,
                variable_cost_rate_lines=variable_cost_rate_lines,
                contribution_margin_bps=6_500,
                operating_days_per_month=26,
                average_ticket_krw=7_000,
            )
        )
        gate = evaluate_capital_gate(
            CapitalGateInput(
                own_funds_krw=state.founder.own_funds_krw,
                borrowing_intent=state.founder.borrowing_intent,
                initial_cash=finance.initial_cash,
            )
        )
        candidate_id = build_candidate_id(
            project_id=state.project_id,
            case_type="FRANCHISE",
            source_id=brand_id,
        )
        drafts.append(
            CandidateDraft(
                candidate={
                    "schema_version": "2.0.0",
                    "candidate_id": candidate_id,
                    "project_id": state.project_id,
                    "state_version": state.state_version,
                    "case_type": "FRANCHISE",
                    "display_name": name,
                    "review_status": (
                        "EXCLUDED"
                        if gate.status == CapitalGateStatus.FAIL
                        else "CONDITIONAL_REVIEW"
                    ),
                    "reason_codes": [gate.reason_code],
                    "summary": (
                        "현재 자금과 확인된 공식 가맹 안내를 기준으로 다음 검토 가치가 "
                        "있는 조건부 후보입니다."
                    ),
                    "rank": 1,
                    "rank_basis": "NEXT_REVIEW_PRIORITY",
                    "is_primary_next_review": True,
                    "franchise": {
                        "brand_id": brand_id,
                        "eligibility": "VERIFIED",
                        "availability_status": "HQ_CONFIRMATION_REQUIRED",
                        "eligibility_evidence_refs": eligibility_refs,
                        "disclosure_evidence_refs": [],
                        "finance_profile": deepcopy(profile),
                    },
                    "independent_model": None,
                    "property_context": (
                        property_context.public_projection()
                        if property_context is not None
                        and property_context.source_id == brand_id
                        else None
                    ),
                    "evidence_refs": candidate_evidence_refs,
                    "assumption_refs": [assumption_ref],
                    "market_signals": brand_signals,
                    "official_documents": brand_documents,
                    "official_document_gaps": sorted(
                        set(brand_document_gaps) | {"정보공개서 공식 문서"}
                    ),
                    "gate_results": [
                        gate.trace.model_dump(mode="json")
                    ]
                    if gate.trace
                    else [],
                    "decision_inputs": project_finance_decision_inputs(
                        initial_cost_lines=initial_cost_lines,
                        monthly_fixed_cost_lines=monthly_fixed_cost_lines,
                        variable_cost_rate_lines=variable_cost_rate_lines,
                        evidence_records=evidence_records,
                        case_type="FRANCHISE",
                        decision_sources=finance_resolver.decision_sources,
                    ),
                    "verification_requirements": project_verification_requirements(
                        case_type="FRANCHISE",
                        franchise_availability="HQ_CONFIRMATION_REQUIRED",
                    ),
                    "financial_summary": {
                        "initial_cash": money_summary(
                            finance.initial_cash,
                            cost_refs(initial_cost_lines),
                        ),
                        "monthly_fixed_cost": money_summary(
                            finance.monthly_fixed_cost,
                            cost_refs(monthly_fixed_cost_lines),
                        ),
                        "base_contribution_margin_bps": finance.base_contribution_margin_bps,
                        "variable_cost_rate_bps": finance.variable_cost_rate_bps,
                        "effective_contribution_margin_bps": (
                            finance.effective_contribution_margin_bps
                        ),
                        "break_even_monthly_sales_krw": finance.break_even_monthly_sales_krw,
                        "required_daily_orders": decimal_number(finance.required_daily_orders),
                        "unknown_cost_fields": finance.unknown_cost_fields,
                    },
                    "missing_fields": [
                        {
                            "field": "정보공개서",
                            "impact": "가맹 조건과 비용의 완전성을 확정할 수 없습니다.",
                            "next_check": "최신 정보공개서를 확보해 조건을 다시 계산합니다.",
                        },
                    ],
                    "risks": [
                        {
                            "risk_id": "FRANCHISE_DISCLOSURE_INCOMPLETE",
                            "severity": "HIGH",
                            "summary": "최신 정보공개서로 비용·계약 조건을 보강해야 합니다.",
                            "evidence_refs": candidate_evidence_refs,
                        }
                    ],
                    "counterfactuals": counterfactuals(
                        gate.minimum_required_reduction_krw
                    ),
                    "next_actions": [
                        "최신 정보공개서를 확보합니다.",
                        "본사에 후보 지역 출점 가능 여부를 확인합니다.",
                        "실제 점포 임대 조건으로 비용을 다시 계산합니다.",
                    ],
                },
                decision_input=CandidateDecisionInput(
                    candidate_id=candidate_id,
                    case_type=CaseType.FRANCHISE,
                    finance=finance,
                    capital_gate=gate,
                    founder_fit=FounderFitStatus.PASS,
                    founder_burden=FounderBurdenLevel.MEDIUM,
                    material_missing_fields=["franchise_disclosure"],
                    conditional_reason_codes=["FRANCHISE_DISCLOSURE_MISSING"],
                    risks=[
                        RiskSignal(
                            risk_id="FRANCHISE_DISCLOSURE_INCOMPLETE",
                            severity=RiskSeverity.HIGH,
                        )
                    ],
                    franchise_eligibility=FranchiseEligibility.VERIFIED,
                    franchise_eligibility_evidence_refs=eligibility_refs,
                    franchise_availability=FranchiseAvailability.HQ_CONFIRMATION_REQUIRED,
                ),
            )
        )
    return drafts
