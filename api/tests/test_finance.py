from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.models import BorrowingIntent
from app.finance.calculator import calculate_finance, evaluate_capital_gate
from app.finance.models import (
    CapitalGateInput,
    CapitalGateStatus,
    CostCategory,
    CostLine,
    FinanceInput,
    MoneyRange,
    ValueProvenance,
    VariableCostRateLine,
)


def cost(
    field_id: str,
    category: CostCategory,
    low: int | None,
    base: int | None,
    high: int | None,
    provenance: ValueProvenance = ValueProvenance.ASSUMPTION,
) -> CostLine:
    return CostLine(
        field_id=field_id,
        category=category,
        amount=MoneyRange(low=low, base=base, high=high),
        provenance=provenance,
    )


def complete_initial_costs(*lines: CostLine) -> list[CostLine]:
    present = {line.category for line in lines}
    zero_lines = [
        cost(f"zero-{category.value}", category, 0, 0, 0)
        for category in (
            CostCategory.DEPOSIT,
            CostCategory.ACQUISITION_OR_PREMIUM,
            CostCategory.CONSTRUCTION,
            CostCategory.EQUIPMENT,
            CostCategory.FRANCHISE_INITIAL_FEES,
            CostCategory.PREOPENING,
            CostCategory.OPENING_INVENTORY,
            CostCategory.CONTINGENCY,
            CostCategory.OPERATING_RESERVE,
        )
        if category not in present
    ]
    return [*lines, *zero_lines]


def complete_monthly_costs(*lines: CostLine) -> list[CostLine]:
    present = {line.category for line in lines}
    zero_lines = [
        cost(f"zero-{category.value}", category, 0, 0, 0)
        for category in (
            CostCategory.MONTHLY_OCCUPANCY,
            CostCategory.MONTHLY_LABOR,
            CostCategory.MONTHLY_OTHER_FIXED,
        )
        if category not in present
    ]
    return [*lines, *zero_lines]


def test_finance_calculation_is_exact_and_repeatable() -> None:
    finance_input = FinanceInput(
        initial_cost_lines=complete_initial_costs(
            cost("deposit", CostCategory.DEPOSIT, 20_000_000, 25_000_000, 30_000_000),
            cost("equipment", CostCategory.EQUIPMENT, 10_000_000, 15_000_000, 20_000_000),
        ),
        monthly_fixed_cost_lines=complete_monthly_costs(
            cost(
                "occupancy",
                CostCategory.MONTHLY_OCCUPANCY,
                2_000_000,
                2_500_000,
                3_000_000,
            ),
            cost("labor", CostCategory.MONTHLY_LABOR, 3_000_000, 3_500_000, 4_000_000),
        ),
        contribution_margin_bps=6_000,
        operating_days_per_month=25,
        average_ticket_krw=5_000,
    )

    first = calculate_finance(finance_input)
    second = calculate_finance(finance_input)

    assert first == second
    assert first.initial_cash == MoneyRange(low=30_000_000, base=40_000_000, high=50_000_000)
    assert first.monthly_fixed_cost == MoneyRange(
        low=5_000_000,
        base=6_000_000,
        high=7_000_000,
    )
    assert first.break_even_monthly_sales_krw == 10_000_000
    assert first.required_daily_orders == Decimal("80.00")
    assert first.unknown_cost_fields == []


def test_percentage_royalty_reduces_margin_without_changing_monthly_fixed_cost() -> None:
    result = calculate_finance(
        FinanceInput(
            initial_cost_lines=complete_initial_costs(),
            monthly_fixed_cost_lines=complete_monthly_costs(
                cost(
                    "monthly-fixed",
                    CostCategory.MONTHLY_OTHER_FIXED,
                    6_200_000,
                    6_200_000,
                    6_200_000,
                )
            ),
            variable_cost_rate_lines=[
                VariableCostRateLine(
                    field_id="SALES_ROYALTY",
                    rate_bps=300,
                    provenance=ValueProvenance.FACT,
                    evidence_ref="royalty:3pct",
                )
            ],
            contribution_margin_bps=6_500,
            operating_days_per_month=25,
            average_ticket_krw=5_000,
        )
    )

    assert result.monthly_fixed_cost.base == 6_200_000
    assert result.base_contribution_margin_bps == 6_500
    assert result.variable_cost_rate_bps == 300
    assert result.effective_contribution_margin_bps == 6_200
    assert result.break_even_monthly_sales_krw == 10_000_000
    assert result.required_daily_orders == Decimal("80.00")


def test_unknown_or_margin_consuming_variable_rate_fails_closed() -> None:
    unknown = calculate_finance(
        FinanceInput(
            initial_cost_lines=complete_initial_costs(),
            monthly_fixed_cost_lines=complete_monthly_costs(
                cost("fixed", CostCategory.MONTHLY_OTHER_FIXED, 1_000_000, 1_000_000, 1_000_000)
            ),
            variable_cost_rate_lines=[
                VariableCostRateLine(
                    field_id="SALES_ROYALTY",
                    rate_bps=None,
                    provenance=ValueProvenance.UNKNOWN,
                )
            ],
            contribution_margin_bps=6_500,
        )
    )
    exhausted = calculate_finance(
        FinanceInput(
            initial_cost_lines=complete_initial_costs(),
            monthly_fixed_cost_lines=complete_monthly_costs(
                cost("fixed", CostCategory.MONTHLY_OTHER_FIXED, 1_000_000, 1_000_000, 1_000_000)
            ),
            variable_cost_rate_lines=[
                VariableCostRateLine(
                    field_id="SALES_ROYALTY",
                    rate_bps=6_500,
                    provenance=ValueProvenance.FACT,
                    evidence_ref="royalty:65pct",
                )
            ],
            contribution_margin_bps=6_500,
        )
    )

    assert unknown.variable_cost_rate_bps is None
    assert unknown.effective_contribution_margin_bps is None
    assert unknown.break_even_monthly_sales_krw is None
    assert "SALES_ROYALTY" in unknown.unknown_cost_fields
    assert exhausted.variable_cost_rate_bps == 6_500
    assert exhausted.effective_contribution_margin_bps is None
    assert exhausted.break_even_monthly_sales_krw is None
    assert "EFFECTIVE_CONTRIBUTION_MARGIN" in exhausted.unknown_cost_fields


def test_unknown_cost_scenario_propagates_instead_of_becoming_zero() -> None:
    result = calculate_finance(
        FinanceInput(
            initial_cost_lines=complete_initial_costs(
                cost("deposit", CostCategory.DEPOSIT, 20_000_000, 25_000_000, 30_000_000),
                cost(
                    "premium",
                    CostCategory.ACQUISITION_OR_PREMIUM,
                    None,
                    None,
                    None,
                    ValueProvenance.UNKNOWN,
                ),
            ),
            monthly_fixed_cost_lines=complete_monthly_costs(
                cost("labor", CostCategory.MONTHLY_LABOR, 3_000_000, None, 5_000_000)
            ),
            contribution_margin_bps=6_000,
            operating_days_per_month=25,
            average_ticket_krw=5_000,
        )
    )

    assert result.initial_cash == MoneyRange(low=None, base=None, high=None)
    assert result.monthly_fixed_cost == MoneyRange(low=3_000_000, base=None, high=5_000_000)
    assert result.break_even_monthly_sales_krw is None
    assert result.required_daily_orders is None
    assert result.unknown_cost_fields == ["labor", "premium"]


def test_missing_cost_category_propagates_and_break_even_stays_unknown() -> None:
    result = calculate_finance(
        FinanceInput(
            initial_cost_lines=[],
            monthly_fixed_cost_lines=[
                cost("labor", CostCategory.MONTHLY_LABOR, 3_000_000, 3_000_000, 3_000_000)
            ],
        )
    )

    assert result.initial_cash == MoneyRange(low=None, base=None, high=None)
    assert CostCategory.DEPOSIT.value in result.unknown_cost_fields
    assert CostCategory.MONTHLY_OCCUPANCY.value in result.unknown_cost_fields
    assert result.break_even_monthly_sales_krw is None
    assert result.required_daily_orders is None


def test_cost_category_cannot_be_placed_in_wrong_section() -> None:
    with pytest.raises(ValidationError, match="monthly cost category"):
        FinanceInput(
            initial_cost_lines=[
                cost("labor", CostCategory.MONTHLY_LABOR, 1_000_000, 1_000_000, 1_000_000)
            ],
            monthly_fixed_cost_lines=[],
        )


def test_known_money_range_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="nondecreasing"):
        MoneyRange(low=20, base=10, high=30)


def test_fact_cost_requires_evidence_reference() -> None:
    with pytest.raises(ValidationError, match="evidence_ref"):
        CostLine(
            field_id="deposit",
            category=CostCategory.DEPOSIT,
            amount=MoneyRange(low=10, base=10, high=10),
            provenance=ValueProvenance.FACT,
        )


def test_unknown_provenance_cannot_hide_a_numeric_value() -> None:
    with pytest.raises(ValidationError, match="cannot contain"):
        cost(
            "premium",
            CostCategory.ACQUISITION_OR_PREMIUM,
            0,
            0,
            0,
            ValueProvenance.UNKNOWN,
        )


def test_structurally_not_applicable_cost_can_be_derived_zero() -> None:
    line = cost(
        "franchise-fee-not-applicable",
        CostCategory.FRANCHISE_INITIAL_FEES,
        0,
        0,
        0,
        ValueProvenance.DERIVED,
    )

    assert line.amount == MoneyRange(low=0, base=0, high=0)
    assert line.evidence_ref is None


def test_capital_gate_passes_only_when_own_funds_cover_high_scenario() -> None:
    result = evaluate_capital_gate(
        CapitalGateInput(
            own_funds_krw=50_000_000,
            borrowing_intent=BorrowingIntent.UNDECIDED,
            initial_cash=MoneyRange(low=30_000_000, base=40_000_000, high=50_000_000),
        )
    )

    assert result.status == CapitalGateStatus.PASS
    assert result.reason_code == "OWN_FUNDS_COVER_HIGH_SCENARIO"


def test_capital_gate_fails_only_on_confirmed_unfunded_minimum() -> None:
    result = evaluate_capital_gate(
        CapitalGateInput(
            own_funds_krw=50_000_000,
            borrowing_intent=BorrowingIntent.NO,
            initial_cash=MoneyRange(low=58_000_000, base=65_000_000, high=72_000_000),
        )
    )

    assert result.status == CapitalGateStatus.FAIL
    assert result.reason_code == "MINIMUM_INITIAL_CASH_EXCEEDS_OWN_FUNDS"
    assert result.minimum_required_reduction_krw == 8_000_000
    assert result.trace is not None
    assert result.trace.gate_type == "CAPITAL"
    assert result.trace.status == CapitalGateStatus.FAIL
    assert result.trace.reason_code == result.reason_code
    assert result.trace.decisive_input_refs == [
        "founder.borrowing_intent",
        "founder.own_funds_krw",
        "finance.initial_cash.low",
    ]
    assert result.trace.metrics == {
        "own_funds_krw": 50_000_000,
        "minimum_required_krw": 58_000_000,
        "maximum_required_krw": 72_000_000,
        "shortfall_krw": 8_000_000,
    }


@pytest.mark.parametrize("borrowing_intent", [BorrowingIntent.YES, BorrowingIntent.UNDECIDED])
def test_possible_borrowing_keeps_unfunded_candidate_conditional(
    borrowing_intent: BorrowingIntent,
) -> None:
    result = evaluate_capital_gate(
        CapitalGateInput(
            own_funds_krw=50_000_000,
            borrowing_intent=borrowing_intent,
            initial_cash=MoneyRange(low=58_000_000, base=65_000_000, high=72_000_000),
        )
    )

    assert result.status == CapitalGateStatus.CONDITIONAL
    assert result.minimum_required_reduction_krw is None


def test_missing_minimum_cash_never_causes_false_pass_or_fail() -> None:
    result = evaluate_capital_gate(
        CapitalGateInput(
            own_funds_krw=50_000_000,
            borrowing_intent=BorrowingIntent.NO,
            initial_cash=MoneyRange(low=None, base=40_000_000, high=60_000_000),
        )
    )

    assert result.status == CapitalGateStatus.CONDITIONAL
    assert result.reason_code == "INITIAL_CASH_LOW_UNKNOWN"
