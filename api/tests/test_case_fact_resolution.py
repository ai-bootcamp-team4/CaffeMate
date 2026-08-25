from datetime import UTC, datetime

from app.finance.case_facts import (
    CaseFactRecord,
    CaseFactResolver,
    FinancialInputResolver,
    PropertyContext,
)
from app.finance.models import (
    CostCategory,
    CostLine,
    MoneyRange,
    ValueProvenance,
    VariableCostRateLine,
)


def fact(
    claim_id: str,
    claim_type: str,
    value: int | str,
    *,
    document_type: str,
    revision: str,
    filename: str,
    unit: str | None = "KRW",
) -> CaseFactRecord:
    return CaseFactRecord(
        claim_id=claim_id,
        source_id="independent-small-takeout-v1",
        claim_type=claim_type,
        value=value,
        unit=unit,
        materiality="HIGH",
        document_type=document_type,
        document_id=f"document-{revision}",
        document_revision_id=revision,
        original_filename=filename,
        anchor={"document_revision_id": revision, "page_index": 0},
        created_at=datetime(2026, 8, 25, 3, 0, tzinfo=UTC),
    )


def assumption(category: CostCategory, value: int) -> CostLine:
    return CostLine(
        field_id=category.value,
        category=category,
        amount=MoneyRange(low=value, base=value, high=value),
        provenance=ValueProvenance.ASSUMPTION,
        evidence_ref="declared-assumption:test",
    )


def test_case_fact_resolver_maps_lease_and_quote_documents_to_cost_categories() -> None:
    records = [
        fact(
            "lease-deposit",
            "LEASE_DEPOSIT",
            30_000_000,
            document_type="COMMERCIAL_LEASE",
            revision="lease-r1",
            filename="lease.pdf",
        ),
        fact(
            "lease-rent",
            "MONTHLY_RENT",
            2_200_000,
            document_type="COMMERCIAL_LEASE",
            revision="lease-r1",
            filename="lease.pdf",
        ),
        fact(
            "lease-management",
            "MANAGEMENT_FEE",
            200_000,
            document_type="COMMERCIAL_LEASE",
            revision="lease-r1",
            filename="lease.pdf",
        ),
        fact(
            "lease-key-money",
            "KEY_MONEY",
            10_000_000,
            document_type="COMMERCIAL_LEASE",
            revision="lease-r1",
            filename="lease.pdf",
        ),
        fact(
            "interior-total",
            "QUOTE_TOTAL",
            26_400_000,
            document_type="INTERIOR_QUOTE",
            revision="interior-r1",
            filename="interior.pdf",
        ),
        fact(
            "equipment-total",
            "QUOTE_TOTAL",
            19_800_000,
            document_type="EQUIPMENT_QUOTE",
            revision="equipment-r1",
            filename="equipment.pdf",
        ),
    ]

    resolved = CaseFactResolver().resolve(records=records, open_conflict_keys=set())
    by_category = {value.category: value for value in resolved.overrides}

    assert by_category[CostCategory.DEPOSIT].amount.base == 30_000_000
    assert by_category[CostCategory.ACQUISITION_OR_PREMIUM].amount.base == 10_000_000
    assert by_category[CostCategory.MONTHLY_OCCUPANCY].amount.base == 2_400_000
    assert by_category[CostCategory.CONSTRUCTION].amount.base == 26_400_000
    assert by_category[CostCategory.EQUIPMENT].amount.base == 19_800_000
    assert all(
        value.provenance == ValueProvenance.USER_INPUT for value in by_category.values()
    )
    assert resolved.sources[by_category[CostCategory.CONSTRUCTION].evidence_ref][
        "source_title"
    ] == "interior.pdf"


def test_open_finance_conflict_becomes_unknown_instead_of_choosing_latest_claim() -> None:
    records = [
        fact(
            "interior-old",
            "QUOTE_TOTAL",
            20_000_000,
            document_type="INTERIOR_QUOTE",
            revision="interior-r1",
            filename="old.pdf",
        ),
        fact(
            "interior-new",
            "QUOTE_TOTAL",
            26_400_000,
            document_type="INTERIOR_QUOTE",
            revision="interior-r2",
            filename="new.pdf",
        ),
    ]

    resolved = CaseFactResolver().resolve(
        records=records,
        open_conflict_keys={("INTERIOR_QUOTE", "QUOTE_TOTAL")},
    )
    override = next(
        value for value in resolved.overrides if value.category == CostCategory.CONSTRUCTION
    )

    assert override.provenance == ValueProvenance.UNKNOWN
    assert override.amount == MoneyRange(low=None, base=None, high=None)
    assert override.evidence_ref is None


def test_partial_lease_occupancy_is_unknown_not_blended_with_seed_assumption() -> None:
    records = [
        fact(
            "lease-rent",
            "MONTHLY_RENT",
            2_200_000,
            document_type="COMMERCIAL_LEASE",
            revision="lease-r1",
            filename="lease.pdf",
        )
    ]

    resolved = CaseFactResolver().resolve(records=records, open_conflict_keys=set())
    override = next(
        value
        for value in resolved.overrides
        if value.category == CostCategory.MONTHLY_OCCUPANCY
    )

    assert override.provenance == ValueProvenance.UNKNOWN
    assert override.amount.base is None


def test_financial_input_resolver_prefers_property_terms_over_document_lease() -> None:
    records = [
        fact(
            "lease-deposit",
            "LEASE_DEPOSIT",
            35_000_000,
            document_type="COMMERCIAL_LEASE",
            revision="lease-r1",
            filename="lease.pdf",
        )
    ]
    case_resolution = CaseFactResolver().resolve(records=records, open_conflict_keys=set())
    resolver = FinancialInputResolver(
        property_context=PropertyContext(
            property_input_id="property-1",
            source_id="independent-small-takeout-v1",
            address="서울특별시 마포구 공덕동 실제 점포",
            area_sqm=33,
            floor="1층",
            deposit_krw=30_000_000,
            monthly_rent_krw=2_000_000,
            management_fee_krw=200_000,
            key_money_krw=5_000_000,
        ),
        case_resolution=case_resolution,
    )

    line = resolver.resolve_cost_line(
        source_id="independent-small-takeout-v1",
        fallback=assumption(CostCategory.DEPOSIT, 20_000_000),
    )

    assert line.amount.base == 30_000_000
    assert line.provenance == ValueProvenance.USER_INPUT
    assert line.evidence_ref == "property-input:property-1"


def test_financial_input_resolver_replaces_quote_assumption_for_matching_candidate_only() -> None:
    records = [
        fact(
            "interior-total",
            "QUOTE_TOTAL",
            26_400_000,
            document_type="INTERIOR_QUOTE",
            revision="interior-r1",
            filename="interior.pdf",
        )
    ]
    case_resolution = CaseFactResolver().resolve(records=records, open_conflict_keys=set())
    resolver = FinancialInputResolver(case_resolution=case_resolution)

    applied = resolver.resolve_cost_line(
        source_id="independent-small-takeout-v1",
        fallback=assumption(CostCategory.CONSTRUCTION, 18_000_000),
    )
    other_candidate = resolver.resolve_cost_line(
        source_id="independent-balanced-v1",
        fallback=assumption(CostCategory.CONSTRUCTION, 24_000_000),
    )

    assert applied.amount.base == 26_400_000
    assert applied.provenance == ValueProvenance.USER_INPUT
    assert other_candidate.amount.base == 24_000_000
    assert other_candidate.provenance == ValueProvenance.ASSUMPTION


def test_franchise_agreement_percentage_royalty_becomes_user_confirmed_variable_rate() -> None:
    records = [
        CaseFactRecord(
            claim_id="royalty-1",
            source_id="kr-ediya-coffee",
            claim_type="ROYALTY",
            value=3.0,
            unit="%",
            materiality="HIGH",
            document_type="FRANCHISE_AGREEMENT",
            document_id="document-royalty-r1",
            document_revision_id="royalty-r1",
            original_filename="franchise-agreement.pdf",
            anchor={"document_revision_id": "royalty-r1", "page_index": 4},
            created_at=datetime(2026, 8, 25, 3, 0, tzinfo=UTC),
        )
    ]

    case_resolution = CaseFactResolver().resolve(records=records, open_conflict_keys=set())
    resolver = FinancialInputResolver(case_resolution=case_resolution)
    line = resolver.resolve_variable_cost_rate(
        source_id="kr-ediya-coffee",
        fallback=VariableCostRateLine(
            field_id="SALES_ROYALTY",
            rate_bps=150,
            provenance=ValueProvenance.FACT,
            evidence_ref="official-rate",
        ),
    )

    assert line.rate_bps == 300
    assert line.provenance == ValueProvenance.USER_INPUT
    assert line.evidence_ref == "document-revision:royalty-r1"
    assert case_resolution.sources[line.evidence_ref]["source_title"] == (
        "franchise-agreement.pdf"
    )


def test_non_numeric_franchise_royalty_fails_closed_instead_of_lower_precedence_rate() -> None:
    records = [
        CaseFactRecord(
            claim_id="royalty-ambiguous",
            source_id="kr-ediya-coffee",
            claim_type="ROYALTY",
            value="매월 본사가 고지하는 기준에 따름",
            unit=None,
            materiality="HIGH",
            document_type="FRANCHISE_AGREEMENT",
            document_id="document-royalty-r2",
            document_revision_id="royalty-r2",
            original_filename="franchise-agreement.pdf",
            anchor={"document_revision_id": "royalty-r2", "page_index": 5},
            created_at=datetime(2026, 8, 25, 3, 0, tzinfo=UTC),
        )
    ]

    case_resolution = CaseFactResolver().resolve(records=records, open_conflict_keys=set())
    resolver = FinancialInputResolver(case_resolution=case_resolution)
    line = resolver.resolve_variable_cost_rate(
        source_id="kr-ediya-coffee",
        fallback=VariableCostRateLine(
            field_id="SALES_ROYALTY",
            rate_bps=150,
            provenance=ValueProvenance.FACT,
            evidence_ref="official-rate",
        ),
    )

    assert line.rate_bps is None
    assert line.provenance == ValueProvenance.UNKNOWN
    assert line.evidence_ref is None


def test_commercial_lease_takes_precedence_over_property_listing_for_same_cost() -> None:
    records = [
        fact(
            "listing-deposit",
            "LEASE_DEPOSIT",
            25_000_000,
            document_type="PROPERTY_LISTING",
            revision="listing-r1",
            filename="listing.pdf",
        ),
        fact(
            "lease-deposit",
            "LEASE_DEPOSIT",
            30_000_000,
            document_type="COMMERCIAL_LEASE",
            revision="lease-r1",
            filename="lease.pdf",
        ),
    ]

    resolved = CaseFactResolver().resolve(records=records, open_conflict_keys=set())
    deposit = next(
        value for value in resolved.overrides if value.category == CostCategory.DEPOSIT
    )

    assert deposit.amount.base == 30_000_000
    assert deposit.evidence_ref == "document-revision:lease-r1"


def test_commercial_lease_occupancy_replaces_property_listing_occupancy() -> None:
    records = [
        fact(
            "listing-rent",
            "MONTHLY_RENT",
            1_800_000,
            document_type="PROPERTY_LISTING",
            revision="listing-r1",
            filename="listing.pdf",
        ),
        fact(
            "listing-management",
            "MANAGEMENT_FEE",
            200_000,
            document_type="PROPERTY_LISTING",
            revision="listing-r1",
            filename="listing.pdf",
        ),
        fact(
            "lease-rent",
            "MONTHLY_RENT",
            2_200_000,
            document_type="COMMERCIAL_LEASE",
            revision="lease-r1",
            filename="lease.pdf",
        ),
        fact(
            "lease-management",
            "MANAGEMENT_FEE",
            200_000,
            document_type="COMMERCIAL_LEASE",
            revision="lease-r1",
            filename="lease.pdf",
        ),
    ]

    resolved = CaseFactResolver().resolve(records=records, open_conflict_keys=set())
    occupancy = next(
        value
        for value in resolved.overrides
        if value.category == CostCategory.MONTHLY_OCCUPANCY
    )

    assert occupancy.amount.base == 2_400_000
    assert occupancy.evidence_ref == "document-revision:lease-r1"


def test_interior_quote_conflict_does_not_block_equipment_quote() -> None:
    records = [
        fact(
            "interior-old",
            "QUOTE_TOTAL",
            20_000_000,
            document_type="INTERIOR_QUOTE",
            revision="interior-r1",
            filename="interior-old.pdf",
        ),
        fact(
            "interior-new",
            "QUOTE_TOTAL",
            26_400_000,
            document_type="INTERIOR_QUOTE",
            revision="interior-r2",
            filename="interior-new.pdf",
        ),
        fact(
            "equipment",
            "QUOTE_TOTAL",
            19_800_000,
            document_type="EQUIPMENT_QUOTE",
            revision="equipment-r1",
            filename="equipment.pdf",
        ),
    ]

    resolved = CaseFactResolver().resolve(
        records=records,
        open_conflict_keys={("INTERIOR_QUOTE", "QUOTE_TOTAL")},
    )
    by_category = {value.category: value for value in resolved.overrides}

    assert by_category[CostCategory.CONSTRUCTION].provenance == ValueProvenance.UNKNOWN
    assert by_category[CostCategory.EQUIPMENT].amount.base == 19_800_000
    assert by_category[CostCategory.EQUIPMENT].provenance == ValueProvenance.USER_INPUT


def test_commercial_lease_occupancy_takes_precedence_over_listing_terms() -> None:
    records = [
        fact(
            "listing-rent",
            "MONTHLY_RENT",
            1_900_000,
            document_type="PROPERTY_LISTING",
            revision="listing-r1",
            filename="listing.pdf",
        ),
        fact(
            "listing-fee",
            "MANAGEMENT_FEE",
            100_000,
            document_type="PROPERTY_LISTING",
            revision="listing-r1",
            filename="listing.pdf",
        ),
        fact(
            "lease-rent",
            "MONTHLY_RENT",
            2_200_000,
            document_type="COMMERCIAL_LEASE",
            revision="lease-r1",
            filename="lease.pdf",
        ),
        fact(
            "lease-fee",
            "MANAGEMENT_FEE",
            200_000,
            document_type="COMMERCIAL_LEASE",
            revision="lease-r1",
            filename="lease.pdf",
        ),
    ]

    resolved = CaseFactResolver().resolve(records=records, open_conflict_keys=set())
    occupancy = next(
        value
        for value in resolved.overrides
        if value.category == CostCategory.MONTHLY_OCCUPANCY
    )

    assert occupancy.amount.base == 2_400_000
    assert occupancy.evidence_ref == "document-revision:lease-r1"
