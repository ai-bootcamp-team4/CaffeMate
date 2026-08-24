"""사용자는 검증 게이트를 기다리지 않고 근거와 가정을 구분한 후보를 비교한다."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.candidates.seed_registry import (
    IndependentSeedDefinition,
    IndependentSeedRegistry,
)
from app.domain.models import CafeTypePreference, VentureState
from app.finance.calculator import calculate_finance, evaluate_capital_gate
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
from app.results.models import (
    AuditStatus,
    ResultBundlePayload,
    ResultOutcomeStatus,
)
from app.results.projection import project_evidence_for_candidate

_EDIYA_BRAND_ID = "kr-ediya-coffee"
_EDIYA_OFFICIAL_COST_URL = "https://www.ediya.com/C/contents/franchise_02.html"
_EDIYA_OFFICIAL_INTERIOR_URL = "https://www.ediya.com/C/contents/interior.html"
_EDIYA_OFFICIAL_COSTS = {
    CostCategory.FRANCHISE_INITIAL_FEES: MoneyRange(
        low=19_000_000,
        base=19_000_000,
        high=19_000_000,
    ),
    CostCategory.OPENING_INVENTORY: MoneyRange(
        low=8_000_000,
        base=8_000_000,
        high=8_000_000,
    ),
    CostCategory.CONSTRUCTION: MoneyRange(
        low=54_900_000,
        base=54_900_000,
        high=54_900_000,
    ),
    CostCategory.EQUIPMENT: MoneyRange(
        low=38_421_000,
        base=38_421_000,
        high=38_421_000,
    ),
}
_EDIYA_ASSUMPTION_COSTS = {
    CostCategory.DEPOSIT: MoneyRange(low=30_000_000, base=40_000_000, high=60_000_000),
    CostCategory.ACQUISITION_OR_PREMIUM: MoneyRange(
        low=0,
        base=10_000_000,
        high=30_000_000,
    ),
    CostCategory.PREOPENING: MoneyRange(low=3_000_000, base=5_000_000, high=8_000_000),
    CostCategory.CONTINGENCY: MoneyRange(low=10_000_000, base=15_000_000, high=20_000_000),
    CostCategory.OPERATING_RESERVE: MoneyRange(
        low=20_000_000,
        base=30_000_000,
        high=40_000_000,
    ),
    CostCategory.MONTHLY_OCCUPANCY: MoneyRange(
        low=3_000_000,
        base=4_500_000,
        high=7_000_000,
    ),
    CostCategory.MONTHLY_LABOR: MoneyRange(
        low=6_000_000,
        base=8_000_000,
        high=12_000_000,
    ),
    CostCategory.MONTHLY_OTHER_FIXED: MoneyRange(
        low=1_500_000,
        base=2_200_000,
        high=3_500_000,
    ),
}


@dataclass(frozen=True)
class _CandidateDraft:
    candidate: dict[str, Any]
    gate_status: CapitalGateStatus
    initial_cash_base: int


@dataclass(frozen=True)
class PropertyCostOverride:
    """선택한 후보의 실제 임차 조건만 참고 범위 대신 계산에 사용한다."""

    property_input_id: str
    source_id: str
    deposit_krw: int
    monthly_rent_krw: int
    management_fee_krw: int
    key_money_krw: int | None

    @property
    def evidence_ref(self) -> str:
        return f"property-input:{self.property_input_id}"


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
        property_cost_override: PropertyCostOverride | None = None,
    ) -> ResultBundlePayload:
        drafts: list[_CandidateDraft] = []
        preference = state.founder.cafe_type_preference
        if preference in {
            CafeTypePreference.OPEN_TO_BOTH,
            CafeTypePreference.INDEPENDENT_ONLY,
        }:
            drafts.extend(
                self._independent_draft(
                    state=state,
                    seed=seed,
                    evidence_records=evidence_records,
                    property_cost_override=property_cost_override,
                )
                for seed in self._seeds.select(state.founder)
            )
        if preference in {
            CafeTypePreference.OPEN_TO_BOTH,
            CafeTypePreference.FRANCHISE_ONLY,
        }:
            drafts.extend(
                self._franchise_drafts(
                    state=state,
                    evidence_records=evidence_records,
                    property_cost_override=property_cost_override,
                )
            )
        if not drafts:
            raise ValueError("No registered candidate is compatible with the founder state")

        reviewable = [draft for draft in drafts if draft.gate_status != CapitalGateStatus.FAIL]
        selected = reviewable if reviewable else drafts
        selected.sort(
            key=lambda draft: (
                self._gate_order(draft.gate_status),
                draft.initial_cash_base,
                str(draft.candidate["candidate_id"]),
            )
        )
        selected = selected[:3]
        no_reviewable = not reviewable
        for index, draft in enumerate(selected, start=1):
            candidate = draft.candidate
            candidate["rank"] = None if no_reviewable else index
            candidate["is_primary_next_review"] = not no_reviewable and index == 1
            if no_reviewable:
                candidate["rank_basis"] = "NOT_RANKED"
            elif candidate["review_status"] == "REVIEW_RECOMMENDED":
                candidate["rank_basis"] = "ECONOMIC_AND_FOUNDER_FIT"
            else:
                candidate["rank_basis"] = "NEXT_REVIEW_PRIORITY"

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

    def _independent_draft(
        self,
        *,
        state: VentureState,
        seed: IndependentSeedDefinition,
        evidence_records: list[dict[str, Any]],
        property_cost_override: PropertyCostOverride | None,
    ) -> _CandidateDraft:
        profile = seed.finance_profile
        if profile is None:
            raise ValueError(f"Registered seed has no finance profile: {seed.model_id}")
        assumption_refs = sorted(set(seed.support_refs))
        initial_cost_lines = [
            self._cost_line(
                seed.model_id,
                category,
                profile.cost_ranges[category],
                property_cost_override,
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
            self._cost_line(
                seed.model_id,
                category,
                profile.cost_ranges[category],
                property_cost_override,
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
        candidate_id = self._candidate_id(
            project_id=state.project_id,
            case_type="INDEPENDENT",
            source_id=seed.model_id,
        )
        review_status = self._review_status(gate.status)
        reason_codes = [gate.reason_code]
        if gate.status == CapitalGateStatus.PASS and not evidence_refs:
            review_status = "CONDITIONAL_REVIEW"
            reason_codes.append("EVIDENCE_CONFIRMATION_REQUIRED")
        return _CandidateDraft(
            candidate={
                "schema_version": "2.0.0",
                "candidate_id": candidate_id,
                "project_id": state.project_id,
                "state_version": state.state_version,
                "case_type": "INDEPENDENT",
                "display_name": seed.display_name,
                "review_status": review_status,
                "reason_codes": sorted(reason_codes),
                "summary": self._summary(gate.status),
                "rank": 1,
                "rank_basis": "NEXT_REVIEW_PRIORITY",
                "is_primary_next_review": True,
                "franchise": None,
                "independent_model": {
                    "model_id": seed.model_id,
                    "adjusted_fields": self._property_adjusted_fields(
                        seed.model_id,
                        property_cost_override,
                    ),
                },
                "evidence_refs": evidence_refs,
                "assumption_refs": assumption_refs,
                "market_signals": signals,
                "official_documents": documents,
                "official_document_gaps": document_gaps,
                "financial_summary": {
                    "initial_cash": self._money(
                        finance.initial_cash,
                        self._cost_refs(initial_cost_lines),
                    ),
                    "monthly_fixed_cost": self._money(
                        finance.monthly_fixed_cost,
                        self._cost_refs(monthly_fixed_cost_lines),
                    ),
                    "break_even_monthly_sales_krw": finance.break_even_monthly_sales_krw,
                    "required_daily_orders": self._decimal(finance.required_daily_orders),
                    "unknown_cost_fields": finance.unknown_cost_fields,
                },
                "missing_fields": [],
                "risks": self._capital_risks(gate.status, evidence_refs),
                "counterfactuals": self._counterfactuals(gate.minimum_required_reduction_krw),
                "next_actions": self._next_actions(gate.status),
            },
            gate_status=gate.status,
            initial_cash_base=int(finance.initial_cash.base or 0),
        )

    def _franchise_drafts(
        self,
        *,
        state: VentureState,
        evidence_records: list[dict[str, Any]],
        property_cost_override: PropertyCostOverride | None,
    ) -> list[_CandidateDraft]:
        evidence_refs, signals, documents, document_gaps = project_evidence_for_candidate(
            evidence_records,
            project_id=state.project_id,
            case_type="FRANCHISE",
        )
        brands = (
            (
                _EDIYA_BRAND_ID,
                "이디야커피",
                "https://www.ediya.com/C/contents/franchise_01.html",
            ),
        )
        drafts: list[_CandidateDraft] = []
        for brand_id, name, source_ref in brands:
            assumption_ref = f"declared-assumption:{brand_id}:2026-08-23"
            initial_cost_lines = [
                self._franchise_cost_line(
                    brand_id=brand_id,
                    category=category,
                    property_cost_override=property_cost_override,
                )
                for category in sorted(INITIAL_COST_CATEGORIES, key=lambda item: item.value)
            ]
            monthly_fixed_cost_lines = [
                self._franchise_cost_line(
                    brand_id=brand_id,
                    category=category,
                    property_cost_override=property_cost_override,
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
                    operating_days_per_month=30,
                    average_ticket_krw=5_500,
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
                        "candidate_id": self._candidate_id(
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
                        "reason_codes": sorted(
                            {
                                gate.reason_code,
                                "FRANCHISE_AREA_AVAILABILITY_UNCONFIRMED",
                                "FRANCHISE_DISCLOSURE_MISSING",
                            }
                        ),
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
                            "eligibility_evidence_refs": [source_ref],
                            "disclosure_evidence_refs": [],
                        },
                        "independent_model": None,
                        "evidence_refs": evidence_refs,
                        "assumption_refs": [assumption_ref],
                        "market_signals": signals,
                        "official_documents": documents,
                        "official_document_gaps": sorted(
                            set(document_gaps) | {"정보공개서 공식 문서"}
                        ),
                        "financial_summary": {
                            "initial_cash": self._money(
                                finance.initial_cash,
                                self._cost_refs(initial_cost_lines),
                            ),
                            "monthly_fixed_cost": self._money(
                                finance.monthly_fixed_cost,
                                self._cost_refs(monthly_fixed_cost_lines),
                            ),
                            "break_even_monthly_sales_krw": (finance.break_even_monthly_sales_krw),
                            "required_daily_orders": self._decimal(finance.required_daily_orders),
                            "unknown_cost_fields": finance.unknown_cost_fields,
                        },
                        "missing_fields": [
                            {
                                "field": "지역 출점 가능 여부",
                                "impact": "선택 동네에서 실제 출점 가능한지 확정할 수 없습니다.",
                                "next_check": "가맹 본사에 후보 지역 출점 가능 여부를 확인합니다.",
                            },
                            {
                                "field": "정보공개서",
                                "impact": "가맹 조건과 비용의 완전성을 확정할 수 없습니다.",
                                "next_check": "최신 정보공개서를 확보해 조건을 다시 계산합니다.",
                            },
                        ],
                        "risks": [
                            {
                                "risk_id": "FRANCHISE_CONDITIONS_INCOMPLETE",
                                "severity": "HIGH",
                                "summary": "출점 승인과 최신 정보공개서 확인이 필요합니다.",
                                "evidence_refs": evidence_refs,
                            }
                        ],
                        "counterfactuals": self._counterfactuals(
                            gate.minimum_required_reduction_krw
                        ),
                        "next_actions": [
                            "최신 정보공개서를 확보합니다.",
                            "본사에 후보 지역 출점 가능 여부를 확인합니다.",
                            "실제 점포 임대 조건으로 비용을 다시 계산합니다.",
                        ],
                    },
                    gate_status=gate.status,
                    initial_cash_base=int(finance.initial_cash.base or 0),
                )
            )
        return drafts

    @staticmethod
    def _franchise_cost_line(
        *,
        brand_id: str,
        category: CostCategory,
        property_cost_override: PropertyCostOverride | None,
    ) -> CostLine:
        if property_cost_override is not None and property_cost_override.source_id == brand_id:
            actual_value = SimpleProposalBuilder._property_cost_value(
                category,
                property_cost_override,
            )
            if actual_value is not None:
                return CostLine(
                    field_id=category.value,
                    category=category,
                    amount=MoneyRange(
                        low=actual_value,
                        base=actual_value,
                        high=actual_value,
                    ),
                    provenance=ValueProvenance.USER_INPUT,
                    evidence_ref=property_cost_override.evidence_ref,
                )
        official = _EDIYA_OFFICIAL_COSTS.get(category)
        if official is not None:
            source_ref = (
                _EDIYA_OFFICIAL_INTERIOR_URL
                if category in {CostCategory.CONSTRUCTION, CostCategory.EQUIPMENT}
                else _EDIYA_OFFICIAL_COST_URL
            )
            return CostLine(
                field_id=category.value,
                category=category,
                amount=official,
                provenance=ValueProvenance.FACT,
                evidence_ref=source_ref,
            )
        assumption = _EDIYA_ASSUMPTION_COSTS.get(category)
        if assumption is None:
            raise ValueError(f"Registered franchise cost is missing: {category.value}")
        return CostLine(
            field_id=category.value,
            category=category,
            amount=assumption,
            provenance=ValueProvenance.ASSUMPTION,
            evidence_ref=f"declared-assumption:{brand_id}:2026-08-23",
        )

    @staticmethod
    def _cost_line(
        model_id: str,
        category: CostCategory,
        amount: MoneyRange,
        property_cost_override: PropertyCostOverride | None,
    ) -> CostLine:
        if property_cost_override is not None and property_cost_override.source_id == model_id:
            actual_value = SimpleProposalBuilder._property_cost_value(
                category,
                property_cost_override,
            )
            if actual_value is not None:
                return CostLine(
                    field_id=category.value,
                    category=category,
                    amount=MoneyRange(
                        low=actual_value,
                        base=actual_value,
                        high=actual_value,
                    ),
                    provenance=ValueProvenance.USER_INPUT,
                    evidence_ref=property_cost_override.evidence_ref,
                )
        return CostLine(
            field_id=category.value,
            category=category,
            amount=amount,
            provenance=ValueProvenance.ASSUMPTION,
            evidence_ref=f"declared-assumption:{model_id}",
        )

    @staticmethod
    def _property_cost_value(
        category: CostCategory,
        value: PropertyCostOverride,
    ) -> int | None:
        if category == CostCategory.DEPOSIT:
            return value.deposit_krw
        if category == CostCategory.ACQUISITION_OR_PREMIUM:
            return value.key_money_krw
        if category == CostCategory.MONTHLY_OCCUPANCY:
            return value.monthly_rent_krw + value.management_fee_krw
        return None

    @staticmethod
    def _cost_refs(lines: list[CostLine]) -> list[str]:
        return sorted(
            {
                line.evidence_ref
                for line in lines
                if isinstance(line.evidence_ref, str) and line.evidence_ref
            }
        )

    @staticmethod
    def _property_adjusted_fields(
        model_id: str,
        value: PropertyCostOverride | None,
    ) -> list[str]:
        if value is None or value.source_id != model_id:
            return []
        fields = [
            "property.deposit_krw",
            "property.management_fee_krw",
            "property.monthly_rent_krw",
        ]
        if value.key_money_krw is not None:
            fields.append("property.key_money_krw")
        return fields

    @staticmethod
    def _money(value: MoneyRange, refs: list[str]) -> dict[str, Any]:
        return {
            "currency": "KRW",
            "low": value.low,
            "base": value.base,
            "high": value.high,
            "provenance_refs": refs if value.base is not None else [],
        }

    @staticmethod
    def _decimal(value: Decimal | None) -> float | None:
        return float(value) if value is not None else None

    @staticmethod
    def _review_status(status: CapitalGateStatus) -> str:
        return {
            CapitalGateStatus.PASS: "REVIEW_RECOMMENDED",
            CapitalGateStatus.CONDITIONAL: "CONDITIONAL_REVIEW",
            CapitalGateStatus.FAIL: "EXCLUDED",
        }[status]

    @staticmethod
    def _summary(status: CapitalGateStatus) -> str:
        return {
            CapitalGateStatus.PASS: "현재 자금 범위에서 다음 검토 가치가 있는 창업안입니다.",
            CapitalGateStatus.CONDITIONAL: (
                "자금 조달 또는 실제 점포 비용을 확인하면서 검토할 창업안입니다."
            ),
            CapitalGateStatus.FAIL: "현재 확인된 자금 조건으로는 진행하기 어려운 창업안입니다.",
        }[status]

    @staticmethod
    def _capital_risks(
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

    @staticmethod
    def _counterfactuals(reduction: int | None) -> list[dict[str, str]]:
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

    @staticmethod
    def _next_actions(status: CapitalGateStatus) -> list[str]:
        if status == CapitalGateStatus.FAIL:
            return [
                "예산에 가까운 작은 운영안을 비교합니다.",
                "추가 자금 조건을 입력합니다.",
            ]
        return [
            "후보를 선택하고 실제 점포 조건을 입력합니다.",
            "보증금·월세·권리금과 견적을 확인합니다.",
        ]

    @staticmethod
    def _gate_order(status: CapitalGateStatus) -> int:
        return {
            CapitalGateStatus.PASS: 0,
            CapitalGateStatus.CONDITIONAL: 1,
            CapitalGateStatus.FAIL: 2,
        }[status]

    @staticmethod
    def _candidate_id(*, project_id: str, case_type: str, source_id: str) -> str:
        """같은 창업안은 State가 바뀌어도 선택과 실제 점포 입력을 이어받는다."""

        return f"{project_id}:{case_type}:{source_id}"
