"""Project deterministic independent-cafe finance inputs for Proposal Agent context."""

from typing import Any

from app.candidates.seed_registry import IndependentSeedDefinition
from app.domain.errors import ContractValidationError
from app.finance.calculator import calculate_finance
from app.finance.labor_benchmark import (
    MinimumWageReference,
    resolve_seed_labor_benchmarks,
)
from app.finance.models import (
    INITIAL_COST_CATEGORIES,
    MONTHLY_FIXED_COST_CATEGORIES,
    CostCategory,
    CostLine,
    FinanceInput,
    MoneyRange,
    ValueProvenance,
)


def independent_finance_snapshot(
    seed: IndependentSeedDefinition,
    minimum_wage_references: list[MinimumWageReference] | None = None,
) -> dict[str, Any]:
    """Expose registered inputs and Control API math without delegating authority."""

    profile = seed.finance_profile
    if profile is None:
        raise ContractValidationError(
            f"Independent model has no registered finance profile: {seed.model_id}"
        )
    initial_lines = [
        CostLine(
            field_id=category.value,
            category=category,
            amount=profile.cost_ranges[category],
            provenance=ValueProvenance.ASSUMPTION,
        )
        for category in sorted(INITIAL_COST_CATEGORIES, key=lambda value: value.value)
        if category != CostCategory.FRANCHISE_INITIAL_FEES
    ]
    initial_lines.append(
        CostLine(
            field_id=CostCategory.FRANCHISE_INITIAL_FEES.value,
            category=CostCategory.FRANCHISE_INITIAL_FEES,
            amount=MoneyRange(low=0, base=0, high=0),
            provenance=ValueProvenance.DERIVED,
        )
    )
    labor_resolution = resolve_seed_labor_benchmarks(
        seeds=[
            (
                seed.model_id,
                profile.paid_staff_fte,
                profile.cost_ranges[CostCategory.MONTHLY_LABOR],
            )
        ],
        references=minimum_wage_references or [],
    )
    labor_override = (
        labor_resolution.overrides[0].as_cost_line()
        if labor_resolution.overrides
        else None
    )
    monthly_lines: list[CostLine] = []
    for category in sorted(
        MONTHLY_FIXED_COST_CATEGORIES,
        key=lambda value: value.value,
    ):
        if category == CostCategory.MONTHLY_LABOR and labor_override is not None:
            monthly_lines.append(labor_override)
            continue
        monthly_lines.append(
            CostLine(
                field_id=category.value,
                category=category,
                amount=profile.cost_ranges[category],
                provenance=ValueProvenance.ASSUMPTION,
            )
        )
    finance = calculate_finance(
        FinanceInput(
            initial_cost_lines=initial_lines,
            monthly_fixed_cost_lines=monthly_lines,
            contribution_margin_bps=profile.contribution_margin_bps,
            operating_days_per_month=profile.operating_days_per_month,
            average_ticket_krw=profile.average_ticket_krw,
        )
    )
    return {
        "initial_cash_krw": finance.initial_cash.model_dump(mode="json"),
        "monthly_fixed_cost_krw": finance.monthly_fixed_cost.model_dump(mode="json"),
        "contribution_margin_bps": profile.contribution_margin_bps,
        "operating_days_per_month": profile.operating_days_per_month,
        "average_ticket_krw": profile.average_ticket_krw,
        "break_even_monthly_sales_krw": finance.break_even_monthly_sales_krw,
        "required_daily_orders": (
            float(finance.required_daily_orders)
            if finance.required_daily_orders is not None
            else None
        ),
    }