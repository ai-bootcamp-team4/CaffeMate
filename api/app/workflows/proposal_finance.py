"""Deterministic proposal finance inputs and case-specific cost overrides."""

from typing import Any

from app.finance.case_facts import FinancialInputResolver, PropertyContext
from app.finance.models import CostCategory, CostLine, MoneyRange, ValueProvenance

FRANCHISE_BENCHMARK_COSTS = {
    CostCategory.DEPOSIT: MoneyRange(low=20_000_000, base=35_000_000, high=60_000_000),
    CostCategory.ACQUISITION_OR_PREMIUM: MoneyRange(
        low=0,
        base=10_000_000,
        high=30_000_000,
    ),
    CostCategory.CONSTRUCTION: MoneyRange(
        low=10_000_000,
        base=20_000_000,
        high=35_000_000,
    ),
    CostCategory.EQUIPMENT: MoneyRange(low=18_000_000, base=26_000_000, high=38_000_000),
    CostCategory.PREOPENING: MoneyRange(low=2_000_000, base=4_000_000, high=6_000_000),
    CostCategory.OPENING_INVENTORY: MoneyRange(
        low=1_500_000,
        base=2_500_000,
        high=4_000_000,
    ),
    CostCategory.CONTINGENCY: MoneyRange(low=6_000_000, base=10_000_000, high=16_000_000),
    CostCategory.OPERATING_RESERVE: MoneyRange(
        low=12_000_000,
        base=20_000_000,
        high=30_000_000,
    ),
    CostCategory.MONTHLY_OCCUPANCY: MoneyRange(
        low=2_500_000,
        base=4_000_000,
        high=6_500_000,
    ),
    CostCategory.MONTHLY_LABOR: MoneyRange(
        low=1_000_000,
        base=2_500_000,
        high=5_000_000,
    ),
    CostCategory.MONTHLY_OTHER_FIXED: MoneyRange(
        low=700_000,
        base=1_100_000,
        high=1_800_000,
    ),
}


def independent_cost_line(
    model_id: str,
    category: CostCategory,
    amount: MoneyRange,
    resolver: FinancialInputResolver,
) -> CostLine:
    fallback = CostLine(
        field_id=category.value,
        category=category,
        amount=amount,
        provenance=ValueProvenance.ASSUMPTION,
        evidence_ref=f"declared-assumption:{model_id}",
    )
    return resolver.resolve_cost_line(source_id=model_id, fallback=fallback)


def franchise_cost_line(
    *,
    brand_id: str,
    category: CostCategory,
    finance_profile: dict[str, Any],
    resolver: FinancialInputResolver,
) -> CostLine:
    fallback = _franchise_fallback_line(
        brand_id=brand_id,
        category=category,
        finance_profile=finance_profile,
    )
    return resolver.resolve_cost_line(source_id=brand_id, fallback=fallback)


def _franchise_fallback_line(
    *,
    brand_id: str,
    category: CostCategory,
    finance_profile: dict[str, Any],
) -> CostLine:
    if category == CostCategory.FRANCHISE_INITIAL_FEES:
        value = finance_profile.get("known_initial_cost_range_krw")
        refs = [
            ref
            for ref in finance_profile.get("evidence_refs", [])
            if isinstance(ref, str)
        ]
        if isinstance(value, dict) and refs:
            return CostLine(
                field_id=category.value,
                category=category,
                amount=MoneyRange.model_validate(value),
                provenance=ValueProvenance.FACT,
                evidence_ref=refs[0],
            )
        return CostLine(
            field_id=category.value,
            category=category,
            amount=MoneyRange(low=None, base=None, high=None),
            provenance=ValueProvenance.UNKNOWN,
        )
    if category not in franchise_assumed_categories(finance_profile):
        return CostLine(
            field_id=category.value,
            category=category,
            amount=MoneyRange(low=0, base=0, high=0),
            provenance=ValueProvenance.DERIVED,
        )
    assumption = FRANCHISE_BENCHMARK_COSTS[category]
    if category == CostCategory.MONTHLY_OTHER_FIXED:
        royalty = finance_profile.get("monthly_royalty_krw")
        if isinstance(royalty, int):
            assumption = MoneyRange(
                low=(assumption.low or 0) + royalty,
                base=(assumption.base or 0) + royalty,
                high=(assumption.high or 0) + royalty,
            )
    return CostLine(
        field_id=category.value,
        category=category,
        amount=assumption,
        provenance=ValueProvenance.ASSUMPTION,
        evidence_ref=f"declared-assumption:{brand_id}:2026-08-24",
    )


def franchise_assumed_categories(finance_profile: dict[str, Any]) -> set[CostCategory]:
    values = {
        value
        for value in finance_profile.get("missing_costs", [])
        if isinstance(value, str)
    }
    assumed = {
        CostCategory.DEPOSIT,
        CostCategory.ACQUISITION_OR_PREMIUM,
        CostCategory.PREOPENING,
        CostCategory.CONTINGENCY,
        CostCategory.OPERATING_RESERVE,
        CostCategory.MONTHLY_OCCUPANCY,
        CostCategory.MONTHLY_LABOR,
        CostCategory.MONTHLY_OTHER_FIXED,
    }
    for category in (
        CostCategory.CONSTRUCTION,
        CostCategory.EQUIPMENT,
        CostCategory.OPENING_INVENTORY,
    ):
        if category.value in values:
            assumed.add(category)
    return assumed


def cost_refs(lines: list[CostLine]) -> list[str]:
    return sorted(
        {
            line.evidence_ref
            for line in lines
            if isinstance(line.evidence_ref, str) and line.evidence_ref
        }
    )


def property_adjusted_fields(
    source_id: str,
    value: PropertyContext | None,
) -> list[str]:
    if value is None or value.source_id != source_id:
        return []
    fields = [
        "property.deposit_krw",
        "property.management_fee_krw",
        "property.monthly_rent_krw",
    ]
    if value.key_money_krw is not None:
        fields.append("property.key_money_krw")
    return fields
