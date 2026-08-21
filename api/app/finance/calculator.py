from decimal import ROUND_CEILING, Decimal

from app.finance.models import (
    INITIAL_COST_CATEGORIES,
    MONTHLY_FIXED_COST_CATEGORIES,
    CapitalGateInput,
    CapitalGateResult,
    CapitalGateStatus,
    CostCategory,
    CostLine,
    FinanceInput,
    FinanceResult,
    MoneyRange,
)


def calculate_finance(value: FinanceInput) -> FinanceResult:
    initial_cash, initial_unknown = _sum_lines(
        value.initial_cost_lines,
        required_categories=INITIAL_COST_CATEGORIES,
    )
    monthly_fixed, monthly_unknown = _sum_lines(
        value.monthly_fixed_cost_lines,
        required_categories=MONTHLY_FIXED_COST_CATEGORIES,
    )
    break_even = _break_even(monthly_fixed.base, value.contribution_margin_bps)
    orders = _required_orders(
        break_even,
        operating_days=value.operating_days_per_month,
        average_ticket=value.average_ticket_krw,
    )
    return FinanceResult(
        initial_cash=initial_cash,
        monthly_fixed_cost=monthly_fixed,
        break_even_monthly_sales_krw=break_even,
        required_daily_orders=orders,
        unknown_cost_fields=sorted(set(initial_unknown + monthly_unknown)),
    )


def evaluate_capital_gate(value: CapitalGateInput) -> CapitalGateResult:
    low = value.initial_cash.low
    high = value.initial_cash.high
    if low is None:
        return CapitalGateResult(
            status=CapitalGateStatus.CONDITIONAL,
            reason_code="INITIAL_CASH_LOW_UNKNOWN",
        )
    if high is not None and high <= value.own_funds_krw:
        return CapitalGateResult(
            status=CapitalGateStatus.PASS,
            reason_code="OWN_FUNDS_COVER_HIGH_SCENARIO",
        )
    if value.borrowing_intent == "NO" and low > value.own_funds_krw:
        return CapitalGateResult(
            status=CapitalGateStatus.FAIL,
            reason_code="MINIMUM_INITIAL_CASH_EXCEEDS_OWN_FUNDS",
            minimum_required_reduction_krw=low - value.own_funds_krw,
        )
    return CapitalGateResult(
        status=CapitalGateStatus.CONDITIONAL,
        reason_code="CAPITAL_COVERAGE_REQUIRES_CONFIRMATION",
    )


def _sum_lines(
    lines: list[CostLine],
    *,
    required_categories: frozenset[CostCategory],
) -> tuple[MoneyRange, list[str]]:
    present_categories = {line.category for line in lines}
    missing_categories = required_categories - present_categories
    unknown = [
        line.field_id
        for line in lines
        if None in (line.amount.low, line.amount.base, line.amount.high)
    ]
    unknown.extend(category.value for category in missing_categories)

    if missing_categories:
        return MoneyRange(low=None, base=None, high=None), unknown

    return (
        MoneyRange(
            low=_sum_scenario([line.amount.low for line in lines]),
            base=_sum_scenario([line.amount.base for line in lines]),
            high=_sum_scenario([line.amount.high for line in lines]),
        ),
        unknown,
    )


def _sum_scenario(values: list[int | None]) -> int | None:
    if any(item is None for item in values):
        return None
    return sum(item for item in values if item is not None)


def _break_even(monthly_fixed_base: int | None, margin_bps: int | None) -> int | None:
    if monthly_fixed_base is None or margin_bps is None:
        return None
    return int(
        (Decimal(monthly_fixed_base) * Decimal(10_000) / Decimal(margin_bps)).to_integral_value(
            rounding=ROUND_CEILING
        )
    )


def _required_orders(
    break_even: int | None,
    *,
    operating_days: int | None,
    average_ticket: int | None,
) -> Decimal | None:
    if break_even is None or operating_days is None or average_ticket is None:
        return None
    return (Decimal(break_even) / Decimal(operating_days) / Decimal(average_ticket)).quantize(
        Decimal("0.01"),
        rounding=ROUND_CEILING,
    )
