from copy import deepcopy
from typing import Any

from app.domain.models import BorrowingIntent
from app.finance.models import (
    INITIAL_COST_CATEGORIES,
    MONTHLY_FIXED_COST_CATEGORIES,
    CostCategory,
)
from app.workflows.calculate_gate_rank import CalculateGateRankStageHandler
from app.workflows.proposal import ProposalStageHandler
from app.workflows.stage_context import StageContext
from tests.test_agent_boundary import evidence_record
from tests.test_proposal_stages import FakeRuntime, proposal_context, proposal_result


def money_evidence(
    category: CostCategory,
    amount: int,
    *,
    suffix: str = "1",
) -> dict[str, Any]:
    value = evidence_record(f"ev-cost-{category.value.lower()}-{suffix}")
    value["claim_type"] = f"INDEPENDENT_COST_{category.value}"
    value["value"] = {
        "kind": "MONEY_RANGE",
        "currency": "KRW",
        "low": amount,
        "base": amount,
        "high": amount,
    }
    value["unit"] = "KRW"
    return value


def scalar_evidence(field: str, amount: int) -> dict[str, Any]:
    value = evidence_record(f"ev-{field.lower()}")
    value["claim_type"] = field
    value["value"] = {"kind": "INTEGER", "value": amount}
    return value


def complete_independent_finance(amount: int = 1_000_000) -> list[dict[str, Any]]:
    categories = sorted(
        (INITIAL_COST_CATEGORIES | MONTHLY_FIXED_COST_CATEGORIES)
        - {CostCategory.FRANCHISE_INITIAL_FEES},
        key=lambda value: value.value,
    )
    return [
        *(money_evidence(category, amount) for category in categories),
        scalar_evidence("CONTRIBUTION_MARGIN_BPS", 5_000),
        scalar_evidence("OPERATING_DAYS_PER_MONTH", 30),
        scalar_evidence("AVERAGE_TICKET_KRW", 5_000),
    ]


def calculation_context(
    *,
    evidence_records: list[dict[str, Any]] | None = None,
    include_franchise: bool = False,
) -> StageContext:
    independent_context = proposal_context("PROPOSE_INDEPENDENT")
    independent_input = independent_context.dependency_results["INDEPENDENT_SEED"][
        "independent_seed"
    ]["proposal_input"]
    independent_input["evidence_records"] = evidence_records or []
    independent_result = ProposalStageHandler.independent(
        FakeRuntime(proposal_result)
    ).execute(independent_context)
    independent_proposal = independent_result["independent_proposal"]
    independent_proposal["candidate_proposals"][0]["missing_fields"] = []
    independent_proposal["candidate_proposals"][0]["warnings"] = []
    dependencies: dict[str, dict[str, Any]] = {
        "PROPOSE_INDEPENDENT": independent_result,
    }
    if include_franchise:
        franchise_context = proposal_context("PROPOSE_FRANCHISE")
        franchise_result = ProposalStageHandler.franchise(
            FakeRuntime(proposal_result)
        ).execute(franchise_context)
        dependencies["PROPOSE_FRANCHISE"] = franchise_result
    return StageContext(
        lease=independent_context.lease.model_copy(
            update={
                "stage_run_id": "stage-calculate",
                "stage_code": "CALCULATE_GATE_RANK",
            }
        ),
        project_id=independent_context.project_id,
        state=independent_context.state,
        dependency_results=dependencies,
    )


def test_unknown_costs_remain_unknown_and_candidate_stays_conditional() -> None:
    output = CalculateGateRankStageHandler().execute(calculation_context())[
        "calculate_gate_rank"
    ]

    assert isinstance(output, dict)
    candidate = output["candidates"][0]
    assert candidate["finance"]["initial_cash"] == {
        "low": None,
        "base": None,
        "high": None,
    }
    assert candidate["capital_gate"]["status"] == "CONDITIONAL"
    assert candidate["decision"]["review_status"] == "CONDITIONAL_REVIEW"
    assert "MATERIAL_COST_UNKNOWN" in candidate["decision"]["reason_codes"]
    assert candidate["counterfactuals"][0]["kind"] == (
        "MISSING_COSTS_REQUIRED_TO_RESOLVE_GATE"
    )
    assert "DEPOSIT" in candidate["counterfactuals"][0]["field_ids"]
    fee = next(
        value
        for value in candidate["finance_input"]["initial_cost_lines"]
        if value["category"] == "FRANCHISE_INITIAL_FEES"
    )
    assert fee["amount"] == {"low": 0, "base": 0, "high": 0}
    assert fee["provenance"] == "DERIVED"


def test_grounded_costs_produce_exact_finance_gate_and_recommended_rank() -> None:
    output = CalculateGateRankStageHandler().execute(
        calculation_context(evidence_records=complete_independent_finance())
    )["calculate_gate_rank"]

    assert isinstance(output, dict)
    candidate = output["candidates"][0]
    assert candidate["finance"]["initial_cash"] == {
        "low": 8_000_000,
        "base": 8_000_000,
        "high": 8_000_000,
    }
    assert candidate["finance"]["monthly_fixed_cost"] == {
        "low": 3_000_000,
        "base": 3_000_000,
        "high": 3_000_000,
    }
    assert candidate["finance"]["break_even_monthly_sales_krw"] == 6_000_000
    assert candidate["finance"]["required_daily_orders"] == "40.00"
    assert candidate["capital_gate"]["status"] == "PASS"
    assert candidate["founder_fit"] == "PASS"
    assert candidate["decision"]["review_status"] == "REVIEW_RECOMMENDED"
    assert candidate["decision"]["rank"] == 1
    assert output["primary_candidate_id"] == candidate["candidate_id"]


def test_confirmed_unfunded_minimum_excludes_and_emits_reversal_amount() -> None:
    context = calculation_context(
        evidence_records=complete_independent_finance(amount=10_000_000)
    )
    context.state.founder.borrowing_intent = BorrowingIntent.NO

    output = CalculateGateRankStageHandler().execute(context)["calculate_gate_rank"]

    assert isinstance(output, dict)
    candidate = output["candidates"][0]
    assert candidate["finance"]["initial_cash"]["low"] == 80_000_000
    assert candidate["capital_gate"] == {
        "status": "FAIL",
        "reason_code": "MINIMUM_INITIAL_CASH_EXCEEDS_OWN_FUNDS",
        "minimum_required_reduction_krw": 30_000_000,
    }
    assert candidate["decision"]["review_status"] == "EXCLUDED"
    assert candidate["decision"]["rank"] is None
    assert candidate["counterfactuals"] == [
        {
            "kind": "INITIAL_CASH_REDUCTION_TO_CLEAR_HARD_GATE",
            "amount_krw": 30_000_000,
        }
    ]


def test_franchise_with_missing_disclosure_remains_ranked_conditional() -> None:
    output = CalculateGateRankStageHandler().execute(
        calculation_context(include_franchise=True)
    )["calculate_gate_rank"]

    assert isinstance(output, dict)
    franchise = next(
        value for value in output["candidates"] if value["case_type"] == "FRANCHISE"
    )
    assert franchise["display_name"] == "검증 브랜드"
    assert franchise["franchise_availability"] == "HQ_CONFIRMATION_REQUIRED"
    assert franchise["franchise_eligibility_evidence_refs"] == [
        "ev-franchise-verified"
    ]
    assert franchise["decision"]["review_status"] == "CONDITIONAL_REVIEW"
    assert franchise["decision"]["rank"] is not None
    assert "FRANCHISE_AREA_AVAILABILITY_UNCONFIRMED" in franchise["decision"][
        "reason_codes"
    ]


def test_conflicting_cost_evidence_is_not_auto_resolved() -> None:
    records = complete_independent_finance()
    records.append(money_evidence(CostCategory.DEPOSIT, 9_000_000, suffix="conflict"))

    output = CalculateGateRankStageHandler().execute(
        calculation_context(evidence_records=records)
    )["calculate_gate_rank"]

    assert isinstance(output, dict)
    candidate = output["candidates"][0]
    assert candidate["finance"]["initial_cash"] == {
        "low": None,
        "base": None,
        "high": None,
    }
    assert any(value["severity"] == "CRITICAL" for value in candidate["risks"])
    assert candidate["decision"]["review_status"] == "CONDITIONAL_REVIEW"


def test_calculation_and_ranking_are_repeatable() -> None:
    context = calculation_context(
        evidence_records=complete_independent_finance(),
        include_franchise=True,
    )
    handler = CalculateGateRankStageHandler()

    first = handler.execute(deepcopy(context))
    second = handler.execute(deepcopy(context))

    assert first == second
