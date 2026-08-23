import hashlib
from typing import Any

import rfc8785

from app.contracts.schema_registry import ContractRegistry
from app.domain.errors import ContractValidationError
from app.finance.calculator import calculate_finance, evaluate_capital_gate
from app.finance.models import CapitalGateInput, FinanceInput
from app.results.projection import project_candidate_results
from app.workflows.stage_context import StageContext


class CandidateAuditInputBuilder:
    def __init__(self, *, contracts: ContractRegistry | None = None) -> None:
        self._contracts = contracts or ContractRegistry()

    def build(self, context: StageContext) -> dict[str, Any]:
        dependency = context.dependency_results.get("CALCULATE_GATE_RANK")
        calculated = dependency.get("calculate_gate_rank") if dependency else None
        if not isinstance(calculated, dict):
            raise ContractValidationError("CANDIDATE_AUDIT requires calculated candidate results")
        candidates = calculated.get("candidates")
        evidence_records = calculated.get("evidence_records")
        if not isinstance(candidates, list) or not candidates:
            raise ContractValidationError("CANDIDATE_AUDIT requires candidates")
        if not isinstance(evidence_records, list):
            raise ContractValidationError("CANDIDATE_AUDIT Evidence is invalid")
        for evidence in evidence_records:
            if not isinstance(evidence, dict):
                raise ContractValidationError("CANDIDATE_AUDIT Evidence is invalid")
            self._contracts.validate_evidence_record(evidence)

        projected = project_candidate_results(
            candidates,
            project_id=context.project_id,
            state_version=context.lease.head.state_version,
            evidence_records=evidence_records,
            contracts=self._contracts,
        )
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise ContractValidationError("CANDIDATE_AUDIT candidate is invalid")
            self._validate_calculated_candidate(context, candidate)

        candidate_ids = [value["candidate_id"] for value in projected]
        input_projection = [
            {
                "candidate_id": value["candidate_id"],
                "finance_input": source.get("finance_input"),
            }
            for value, source in zip(projected, candidates, strict=True)
        ]
        output_projection = [
            {
                "candidate_id": value["candidate_id"],
                "finance": source.get("finance"),
                "capital_gate": source.get("capital_gate"),
                "founder_fit": source.get("founder_fit"),
                "decision": source.get("decision"),
            }
            for value, source in zip(projected, candidates, strict=True)
        ]
        return {
            "candidates": projected,
            "evidence_records": evidence_records,
            "calculation_snapshot": {
                "calculation_version": "finance-gate-rank.v1",
                "candidate_ids": candidate_ids,
                "input_digest": self._content_digest(input_projection),
                "output_digest": self._content_digest(output_projection),
                "warning_codes": sorted(
                    {
                        code
                        for candidate in candidates
                        for code in candidate.get("decision", {}).get("reason_codes", [])
                        if isinstance(code, str)
                    }
                ),
            },
            "gate_snapshot": {
                "gate_version": "capital-gate-founder-fit.v1",
                "candidate_gates": [self._candidate_gate(candidate) for candidate in candidates],
            },
        }

    @staticmethod
    def _content_digest(value: Any) -> str:
        return f"sha256:{hashlib.sha256(rfc8785.dumps(value)).hexdigest()}"

    @staticmethod
    def _candidate_gate(candidate: dict[str, Any]) -> dict[str, str]:
        capital_result = candidate.get("capital_gate")
        decision_result = candidate.get("decision")
        finance = candidate.get("finance")
        if (
            not isinstance(capital_result, dict)
            or not isinstance(decision_result, dict)
            or not isinstance(finance, dict)
        ):
            raise ContractValidationError("Calculated candidate Gate input is invalid")
        capital = capital_result.get("status")
        founder = candidate.get("founder_fit")
        decision = decision_result.get("review_status")
        if not isinstance(capital, str) or not isinstance(founder, str):
            raise ContractValidationError("Calculated candidate Gate status is invalid")
        hard_constraint = {
            "PASS": "PASS",
            "FAIL": "FAIL",
            "CONDITIONAL": "UNKNOWN",
        }.get(capital)
        founder_fit = {
            "PASS": "PASS",
            "FAIL": "FAIL",
            "CONDITIONAL": "UNKNOWN",
        }.get(founder)
        economic_viability = (
            "PASS"
            if capital == "PASS"
            and not finance.get("unknown_cost_fields")
            and finance.get("break_even_monthly_sales_krw") is not None
            else "FAIL"
            if capital == "FAIL"
            else "UNKNOWN"
        )
        if (
            hard_constraint is None
            or founder_fit is None
            or decision
            not in {
                "REVIEW_RECOMMENDED",
                "CONDITIONAL_REVIEW",
                "EXCLUDED",
            }
        ):
            raise ContractValidationError("Calculated candidate Gate snapshot is invalid")
        return {
            "candidate_id": candidate["candidate_id"],
            "hard_constraint": hard_constraint,
            "economic_viability": economic_viability,
            "founder_fit": founder_fit,
            "risk_adjusted_status": decision,
        }

    @staticmethod
    def _validate_calculated_candidate(
        context: StageContext,
        candidate: dict[str, Any],
    ) -> None:
        try:
            finance_input = FinanceInput.model_validate(candidate.get("finance_input"))
        except ValueError as error:
            raise ContractValidationError(
                "Calculated candidate Finance input is invalid"
            ) from error
        calculated_finance = calculate_finance(finance_input)
        expected_finance = calculated_finance.model_dump(mode="json")
        if candidate.get("finance") != expected_finance:
            raise ContractValidationError(
                "Calculated candidate Finance output failed deterministic replay"
            )
        expected_gate = evaluate_capital_gate(
            CapitalGateInput(
                own_funds_krw=context.state.founder.own_funds_krw,
                borrowing_intent=context.state.founder.borrowing_intent,
                initial_cash=calculated_finance.initial_cash,
            )
        ).model_dump(mode="json")
        if candidate.get("capital_gate") != expected_gate:
            raise ContractValidationError(
                "Calculated candidate capital Gate failed deterministic replay"
            )
        unknown_fields = set(expected_finance["unknown_cost_fields"])
        material_missing = {
            value
            for value in candidate.get("material_missing_fields", [])
            if isinstance(value, str)
        }
        if not unknown_fields.issubset(material_missing):
            raise ContractValidationError(
                "Calculated candidate dropped material unknown cost fields"
            )
