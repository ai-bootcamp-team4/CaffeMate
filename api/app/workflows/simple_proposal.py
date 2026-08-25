"""사용자는 검증 게이트를 기다리지 않고 근거와 가정을 구분한 후보를 비교한다."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from app.candidates.models import (
    CandidateDecisionInput,
    FounderBurdenLevel,
    FounderFitStatus,
    FranchiseAvailability,
    ReviewStatus,
    RiskSeverity,
    RiskSignal,
)
from app.candidates.ranking import rank_candidates
from app.candidates.seed_registry import (
    IndependentSeedDefinition,
    IndependentSeedRegistry,
)
from app.decisions.projection import (
    project_finance_decision_inputs,
    project_verification_requirements,
)
from app.domain.models import (
    CafeTypePreference,
    CaseType,
    FranchiseEligibility,
    VentureState,
)
from app.finance.calculator import calculate_finance, evaluate_capital_gate
from app.finance.case_facts import (
    CaseFactResolution,
    FinancialInputResolver,
    PropertyContext,
)
from app.finance.models import (
    INITIAL_COST_CATEGORIES,
    MONTHLY_FIXED_COST_CATEGORIES,
    CapitalGateInput,
    CapitalGateStatus,
    CostCategory,
    CostLine,
    FinanceInput,
    MoneyRange,
    ValueProvenance,
)
from app.finance.property_benchmark import (
    PropertyRentBenchmark,
    resolve_seed_property_benchmarks,
)
from app.results.models import (
    AuditStatus,
    ResultBundlePayload,
    ResultOutcomeStatus,
)
from app.results.projection import project_evidence_for_candidate
from app.workflows.proposal_finance import (
    cost_refs,
    franchise_cost_line,
    independent_cost_line,
    property_adjusted_fields,
)
from app.workflows.proposal_presentation import candidate_id as build_candidate_id
from app.workflows.proposal_presentation import (
    candidate_summary,
    capital_risks,
    counterfactuals,
    decimal_number,
    money_summary,
    next_actions,
    review_status,
)


@dataclass(frozen=True)
class _CandidateDraft:
    candidate: dict[str, Any]
    decision_input: CandidateDecisionInput


class SimpleProposalBuilder:
    """Build a reviewable proposal without a multi-stage control plane.

    Registered assumptions and deterministic finance math guarantee that the
    user receives a useful card even when external retrieval is unavailable.
    Previously accepted Evidence records are attached when present.
    """

    def __init__(self, seed_registry: IndependentSeedRegistry) -> None:
        self._seeds = seed_registry

    def build(
        self,
        *,
        state: VentureState,
        evidence_records: list[dict[str, Any]],
        property_context: PropertyContext | None = None,
        case_fact_resolution: CaseFactResolution | None = None,
        property_rent_benchmarks: list[PropertyRentBenchmark] | None = None,
        agent_proposals: list[dict[str, Any]] | None = None,
        franchise_universe: list[dict[str, Any]] | None = None,
    ) -> ResultBundlePayload:
        drafts: list[_CandidateDraft] = []
        independent_seeds = self._seeds.select(state.founder)
        benchmark_resolution = resolve_seed_property_benchmarks(
            seeds=[
                (
                    seed.model_id,
                    profile.space_profile_sqm,
                    profile.commercial_property_class,
                    profile.management_fee_ratio_bps,
                    profile.cost_ranges[CostCategory.DEPOSIT],
                )
                for seed in independent_seeds
                if (profile := seed.finance_profile) is not None
            ],
            benchmarks=property_rent_benchmarks or [],
        )
        finance_resolver = FinancialInputResolver(
            property_context=property_context,
            case_resolution=case_fact_resolution,
            benchmark_resolution=benchmark_resolution,
        )
        preference = state.founder.cafe_type_preference
        proposals_by_source = {
            proposal["seed_or_brand_id"]: proposal
            for proposal in (agent_proposals or [])
            if isinstance(proposal, dict)
            and isinstance(proposal.get("seed_or_brand_id"), str)
        }
        proposed_ids = (
            {
                proposal["seed_or_brand_id"]
                for proposal in agent_proposals
                if isinstance(proposal.get("seed_or_brand_id"), str)
            }
            if agent_proposals is not None
            else None
        )
        if preference in {
            CafeTypePreference.OPEN_TO_BOTH,
            CafeTypePreference.INDEPENDENT_ONLY,
        }:
            drafts.extend(
                self._independent_draft(
                    state=state,
                    seed=seed,
                    evidence_records=evidence_records,
                    property_context=property_context,
                    finance_resolver=finance_resolver,
                    agent_proposal=proposals_by_source.get(seed.model_id),
                )
                for seed in independent_seeds
                if proposed_ids is None or seed.model_id in proposed_ids
            )
        if preference in {
            CafeTypePreference.OPEN_TO_BOTH,
            CafeTypePreference.FRANCHISE_ONLY,
        }:
            drafts.extend(
                self._franchise_drafts(
                    state=state,
                    evidence_records=evidence_records,
                    property_context=property_context,
                    finance_resolver=finance_resolver,
                    proposed_ids=proposed_ids,
                    franchise_universe=franchise_universe,
                )
            )
        if not drafts:
            raise ValueError("No registered candidate is compatible with the founder state")
        if preference == CafeTypePreference.OPEN_TO_BOTH:
            drafts = self._open_to_both_pool(drafts, limit=3)

        decisions = rank_candidates([draft.decision_input for draft in drafts])
        reviewable = [
            decision for decision in decisions if decision.review_status != ReviewStatus.EXCLUDED
        ]
        selected_decisions = (reviewable if reviewable else decisions)[:3]
        drafts_by_id = {str(draft.candidate["candidate_id"]): draft for draft in drafts}
        selected: list[_CandidateDraft] = []
        for decision in selected_decisions:
            draft = drafts_by_id[decision.candidate_id]
            candidate = draft.candidate
            candidate["review_status"] = decision.review_status.value
            candidate["reason_codes"] = decision.reason_codes
            candidate["rank"] = decision.rank
            candidate["rank_basis"] = decision.rank_basis.value
            candidate["is_primary_next_review"] = decision.is_primary_next_review
            candidate["rank_trace"] = (
                decision.rank_trace.model_dump(mode="json")
                if decision.rank_trace is not None
                else None
            )
            selected.append(draft)
        no_reviewable = not reviewable

        payload = ResultBundlePayload(
            candidates=[draft.candidate for draft in selected],
            primary_candidate_id=(
                None if no_reviewable else str(selected[0].candidate["candidate_id"])
            ),
            audit_status=AuditStatus.PASSED,
            outcome_status=(
                ResultOutcomeStatus.NO_REVIEWABLE_CANDIDATES
                if no_reviewable
                else ResultOutcomeStatus.REVIEWABLE_CANDIDATES
            ),
        )
        payload.validate_contracts(
            project_id=state.project_id,
            state_version=state.state_version,
        )
        return payload

    @staticmethod
    def _open_to_both_pool(
        drafts: list[_CandidateDraft],
        *,
        limit: int,
    ) -> list[_CandidateDraft]:
        """Keep both requested candidate families visible before authoritative ranking."""

        if len(drafts) <= limit:
            return drafts
        independent = [
            draft for draft in drafts if draft.decision_input.case_type == CaseType.INDEPENDENT
        ]
        franchise = [
            draft for draft in drafts if draft.decision_input.case_type == CaseType.FRANCHISE
        ]
        if not independent or not franchise:
            return drafts[:limit]
        selected = [independent[0], franchise[0]]
        selected_ids = {str(draft.candidate["candidate_id"]) for draft in selected}
        for draft in drafts:
            if len(selected) >= limit:
                break
            candidate_id = str(draft.candidate["candidate_id"])
            if candidate_id not in selected_ids:
                selected.append(draft)
                selected_ids.add(candidate_id)
        return selected

    def _independent_draft(
        self,
        *,
        state: VentureState,
        seed: IndependentSeedDefinition,
        evidence_records: list[dict[str, Any]],
        property_context: PropertyContext | None,
        finance_resolver: FinancialInputResolver,
        agent_proposal: dict[str, Any] | None,
    ) -> _CandidateDraft:
        profile = seed.finance_profile
        if profile is None:
            raise ValueError(f"Registered seed has no finance profile: {seed.model_id}")
        assumption_refs = sorted(set(seed.support_refs))
        initial_cost_lines = [
            independent_cost_line(
                seed.model_id,
                category,
                profile.cost_ranges[category],
                finance_resolver,
            )
            for category in sorted(INITIAL_COST_CATEGORIES, key=lambda item: item.value)
            if category != CostCategory.FRANCHISE_INITIAL_FEES
        ]
        initial_cost_lines.append(
            CostLine(
                field_id=CostCategory.FRANCHISE_INITIAL_FEES.value,
                category=CostCategory.FRANCHISE_INITIAL_FEES,
                amount=MoneyRange(low=0, base=0, high=0),
                provenance=ValueProvenance.DERIVED,
            )
        )
        monthly_fixed_cost_lines = [
            independent_cost_line(
                seed.model_id,
                category,
                profile.cost_ranges[category],
                finance_resolver,
            )
            for category in sorted(MONTHLY_FIXED_COST_CATEGORIES, key=lambda item: item.value)
        ]
        finance_input = FinanceInput(
            initial_cost_lines=initial_cost_lines,
            monthly_fixed_cost_lines=monthly_fixed_cost_lines,
            contribution_margin_bps=profile.contribution_margin_bps,
            operating_days_per_month=profile.operating_days_per_month,
            average_ticket_krw=profile.average_ticket_krw,
        )
        finance = calculate_finance(finance_input)
        gate = evaluate_capital_gate(
            CapitalGateInput(
                own_funds_krw=state.founder.own_funds_krw,
                borrowing_intent=state.founder.borrowing_intent,
                initial_cash=finance.initial_cash,
            )
        )
        evidence_refs, signals, documents, document_gaps = project_evidence_for_candidate(
            evidence_records,
            project_id=state.project_id,
            case_type="INDEPENDENT",
        )
        candidate_id = build_candidate_id(
            project_id=state.project_id,
            case_type="INDEPENDENT",
            source_id=seed.model_id,
        )
        conditional_reason_codes = (
            ["EVIDENCE_CONFIRMATION_REQUIRED"]
            if gate.status == CapitalGateStatus.PASS and not evidence_refs
            else []
        )
        adjusted_fields = set(
            property_adjusted_fields(seed.model_id, property_context)
        )
        adjusted_fields.update(
            value["field_path"]
            for value in (agent_proposal or {}).get("adjusted_parameters", [])
            if isinstance(value, dict)
            and isinstance(value.get("field_path"), str)
            and value["field_path"]
        )
        risk_values = capital_risks(gate.status, evidence_refs)
        candidate = {
                "schema_version": "2.0.0",
                "candidate_id": candidate_id,
                "project_id": state.project_id,
                "state_version": state.state_version,
                "case_type": "INDEPENDENT",
                "display_name": seed.display_name,
                "review_status": review_status(gate.status),
                "reason_codes": [gate.reason_code],
                "summary": candidate_summary(gate.status),
                "rank": 1,
                "rank_basis": "NEXT_REVIEW_PRIORITY",
                "is_primary_next_review": True,
                "franchise": None,
                "independent_model": {
                    "model_id": seed.model_id,
                    "adjusted_fields": sorted(adjusted_fields),
                },
                "property_context": (
                    property_context.public_projection()
                    if property_context is not None
                    and property_context.source_id == seed.model_id
                    else None
                ),
                "evidence_refs": evidence_refs,
                "assumption_refs": assumption_refs,
                "market_signals": signals,
                "official_documents": documents,
                "official_document_gaps": document_gaps,
                "gate_results": [gate.trace.model_dump(mode="json")] if gate.trace else [],
                "decision_inputs": project_finance_decision_inputs(
                    initial_cost_lines=initial_cost_lines,
                    monthly_fixed_cost_lines=monthly_fixed_cost_lines,
                    evidence_records=evidence_records,
                    case_type="INDEPENDENT",
                    decision_sources=finance_resolver.decision_sources,
                ),
                "verification_requirements": [],
                "financial_summary": {
                    "initial_cash": money_summary(
                        finance.initial_cash,
                        cost_refs(initial_cost_lines),
                    ),
                    "monthly_fixed_cost": money_summary(
                        finance.monthly_fixed_cost,
                        cost_refs(monthly_fixed_cost_lines),
                    ),
                    "break_even_monthly_sales_krw": finance.break_even_monthly_sales_krw,
                    "required_daily_orders": decimal_number(finance.required_daily_orders),
                    "unknown_cost_fields": finance.unknown_cost_fields,
                },
                "missing_fields": [],
                "risks": risk_values,
                "counterfactuals": counterfactuals(gate.minimum_required_reduction_krw),
                "next_actions": next_actions(gate.status),
            }
        advisory = self._agent_advisory(agent_proposal)
        if advisory is not None:
            candidate["agent_advisory"] = advisory
        return _CandidateDraft(
            candidate=candidate,
            decision_input=CandidateDecisionInput(
                candidate_id=candidate_id,
                case_type=CaseType.INDEPENDENT,
                finance=finance,
                capital_gate=gate,
                founder_fit=FounderFitStatus.PASS,
                founder_burden=FounderBurdenLevel.MEDIUM,
                conditional_reason_codes=conditional_reason_codes,
                risks=[
                    RiskSignal(
                        risk_id=str(risk["risk_id"]),
                        severity=RiskSeverity(str(risk["severity"])),
                    )
                    for risk in risk_values
                ],
            ),
        )

    @staticmethod
    def _agent_advisory(proposal: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(proposal, dict):
            return None
        fit_assessments = proposal.get("fit_assessments")
        if not isinstance(fit_assessments, list) or len(fit_assessments) != 5:
            return None
        return {
            "fit_assessments": deepcopy(fit_assessments),
            "adjusted_parameters": deepcopy(proposal.get("adjusted_parameters", [])),
            "missing_fields": deepcopy(proposal.get("missing_fields", [])),
            "warnings": deepcopy(proposal.get("warnings", [])),
        }

    def _franchise_drafts(
        self,
        *,
        state: VentureState,
        evidence_records: list[dict[str, Any]],
        property_context: PropertyContext | None,
        finance_resolver: FinancialInputResolver,
        proposed_ids: set[str] | None,
        franchise_universe: list[dict[str, Any]] | None,
    ) -> list[_CandidateDraft]:
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
        drafts: list[_CandidateDraft] = []
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
                value
                for value in brand.get("evidence_refs", [])
                if isinstance(value, str)
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
                    MONTHLY_FIXED_COST_CATEGORIES,
                    key=lambda item: item.value,
                )
            ]
            finance = calculate_finance(
                FinanceInput(
                    initial_cost_lines=initial_cost_lines,
                    monthly_fixed_cost_lines=monthly_fixed_cost_lines,
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
            drafts.append(
                _CandidateDraft(
                    candidate={
                        "schema_version": "2.0.0",
                        "candidate_id": build_candidate_id(
                            project_id=state.project_id,
                            case_type="FRANCHISE",
                            source_id=brand_id,
                        ),
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
                        "gate_results": [gate.trace.model_dump(mode="json")] if gate.trace else [],
                        "decision_inputs": project_finance_decision_inputs(
                            initial_cost_lines=initial_cost_lines,
                            monthly_fixed_cost_lines=monthly_fixed_cost_lines,
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
                            "break_even_monthly_sales_krw": (finance.break_even_monthly_sales_krw),
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
                        candidate_id=build_candidate_id(
                            project_id=state.project_id,
                            case_type="FRANCHISE",
                            source_id=brand_id,
                        ),
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
