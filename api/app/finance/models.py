from decimal import Decimal
from enum import StrEnum

from pydantic import Field, model_validator

from app.domain.models import BorrowingIntent, StrictModel


class ValueProvenance(StrEnum):
    FACT = "FACT"
    USER_INPUT = "USER_INPUT"
    BENCHMARK = "BENCHMARK"
    ASSUMPTION = "ASSUMPTION"
    DERIVED = "DERIVED"
    UNKNOWN = "UNKNOWN"


class CostCategory(StrEnum):
    DEPOSIT = "DEPOSIT"
    ACQUISITION_OR_PREMIUM = "ACQUISITION_OR_PREMIUM"
    CONSTRUCTION = "CONSTRUCTION"
    EQUIPMENT = "EQUIPMENT"
    FRANCHISE_INITIAL_FEES = "FRANCHISE_INITIAL_FEES"
    PREOPENING = "PREOPENING"
    OPENING_INVENTORY = "OPENING_INVENTORY"
    CONTINGENCY = "CONTINGENCY"
    OPERATING_RESERVE = "OPERATING_RESERVE"
    MONTHLY_OCCUPANCY = "MONTHLY_OCCUPANCY"
    MONTHLY_LABOR = "MONTHLY_LABOR"
    MONTHLY_OTHER_FIXED = "MONTHLY_OTHER_FIXED"


INITIAL_COST_CATEGORIES = frozenset(
    {
        CostCategory.DEPOSIT,
        CostCategory.ACQUISITION_OR_PREMIUM,
        CostCategory.CONSTRUCTION,
        CostCategory.EQUIPMENT,
        CostCategory.FRANCHISE_INITIAL_FEES,
        CostCategory.PREOPENING,
        CostCategory.OPENING_INVENTORY,
        CostCategory.CONTINGENCY,
        CostCategory.OPERATING_RESERVE,
    }
)
MONTHLY_FIXED_COST_CATEGORIES = frozenset(
    {
        CostCategory.MONTHLY_OCCUPANCY,
        CostCategory.MONTHLY_LABOR,
        CostCategory.MONTHLY_OTHER_FIXED,
    }
)


class MoneyRange(StrictModel):
    low: int | None = Field(default=None, ge=0)
    base: int | None = Field(default=None, ge=0)
    high: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def ordered_known_values(self) -> "MoneyRange":
        known = [value for value in (self.low, self.base, self.high) if value is not None]
        if known != sorted(known):
            raise ValueError("Known MoneyRange scenarios must be nondecreasing")
        return self


class CostLine(StrictModel):
    field_id: str = Field(min_length=1)
    category: CostCategory
    amount: MoneyRange
    provenance: ValueProvenance
    evidence_ref: str | None = None

    @model_validator(mode="after")
    def provenance_matches_value(self) -> "CostLine":
        if self.provenance in {
            ValueProvenance.FACT,
            ValueProvenance.USER_INPUT,
            ValueProvenance.BENCHMARK,
        } and not self.evidence_ref:
            raise ValueError("Grounded cost line requires evidence_ref")
        values = (self.amount.low, self.amount.base, self.amount.high)
        if self.provenance == ValueProvenance.UNKNOWN and any(
            value is not None for value in values
        ):
            raise ValueError("UNKNOWN cost line cannot contain a numeric value")
        if self.provenance != ValueProvenance.UNKNOWN and all(
            value is None for value in values
        ):
            raise ValueError("Known cost provenance requires at least one numeric value")
        return self


class FinanceInput(StrictModel):
    initial_cost_lines: list[CostLine]
    monthly_fixed_cost_lines: list[CostLine]
    contribution_margin_bps: int | None = Field(default=None, ge=1, le=10_000)
    operating_days_per_month: int | None = Field(default=None, ge=1, le=31)
    average_ticket_krw: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def cost_categories_match_sections(self) -> "FinanceInput":
        if any(line.category not in INITIAL_COST_CATEGORIES for line in self.initial_cost_lines):
            raise ValueError("initial_cost_lines contains a monthly cost category")
        if any(
            line.category not in MONTHLY_FIXED_COST_CATEGORIES
            for line in self.monthly_fixed_cost_lines
        ):
            raise ValueError("monthly_fixed_cost_lines contains an initial cost category")
        return self


class FinanceResult(StrictModel):
    initial_cash: MoneyRange
    monthly_fixed_cost: MoneyRange
    break_even_monthly_sales_krw: int | None
    required_daily_orders: Decimal | None
    unknown_cost_fields: list[str]


class CapitalGateStatus(StrEnum):
    PASS = "PASS"
    CONDITIONAL = "CONDITIONAL"
    FAIL = "FAIL"


class CapitalGateResult(StrictModel):
    status: CapitalGateStatus
    reason_code: str
    minimum_required_reduction_krw: int | None = Field(default=None, ge=0)


class CapitalGateInput(StrictModel):
    own_funds_krw: int = Field(ge=0)
    borrowing_intent: BorrowingIntent
    initial_cash: MoneyRange
