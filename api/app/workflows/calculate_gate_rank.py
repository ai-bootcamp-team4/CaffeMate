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


class CalculateGateRankStageHandler:
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
            if category.startswith("CONFLICT:"):
                actual_category = category.removeprefix("CONFLICT:")
                records.append(
                    {
                        "evidence_id": f"document-conflict-{claims[0]['claim_id']}",
                        "project_id": context.project_id,
                        "claim_type": f"DOCUMENT_CONFLICT_COST_{actual_category}",
                        "value": {"kind": "CONFLICT"},
                    }
                )
                continue
            amount = sum(int(claim["value_json"]) for claim in claims)
            claim_ids = sorted(str(claim["claim_id"]) for claim in claims)
            digest = hashlib.sha256(rfc8785.dumps(claim_ids)).hexdigest()
            records.append(
                {
                    "evidence_id": f"document-evidence-{digest[:32]}",
                    "project_id": context.project_id,
                    "claim_type": f"COST_{category}",
                    "value": {
                        "kind": "MONEY_RANGE",
                        "low": amount,
                        "base": amount,
                        "high": amount,
                    },
                    "value_kind": "USER_CONFIRMED_FACT",
                    "source": {"authority": "USER_DOCUMENT"},
                    "geographic_scope": {"scope_type": "CASE", "scope_id": source_id},
                    "document_claim_ids": claim_ids,
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
        finance_input, finance_conflicts, calculation_evidence_refs = self._finance_input(
            case_type=case_type,
            source_id=source_id,
            proposal_id=proposal_id,
            evidence_records=evidence_records,
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
        proposal_missing = {
            value for value in proposal.get("missing_fields", []) if isinstance(value, str)
        }
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
        )
        operating_days, operating_days_ref = self._scalar_value(
            case_type,
            "OPERATING_DAYS_PER_MONTH",
            source_id,
            proposal_id,
            evidence_records,
            conflicts,
        )
        average_ticket, average_ticket_ref = self._scalar_value(
            case_type,
            "AVERAGE_TICKET_KRW",
            source_id,
            proposal_id,
            evidence_records,
            conflicts,
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
            return self._unknown_cost(category)
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
        distinct = {value["value"]["value"] for value in values}
        if len(distinct) > 1:
            conflicts.append(f"VALUE_CONFLICT:{field}")
            return None, None
        if not values:
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
