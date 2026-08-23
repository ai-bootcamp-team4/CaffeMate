import hashlib
from typing import Any

import rfc8785

from app.candidates.models import (
    CandidateDecisionInput,
    FounderBurdenLevel,
    FounderFitStatus,
    FranchiseAvailability,
    RiskSeverity,
    RiskSignal,
)
from app.candidates.ranking import rank_candidates
from app.candidates.seed_registry import IndependentFinanceProfile, IndependentSeedRegistry
from app.domain.errors import ContractValidationError
from app.domain.models import CaseType, FranchiseEligibility, OperationMode
from app.finance.calculator import calculate_finance, evaluate_capital_gate
from app.finance.models import (
    INITIAL_COST_CATEGORIES,
    MONTHLY_FIXED_COST_CATEGORIES,
    CapitalGateInput,
    CapitalGateResult,
    CostCategory,
    CostLine,
    FinanceInput,
    MoneyRange,
    ValueProvenance,
)
from app.workflows.models import StageControl
from app.workflows.stage_context import StageContext

_EDIYA_BRAND_ID = "kr-ediya-coffee"
_EDIYA_OFFICIAL_COST_URL = "https://www.ediya.com/C/contents/franchise_02.html"
_EDIYA_OFFICIAL_INTERIOR_URL = "https://www.ediya.com/C/contents/interior.html"

# Official 20-pyeong figures are kept separate from provisional operating assumptions.
# Actual property terms and user documents continue to override both through the normal
# Evidence priority rules.
_EDIYA_OFFICIAL_COSTS: dict[CostCategory, MoneyRange] = {
    CostCategory.FRANCHISE_INITIAL_FEES: MoneyRange(
        low=19_000_000, base=19_000_000, high=19_000_000
    ),
    CostCategory.OPENING_INVENTORY: MoneyRange(low=8_000_000, base=8_000_000, high=8_000_000),
    CostCategory.CONSTRUCTION: MoneyRange(low=54_900_000, base=54_900_000, high=54_900_000),
    CostCategory.EQUIPMENT: MoneyRange(low=38_421_000, base=38_421_000, high=38_421_000),
}

_EDIYA_PROVISIONAL_COSTS: dict[CostCategory, MoneyRange] = {
    CostCategory.DEPOSIT: MoneyRange(low=30_000_000, base=40_000_000, high=60_000_000),
    CostCategory.ACQUISITION_OR_PREMIUM: MoneyRange(low=0, base=10_000_000, high=30_000_000),
    CostCategory.PREOPENING: MoneyRange(low=3_000_000, base=5_000_000, high=8_000_000),
    CostCategory.CONTINGENCY: MoneyRange(low=10_000_000, base=15_000_000, high=20_000_000),
    CostCategory.OPERATING_RESERVE: MoneyRange(low=20_000_000, base=30_000_000, high=40_000_000),
    CostCategory.MONTHLY_OCCUPANCY: MoneyRange(low=3_000_000, base=4_500_000, high=7_000_000),
    CostCategory.MONTHLY_LABOR: MoneyRange(low=6_000_000, base=8_000_000, high=12_000_000),
    CostCategory.MONTHLY_OTHER_FIXED: MoneyRange(low=1_500_000, base=2_200_000, high=3_500_000),
}

_EDIYA_PROVISIONAL_SCALARS = {
    "CONTRIBUTION_MARGIN_BPS": (6_500, "basis_point"),
    "OPERATING_DAYS_PER_MONTH": (30, "day/month"),
    "AVERAGE_TICKET_KRW": (5_500, "KRW/order"),
}


class CalculateGateRankStageHandler:
    def __init__(self, seed_registry: IndependentSeedRegistry | None = None) -> None:
        self._seed_registry = seed_registry

    def execute(self, context: StageContext) -> dict[str, object]:
        calculated: list[dict[str, Any]] = []
        decision_inputs: list[CandidateDecisionInput] = []
        evidence_by_id: dict[str, dict[str, Any]] = {}
        for output_key, dependency_code, source_key, case_type in (
            (
                "independent_proposal",
                "PROPOSE_INDEPENDENT",
                "model_seeds",
                CaseType.INDEPENDENT,
            ),
            (
                "franchise_proposal",
                "PROPOSE_FRANCHISE",
                "franchise_universe",
                CaseType.FRANCHISE,
            ),
        ):
            branch = self._branch(context, dependency_code, output_key)
            if branch is None:
                continue
            proposal_input = branch.get("proposal_input")
            proposals = branch.get("candidate_proposals")
            if not isinstance(proposal_input, dict) or not isinstance(proposals, list):
                raise ContractValidationError("Proposal branch output is invalid")
            sources = self._sources(proposal_input, source_key)
            base_evidence_records = self._evidence_records(proposal_input, context.project_id)
            for evidence in base_evidence_records:
                evidence_id = evidence.get("evidence_id")
                if not isinstance(evidence_id, str):
                    raise ContractValidationError("Proposal Evidence id is invalid")
                previous = evidence_by_id.get(evidence_id)
                if previous is not None and previous != evidence:
                    raise ContractValidationError("Proposal Evidence id has conflicting records")
                evidence_by_id[evidence_id] = evidence
            for proposal in proposals:
                if not isinstance(proposal, dict):
                    raise ContractValidationError("Candidate proposal is invalid")
                proposal_id = proposal.get("proposal_id")
                if not isinstance(proposal_id, str):
                    raise ContractValidationError("Candidate proposal id is invalid")
                source = sources.get(proposal_id)
                if source is None:
                    raise ContractValidationError("Candidate proposal source is missing")
                source_id = proposal.get("seed_or_brand_id")
                if not isinstance(source_id, str):
                    raise ContractValidationError("Candidate proposal source id is invalid")
                evidence_records = [
                    *base_evidence_records,
                    *self._document_evidence(
                        context=context,
                        case_type=case_type,
                        source_id=source_id,
                    ),
                    *self._seed_assumption_evidence(
                        context=context,
                        case_type=case_type,
                        source_id=source_id,
                    ),
                    *self._franchise_baseline_evidence(
                        context=context,
                        case_type=case_type,
                        source_id=source_id,
                    ),
                ]
                for evidence in evidence_records:
                    evidence_id = evidence.get("evidence_id")
                    if not isinstance(evidence_id, str):
                        raise ContractValidationError("Document Evidence id is invalid")
                    previous = evidence_by_id.get(evidence_id)
                    if previous is not None and previous != evidence:
                        raise ContractValidationError(
                            "Document Evidence id has conflicting records"
                        )
                    evidence_by_id[evidence_id] = evidence
                candidate, decision_input = self._calculate_candidate(
                    context=context,
                    case_type=case_type,
                    proposal=proposal,
                    source=source,
                    evidence_records=evidence_records,
                )
                calculated.append(candidate)
                decision_inputs.append(decision_input)

        decisions = rank_candidates(decision_inputs)
        decisions_by_id = {value.candidate_id: value for value in decisions}
        for candidate in calculated:
            decision = decisions_by_id[candidate["candidate_id"]]
            candidate["decision"] = decision.model_dump(mode="json")
        calculated.sort(
            key=lambda value: (
                value["decision"]["rank"] is None,
                value["decision"]["rank"] or 2**31,
                value["candidate_id"],
            )
        )
        primary = next(
            (value.candidate_id for value in decisions if value.is_primary_next_review),
            None,
        )
        reason_codes = [] if calculated else ["NO_PROPOSALS_TO_CALCULATE"]
        return {
            "stage_control": StageControl(reason_codes=reason_codes).model_dump(mode="json"),
            "calculate_gate_rank": {
                "candidates": calculated,
                "ranked_decisions": [value.model_dump(mode="json") for value in decisions],
                "primary_candidate_id": primary,
                "excluded_candidate_ids": sorted(
                    value.candidate_id for value in decisions if value.rank is None
                ),
                "evidence_records": [
                    evidence_by_id[evidence_id] for evidence_id in sorted(evidence_by_id)
                ],
                "reason_codes": reason_codes,
            },
        }

    @staticmethod
    def _branch(
        context: StageContext,
        dependency_code: str,
        output_key: str,
    ) -> dict[str, Any] | None:
        dependency = context.dependency_results.get(dependency_code)
        if dependency is None:
            return None
        value = dependency.get(output_key)
        if not isinstance(value, dict):
            raise ContractValidationError(f"{dependency_code} result is missing")
        return value

    @staticmethod
    def _sources(
        proposal_input: dict[str, Any],
        source_key: str,
    ) -> dict[str, dict[str, Any]]:
        values = proposal_input.get(source_key)
        if not isinstance(values, list):
            raise ContractValidationError("Proposal source collection is invalid")
        sources: dict[str, dict[str, Any]] = {}
        for value in values:
            if not isinstance(value, dict) or not isinstance(value.get("proposal_id"), str):
                raise ContractValidationError("Proposal source is invalid")
            if value["proposal_id"] in sources:
                raise ContractValidationError("Proposal source id is duplicated")
            sources[value["proposal_id"]] = value
        return sources

    @staticmethod
    def _evidence_records(
        proposal_input: dict[str, Any],
        project_id: str,
    ) -> list[dict[str, Any]]:
        values = proposal_input.get("evidence_records")
        if not isinstance(values, list):
            raise ContractValidationError("Proposal Evidence collection is invalid")
        records: list[dict[str, Any]] = []
        for value in values:
            if not isinstance(value, dict) or value.get("project_id") != project_id:
                raise ContractValidationError("Proposal Evidence crossed project scope")
            records.append(value)
        return records

    @staticmethod
    def _franchise_baseline_evidence(
        *, context: StageContext, case_type: CaseType, source_id: str
    ) -> list[dict[str, Any]]:
        if case_type != CaseType.FRANCHISE or source_id != _EDIYA_BRAND_ID:
            return []

        timestamp = context.state.updated_at.isoformat().replace("+00:00", "Z")
        values: list[
            tuple[
                str,
                dict[str, Any],
                str | None,
                str,
                str,
                str,
                list[str],
            ]
        ] = []
        for category, amount in _EDIYA_OFFICIAL_COSTS.items():
            if category in {
                CostCategory.CONSTRUCTION,
                CostCategory.EQUIPMENT,
            }:
                source_ref = _EDIYA_OFFICIAL_INTERIOR_URL
                title = "이디야커피 20평 인테리어·기기 공식 기준"
            else:
                source_ref = _EDIYA_OFFICIAL_COST_URL
                title = "이디야커피 가맹 개설 공식 비용"
            values.append(
                (
                    f"FRANCHISE_COST_{category.value}",
                    {
                        "kind": "MONEY_RANGE",
                        "currency": "KRW",
                        **amount.model_dump(mode="json"),
                    },
                    "KRW",
                    "EVIDENCED_FACT",
                    title,
                    source_ref,
                    [
                        "20평 기준이며 부가가치세와 임대차 비용은 별도",
                        "실제 출점 조건과 견적으로 교체 필요",
                    ],
                )
            )
        for category, amount in _EDIYA_PROVISIONAL_COSTS.items():
            values.append(
                (
                    f"FRANCHISE_COST_{category.value}",
                    {
                        "kind": "MONEY_RANGE",
                        "currency": "KRW",
                        **amount.model_dump(mode="json"),
                    },
                    "KRW",
                    "DECLARED_ASSUMPTION",
                    "이디야 20평 데모 계산용 임시 범위",
                    f"seed://{source_id}/FRANCHISE_COST_{category.value}",
                    ["실제 점포 매물 또는 견적 입력 전 사용하는 임시 계산 범위"],
                )
            )
        for claim_type, (scalar_value, scalar_unit) in _EDIYA_PROVISIONAL_SCALARS.items():
            values.append(
                (
                    f"FRANCHISE_{claim_type}",
                    {"kind": "INTEGER", "value": scalar_value},
                    scalar_unit,
                    "DECLARED_ASSUMPTION",
                    "이디야 20평 데모 계산용 임시 운영값",
                    f"seed://{source_id}/FRANCHISE_{claim_type}",
                    ["실제 점포 운영계획과 매출 자료 입력 전 사용하는 임시 계산값"],
                )
            )

        records: list[dict[str, Any]] = []
        for (
            claim_type,
            record_value,
            record_unit,
            value_kind,
            title,
            source_ref,
            missing_context,
        ) in values:
            digest = hashlib.sha256(
                rfc8785.dumps(
                    {
                        "source_id": source_id,
                        "claim_type": claim_type,
                        "value": record_value,
                        "source_ref": source_ref,
                    }
                )
            ).hexdigest()
            official = value_kind == "EVIDENCED_FACT"
            records.append(
                {
                    "schema_version": "2.0.0",
                    "evidence_id": f"franchise-baseline-{digest[:40]}",
                    "project_id": context.project_id,
                    "claim_type": claim_type,
                    "value": record_value,
                    "value_kind": value_kind,
                    "unit": record_unit,
                    "geographic_scope": {
                        "scope_type": "CASE",
                        "scope_id": source_id,
                        "boundary_version": None,
                    },
                    "source": {
                        "title": title,
                        "source_ref": source_ref,
                        "authority": "COMPANY_OFFICIAL" if official else "SECONDARY",
                        "source_type": "WEB" if official else "DATASET",
                        "published_or_data_date": None,
                        "source_observed_at": timestamp if official else None,
                        "document_version": "ediya-20p-demo-v1",
                        "checksum": digest,
                    },
                    "original_anchor": {
                        "anchor_type": "SECTION" if official else "CALCULATION",
                        "locator": claim_type,
                        "excerpt_hash": None,
                    },
                    "freshness_status": "NOT_APPLICABLE",
                    "conflict_status": "NONE",
                    "retrieved_at": timestamp,
                    "missing_context": missing_context,
                    "durable_evidence_refs": [],
                }
            )
        return records

    @staticmethod
    def _document_evidence(
        *, context: StageContext, case_type: CaseType, source_id: str
    ) -> list[dict[str, Any]]:
        category_map = {
            "LEASE_DEPOSIT": CostCategory.DEPOSIT.value,
            "KEY_MONEY": CostCategory.ACQUISITION_OR_PREMIUM.value,
            "MONTHLY_RENT": CostCategory.MONTHLY_OCCUPANCY.value,
            "MANAGEMENT_FEE": CostCategory.MONTHLY_OCCUPANCY.value,
            "FRANCHISE_FEE": CostCategory.FRANCHISE_INITIAL_FEES.value,
            "EDUCATION_FEE": CostCategory.FRANCHISE_INITIAL_FEES.value,
        }
        grouped: dict[str, list[dict[str, Any]]] = {}
        for claim in context.document_claims:
            if (
                claim.get("case_type") != case_type.value
                or claim.get("source_id") != source_id
                or not isinstance(claim.get("value_json"), int)
            ):
                continue
            claim_type = claim.get("claim_type")
            category = category_map.get(str(claim_type))
            if claim_type == "QUOTE_TOTAL":
                if claim.get("document_type") == "INTERIOR_QUOTE":
                    category = CostCategory.CONSTRUCTION.value
                elif claim.get("document_type") == "EQUIPMENT_QUOTE":
                    category = CostCategory.EQUIPMENT.value
            if category is not None:
                if claim.get("has_open_conflict") is True:
                    grouped.setdefault(f"CONFLICT:{category}", []).append(claim)
                    continue
                grouped.setdefault(category, []).append(claim)
        records: list[dict[str, Any]] = []
        for category, claims in sorted(grouped.items()):
            claim_ids = sorted(str(claim["claim_id"]) for claim in claims)
            digest = hashlib.sha256(rfc8785.dumps(claim_ids)).hexdigest()
            timestamp = context.state.updated_at.isoformat().replace("+00:00", "Z")
            observed_values = [
                value
                for value in (
                    CalculateGateRankStageHandler._iso_timestamp(
                        claim.get("observed_at")
                    )
                    for claim in claims
                )
                if value is not None
            ]
            source_observed_at = max(observed_values, default=timestamp)
            document_versions = sorted(
                {
                    str(claim["document_revision_id"])
                    for claim in claims
                    if claim.get("document_revision_id") is not None
                }
            )
            is_property_input = all(
                claim.get("input_kind") == "USER_CONFIRMED_PROPERTY_TERMS"
                for claim in claims
            )
            source_type = "USER_FIELD" if is_property_input else "USER_DOCUMENT"
            title = (
                "사용자가 확인한 점포 조건"
                if is_property_input
                else "사용자가 확인한 창업 문서"
            )
            source_ref = (
                f"user-field://{source_id}/{digest}"
                if is_property_input
                else f"user-document://{digest}"
            )
            document_version = (
                document_versions[0]
                if len(document_versions) == 1
                else f"compound:{digest}"
                if document_versions
                else None
            )
            common = {
                "schema_version": "2.0.0",
                "project_id": context.project_id,
                "unit": "KRW",
                "geographic_scope": {
                    "scope_type": "CASE",
                    "scope_id": source_id,
                    "boundary_version": None,
                },
                "source": {
                    "title": title,
                    "source_ref": source_ref,
                    "authority": "USER_ARTIFACT",
                    "source_type": source_type,
                    "published_or_data_date": None,
                    "source_observed_at": source_observed_at,
                    "document_version": document_version,
                    "checksum": digest,
                },
                "original_anchor": {
                    "anchor_type": "USER_FIELD",
                    "locator": "claims:" + ",".join(claim_ids),
                    "excerpt_hash": None,
                },
                "retrieved_at": timestamp,
                "durable_evidence_refs": [],
            }
            if category.startswith("CONFLICT:"):
                actual_category = category.removeprefix("CONFLICT:")
                records.append(
                    {
                        **common,
                        "evidence_id": f"document-conflict-{digest[:32]}",
                        "claim_type": f"DOCUMENT_CONFLICT_COST_{actual_category}",
                        "value": {"kind": "NULL", "value": None},
                        "value_kind": "UNKNOWN",
                        "freshness_status": "UNKNOWN",
                        "conflict_status": "CONFIRMED",
                        "missing_context": [
                            "상충하는 사용자 확인 비용 입력이 있어 값을 확정할 수 없습니다."
                        ],
                    }
                )
                continue
            amount = sum(int(claim["value_json"]) for claim in claims)
            records.append(
                {
                    **common,
                    "evidence_id": f"document-evidence-{digest[:32]}",
                    "claim_type": f"COST_{category}",
                    "value": {
                        "kind": "MONEY_RANGE",
                        "currency": "KRW",
                        "low": amount,
                        "base": amount,
                        "high": amount,
                    },
                    "value_kind": "USER_CONFIRMED_FACT",
                    "freshness_status": "NOT_APPLICABLE",
                    "conflict_status": "NONE",
                    "missing_context": [],
                }
            )
        return records

    @staticmethod
    def _iso_timestamp(value: Any) -> str | None:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return str(value.isoformat()).replace("+00:00", "Z")
        if isinstance(value, str) and value:
            return value.replace("+00:00", "Z")
        return None

    def _seed_assumption_evidence(
        self,
        *,
        context: StageContext,
        case_type: CaseType,
        source_id: str,
    ) -> list[dict[str, Any]]:
        profile = self._seed_finance_profile(case_type, source_id)
        if profile is None:
            return []

        values: list[tuple[str, dict[str, Any], str | None]] = []
        for category, amount in sorted(profile.cost_ranges.items(), key=lambda item: item[0].value):
            values.append(
                (
                    f"INDEPENDENT_COST_{category.value}",
                    {
                        "kind": "MONEY_RANGE",
                        "currency": "KRW",
                        **amount.model_dump(mode="json"),
                    },
                    "KRW",
                )
            )
        values.extend(
            [
                (
                    "CONTRIBUTION_MARGIN_BPS",
                    {"kind": "INTEGER", "value": profile.contribution_margin_bps},
                    "basis_point",
                ),
                (
                    "OPERATING_DAYS_PER_MONTH",
                    {"kind": "INTEGER", "value": profile.operating_days_per_month},
                    "day/month",
                ),
                (
                    "AVERAGE_TICKET_KRW",
                    {"kind": "INTEGER", "value": profile.average_ticket_krw},
                    "KRW/order",
                ),
            ]
        )
        timestamp = context.state.updated_at.isoformat().replace("+00:00", "Z")
        records: list[dict[str, Any]] = []
        for claim_type, value, unit in values:
            digest = hashlib.sha256(
                rfc8785.dumps(
                    {
                        "seed_registry_id": context.lease.head.seed_registry_id,
                        "source_id": source_id,
                        "claim_type": claim_type,
                        "value": value,
                    }
                )
            ).hexdigest()
            records.append(
                {
                    "schema_version": "2.0.0",
                    "evidence_id": f"seed-assumption-{digest[:40]}",
                    "project_id": context.project_id,
                    "claim_type": claim_type,
                    "value": value,
                    "value_kind": "DECLARED_ASSUMPTION",
                    "unit": unit,
                    "geographic_scope": {
                        "scope_type": "CASE",
                        "scope_id": source_id,
                        "boundary_version": None,
                    },
                    "source": {
                        "title": f"{source_id} 등록 모델 임시 계산값",
                        "source_ref": f"seed://{source_id}/{claim_type}",
                        "authority": "SECONDARY",
                        "source_type": "DATASET",
                        "published_or_data_date": None,
                        "source_observed_at": None,
                        "document_version": context.lease.head.seed_registry_id,
                        "checksum": digest,
                    },
                    "original_anchor": {
                        "anchor_type": "CALCULATION",
                        "locator": f"seed:{source_id}:{claim_type}",
                        "excerpt_hash": None,
                    },
                    "freshness_status": "NOT_APPLICABLE",
                    "conflict_status": "NONE",
                    "retrieved_at": timestamp,
                    "missing_context": ["실제 매물·견적 입력 전 사용하는 등록 모델 임시 범위"],
                    "durable_evidence_refs": [],
                }
            )
        return records

    def _calculate_candidate(
        self,
        *,
        context: StageContext,
        case_type: CaseType,
        proposal: dict[str, Any],
        source: dict[str, Any],
        evidence_records: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], CandidateDecisionInput]:
        proposal_id = proposal.get("proposal_id")
        source_id = proposal.get("seed_or_brand_id")
        if not isinstance(proposal_id, str) or not isinstance(source_id, str):
            raise ContractValidationError("Proposal identity is invalid")
        candidate_id = self._candidate_id(context, proposal_id)
        seed_finance_profile = self._seed_finance_profile(case_type, source_id)
        finance_input, finance_conflicts, calculation_evidence_refs = self._finance_input(
            case_type=case_type,
            source_id=source_id,
            proposal_id=proposal_id,
            evidence_records=evidence_records,
            seed_finance_profile=seed_finance_profile,
        )
        finance = calculate_finance(finance_input)
        capital_gate = evaluate_capital_gate(
            CapitalGateInput(
                own_funds_krw=context.state.founder.own_funds_krw,
                borrowing_intent=context.state.founder.borrowing_intent,
                initial_cash=finance.initial_cash,
            )
        )
        founder_fit, founder_burden = self._founder_fit(
            context=context,
            case_type=case_type,
            source=source,
        )
        proposal_missing = (
            set()
            if seed_finance_profile is not None
            else {value for value in proposal.get("missing_fields", []) if isinstance(value, str)}
        )
        material_missing = sorted(proposal_missing | set(finance.unknown_cost_fields))
        risks = self._risks(
            candidate_id=candidate_id,
            missing_fields=material_missing,
            warnings=[value for value in proposal.get("warnings", []) if isinstance(value, str)],
            conflicts=finance_conflicts,
        )
        franchise = case_type == CaseType.FRANCHISE
        decision_input = CandidateDecisionInput(
            candidate_id=candidate_id,
            case_type=case_type,
            finance=finance,
            capital_gate=capital_gate,
            founder_fit=founder_fit,
            founder_burden=founder_burden,
            material_missing_fields=material_missing,
            risks=risks,
            franchise_eligibility=(
                FranchiseEligibility.VERIFIED if franchise else FranchiseEligibility.NOT_APPLICABLE
            ),
            franchise_eligibility_evidence_refs=(
                [value for value in source.get("evidence_refs", []) if isinstance(value, str)]
                if franchise
                else []
            ),
            franchise_availability=(
                FranchiseAvailability.HQ_CONFIRMATION_REQUIRED
                if franchise
                else FranchiseAvailability.NOT_APPLICABLE
            ),
        )
        return (
            {
                "candidate_id": candidate_id,
                "proposal_id": proposal_id,
                "case_type": case_type.value,
                "display_name": proposal.get("display_name"),
                "source_id": source_id,
                "proposal": proposal,
                "finance_input": finance_input.model_dump(mode="json"),
                "finance": finance.model_dump(mode="json"),
                "calculation_evidence_refs": calculation_evidence_refs,
                "capital_gate": capital_gate.model_dump(mode="json"),
                "founder_fit": founder_fit.value,
                "founder_burden": founder_burden.value,
                "material_missing_fields": material_missing,
                "risks": [value.model_dump(mode="json") for value in risks],
                "counterfactuals": self._counterfactuals(
                    own_funds_krw=context.state.founder.own_funds_krw,
                    capital_gate=capital_gate,
                    initial_cash=finance.initial_cash,
                    unknown_cost_fields=finance.unknown_cost_fields,
                ),
                "franchise_eligibility_evidence_refs": (
                    decision_input.franchise_eligibility_evidence_refs
                ),
                "franchise_availability": decision_input.franchise_availability.value,
            },
            decision_input,
        )

    def _finance_input(
        self,
        *,
        case_type: CaseType,
        source_id: str,
        proposal_id: str,
        evidence_records: list[dict[str, Any]],
        seed_finance_profile: IndependentFinanceProfile | None,
    ) -> tuple[FinanceInput, list[str], list[str]]:
        conflicts: list[str] = []
        initial = [
            self._cost_line(
                case_type=case_type,
                category=category,
                source_id=source_id,
                proposal_id=proposal_id,
                evidence_records=evidence_records,
                conflicts=conflicts,
                seed_finance_profile=seed_finance_profile,
            )
            for category in sorted(INITIAL_COST_CATEGORIES, key=lambda value: value.value)
        ]
        monthly = [
            self._cost_line(
                case_type=case_type,
                category=category,
                source_id=source_id,
                proposal_id=proposal_id,
                evidence_records=evidence_records,
                conflicts=conflicts,
                seed_finance_profile=seed_finance_profile,
            )
            for category in sorted(MONTHLY_FIXED_COST_CATEGORIES, key=lambda value: value.value)
        ]
        contribution_margin, contribution_margin_ref = self._scalar_value(
            case_type,
            "CONTRIBUTION_MARGIN_BPS",
            source_id,
            proposal_id,
            evidence_records,
            conflicts,
            seed_finance_profile,
        )
        operating_days, operating_days_ref = self._scalar_value(
            case_type,
            "OPERATING_DAYS_PER_MONTH",
            source_id,
            proposal_id,
            evidence_records,
            conflicts,
            seed_finance_profile,
        )
        average_ticket, average_ticket_ref = self._scalar_value(
            case_type,
            "AVERAGE_TICKET_KRW",
            source_id,
            proposal_id,
            evidence_records,
            conflicts,
            seed_finance_profile,
        )
        finance_input = FinanceInput(
            initial_cost_lines=initial,
            monthly_fixed_cost_lines=monthly,
            contribution_margin_bps=contribution_margin,
            operating_days_per_month=operating_days,
            average_ticket_krw=average_ticket,
        )
        cost_refs = {
            line.evidence_ref for line in [*initial, *monthly] if line.evidence_ref is not None
        }
        scalar_refs = {
            value
            for value in (
                contribution_margin_ref,
                operating_days_ref,
                average_ticket_ref,
            )
            if value is not None
        }
        return (
            finance_input,
            sorted(set(conflicts)),
            sorted(cost_refs | scalar_refs),
        )

    def _cost_line(
        self,
        *,
        case_type: CaseType,
        category: CostCategory,
        source_id: str,
        proposal_id: str,
        evidence_records: list[dict[str, Any]],
        conflicts: list[str],
        seed_finance_profile: IndependentFinanceProfile | None,
    ) -> CostLine:
        if case_type == CaseType.INDEPENDENT and category == CostCategory.FRANCHISE_INITIAL_FEES:
            return CostLine(
                field_id=category.value,
                category=category,
                amount=MoneyRange(low=0, base=0, high=0),
                provenance=ValueProvenance.DERIVED,
            )
        if any(
            value.get("claim_type") == f"DOCUMENT_CONFLICT_COST_{category.value}"
            for value in evidence_records
        ):
            conflicts.append(f"DOCUMENT_COST_CONFLICT:{category.value}")
            return self._unknown_cost(category)
        matches = self._money_records(
            case_type,
            category.value,
            source_id,
            proposal_id,
            evidence_records,
        )
        if not matches:
            if seed_finance_profile is not None:
                amount = seed_finance_profile.cost_ranges.get(category)
                if amount is not None:
                    return CostLine(
                        field_id=category.value,
                        category=category,
                        amount=amount,
                        provenance=ValueProvenance.ASSUMPTION,
                    )
            return self._unknown_cost(category)
        grounded_or_confirmed = [
            value for value in matches if value.get("value_kind") != "DECLARED_ASSUMPTION"
        ]
        if grounded_or_confirmed:
            matches = grounded_or_confirmed
        user_confirmed = [
            value for value in matches if value.get("value_kind") == "USER_CONFIRMED_FACT"
        ]
        if user_confirmed:
            matches = user_confirmed
        distinct = {
            (
                value["value"].get("low"),
                value["value"].get("base"),
                value["value"].get("high"),
            )
            for value in matches
        }
        if len(distinct) != 1:
            conflicts.append(f"COST_CONFLICT:{category.value}")
            return self._unknown_cost(category)
        selected = min(matches, key=self._evidence_priority)
        typed = selected["value"]
        return CostLine(
            field_id=category.value,
            category=category,
            amount=MoneyRange(
                low=typed.get("low"),
                base=typed.get("base"),
                high=typed.get("high"),
            ),
            provenance=self._provenance(selected),
            evidence_ref=selected["evidence_id"],
        )

    @staticmethod
    def _unknown_cost(category: CostCategory) -> CostLine:
        return CostLine(
            field_id=category.value,
            category=category,
            amount=MoneyRange(low=None, base=None, high=None),
            provenance=ValueProvenance.UNKNOWN,
        )

    def _money_records(
        self,
        case_type: CaseType,
        field: str,
        source_id: str,
        proposal_id: str,
        evidence_records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        claim_types = {
            f"{case_type.value}_COST_{field}",
            f"CAFE_COST_{field}",
            f"COST_{field}",
        }
        return [
            value
            for value in evidence_records
            if value.get("claim_type") in claim_types
            and isinstance(value.get("value"), dict)
            and value["value"].get("kind") == "MONEY_RANGE"
            and self._scope_matches(value, source_id, proposal_id)
        ]

    def _scalar_value(
        self,
        case_type: CaseType,
        field: str,
        source_id: str,
        proposal_id: str,
        evidence_records: list[dict[str, Any]],
        conflicts: list[str],
        seed_finance_profile: IndependentFinanceProfile | None,
    ) -> tuple[int | None, str | None]:
        claim_types = {field, f"CAFE_{field}", f"{case_type.value}_{field}"}
        values = [
            value
            for value in evidence_records
            if value.get("claim_type") in claim_types
            and isinstance(value.get("value"), dict)
            and value["value"].get("kind") == "INTEGER"
            and isinstance(value["value"].get("value"), int)
            and self._scope_matches(value, source_id, proposal_id)
        ]
        grounded_or_confirmed = [
            value for value in values if value.get("value_kind") != "DECLARED_ASSUMPTION"
        ]
        if grounded_or_confirmed:
            values = grounded_or_confirmed
        distinct = {value["value"]["value"] for value in values}
        if len(distinct) > 1:
            conflicts.append(f"VALUE_CONFLICT:{field}")
            return None, None
        if not values:
            if seed_finance_profile is not None:
                fallback = {
                    "CONTRIBUTION_MARGIN_BPS": seed_finance_profile.contribution_margin_bps,
                    "OPERATING_DAYS_PER_MONTH": (seed_finance_profile.operating_days_per_month),
                    "AVERAGE_TICKET_KRW": seed_finance_profile.average_ticket_krw,
                }.get(field)
                if fallback is not None:
                    return fallback, None
            return None, None
        selected = min(values, key=self._evidence_priority)
        return int(selected["value"]["value"]), str(selected["evidence_id"])

    @staticmethod
    def _scope_matches(
        evidence: dict[str, Any],
        source_id: str,
        proposal_id: str,
    ) -> bool:
        scope = evidence.get("geographic_scope")
        if not isinstance(scope, dict) or scope.get("scope_type") != "CASE":
            return True
        return scope.get("scope_id") in {source_id, proposal_id}

    @staticmethod
    def _evidence_priority(value: dict[str, Any]) -> tuple[int, str]:
        value_kind = value.get("value_kind")
        authority = value.get("source", {}).get("authority")
        priority = 0 if value_kind == "USER_CONFIRMED_FACT" else 1
        if authority == "VALIDATED_BENCHMARK":
            priority = 2
        if value_kind == "DECLARED_ASSUMPTION":
            priority = 3
        return priority, str(value.get("evidence_id"))

    @staticmethod
    def _provenance(value: dict[str, Any]) -> ValueProvenance:
        if value.get("value_kind") == "USER_CONFIRMED_FACT":
            return ValueProvenance.USER_INPUT
        if value.get("source", {}).get("authority") == "VALIDATED_BENCHMARK":
            return ValueProvenance.BENCHMARK
        if value.get("value_kind") == "DECLARED_ASSUMPTION":
            return ValueProvenance.ASSUMPTION
        return ValueProvenance.FACT

    def _seed_finance_profile(
        self, case_type: CaseType, source_id: str
    ) -> IndependentFinanceProfile | None:
        if case_type != CaseType.INDEPENDENT or self._seed_registry is None:
            return None
        seed = self._seed_registry.get(source_id)
        return seed.finance_profile if seed is not None else None

    @staticmethod
    def _founder_fit(
        *,
        context: StageContext,
        case_type: CaseType,
        source: dict[str, Any],
    ) -> tuple[FounderFitStatus, FounderBurdenLevel]:
        mode = context.state.founder.operation_mode
        if case_type == CaseType.FRANCHISE:
            return FounderFitStatus.CONDITIONAL, FounderBurdenLevel.UNKNOWN
        allowed_modes = {
            value for value in source.get("allowed_operation_modes", []) if isinstance(value, str)
        }
        if mode.value not in allowed_modes:
            return FounderFitStatus.FAIL, FounderBurdenLevel.UNKNOWN
        if mode == OperationMode.DIRECT_FULL_TIME:
            return FounderFitStatus.PASS, FounderBurdenLevel.HIGH
        if mode == OperationMode.EMPLOYEE_LED:
            return FounderFitStatus.PASS, FounderBurdenLevel.MEDIUM
        if mode == OperationMode.DIRECT_PART_TIME:
            return FounderFitStatus.CONDITIONAL, FounderBurdenLevel.HIGH
        return FounderFitStatus.CONDITIONAL, FounderBurdenLevel.UNKNOWN

    @classmethod
    def _risks(
        cls,
        *,
        candidate_id: str,
        missing_fields: list[str],
        warnings: list[str],
        conflicts: list[str],
    ) -> list[RiskSignal]:
        values = [(f"missing:{value}", RiskSeverity.HIGH) for value in sorted(set(missing_fields))]
        values.extend((f"warning:{value}", RiskSeverity.MEDIUM) for value in sorted(set(warnings)))
        values.extend(
            (f"conflict:{value}", RiskSeverity.CRITICAL) for value in sorted(set(conflicts))
        )
        return [
            RiskSignal(
                risk_id=cls._stable_id("risk", candidate_id, label),
                severity=severity,
            )
            for label, severity in values
        ]

    @staticmethod
    def _counterfactuals(
        *,
        own_funds_krw: int,
        capital_gate: CapitalGateResult,
        initial_cash: MoneyRange,
        unknown_cost_fields: list[str],
    ) -> list[dict[str, Any]]:
        if capital_gate.minimum_required_reduction_krw is not None:
            return [
                {
                    "kind": "INITIAL_CASH_REDUCTION_TO_CLEAR_HARD_GATE",
                    "amount_krw": capital_gate.minimum_required_reduction_krw,
                }
            ]
        if initial_cash.high is not None and initial_cash.high <= own_funds_krw:
            return [
                {
                    "kind": "INITIAL_CASH_INCREASE_BEFORE_GATE_CHANGES",
                    "amount_krw": own_funds_krw - initial_cash.high,
                }
            ]
        return [
            {
                "kind": "MISSING_COSTS_REQUIRED_TO_RESOLVE_GATE",
                "field_ids": sorted(set(unknown_cost_fields)),
            }
        ]

    @classmethod
    def _candidate_id(cls, context: StageContext, proposal_id: str) -> str:
        return cls._stable_id("candidate", context.lease.workflow_run_id, proposal_id)

    @staticmethod
    def _stable_id(prefix: str, *parts: str) -> str:
        digest = hashlib.sha256(rfc8785.dumps(list(parts))).hexdigest()
        return f"{prefix}-{digest[:32]}"
