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
from app.candidates.seed_registry import IndependentSeedRegistry
from app.domain.errors import ContractValidationError
from app.domain.models import CaseType, FranchiseEligibility, OperationMode
from app.finance.calculator import calculate_finance, evaluate_capital_gate
from app.finance.models import CapitalGateInput, CapitalGateResult, MoneyRange
from app.workflows.candidate_finance import CandidateFinanceInputBuilder
from app.workflows.models import StageControl
from app.workflows.stage_context import StageContext


class CalculateGateRankStageHandler:
    def __init__(self, seed_registry: IndependentSeedRegistry | None = None) -> None:
        self._finance_inputs = CandidateFinanceInputBuilder(seed_registry)

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
                evidence_records = self._finance_inputs.evidence_records(
                    context=context,
                    case_type=case_type,
                    source_id=source_id,
                    base_records=base_evidence_records,
                )
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
        seed_finance_profile = self._finance_inputs.seed_finance_profile(case_type, source_id)
        finance_input, finance_conflicts, calculation_evidence_refs = self._finance_inputs.build(
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
