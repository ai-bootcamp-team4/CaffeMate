"""Resolve user-confirmed case facts into authoritative finance overrides."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.finance.models import CostCategory, CostLine, MoneyRange, ValueProvenance


@dataclass(frozen=True)
class PropertyCostOverride:
    """Actual property terms that replace less-specific case assumptions."""

    property_input_id: str
    source_id: str
    deposit_krw: int
    monthly_rent_krw: int
    management_fee_krw: int
    key_money_krw: int | None

    @property
    def evidence_ref(self) -> str:
        return f"property-input:{self.property_input_id}"


@dataclass(frozen=True)
class CaseFactRecord:
    claim_id: str
    source_id: str
    claim_type: str
    value: int | float | str | bool
    unit: str | None
    materiality: str
    document_type: str
    document_id: str
    document_revision_id: str
    original_filename: str | None
    anchor: dict[str, Any] | None
    created_at: datetime

    @property
    def conflict_key(self) -> tuple[str, str]:
        return self.document_type, self.claim_type


@dataclass(frozen=True)
class ResolvedCaseCost:
    source_id: str
    category: CostCategory
    amount: MoneyRange
    provenance: ValueProvenance
    evidence_ref: str | None
    claim_ids: tuple[str, ...] = ()

    def as_cost_line(self) -> CostLine:
        return CostLine(
            field_id=self.category.value,
            category=self.category,
            amount=self.amount,
            provenance=self.provenance,
            evidence_ref=self.evidence_ref,
        )


@dataclass(frozen=True)
class CaseFactResolution:
    overrides: tuple[ResolvedCaseCost, ...] = ()
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)


class CaseFactResolver:
    """Map only semantically complete, numeric confirmed document facts to finance."""

    def resolve(
        self,
        *,
        records: list[CaseFactRecord],
        open_conflict_keys: set[tuple[str, str]],
    ) -> CaseFactResolution:
        overrides: list[ResolvedCaseCost] = []
        sources: dict[str, dict[str, Any]] = {}
        source_ids = sorted({record.source_id for record in records})
        for source_id in source_ids:
            scoped = [record for record in records if record.source_id == source_id]
            for document_type, claim_type, category in (
                ("PROPERTY_LISTING", "LEASE_DEPOSIT", CostCategory.DEPOSIT),
                ("COMMERCIAL_LEASE", "LEASE_DEPOSIT", CostCategory.DEPOSIT),
                ("PROPERTY_LISTING", "KEY_MONEY", CostCategory.ACQUISITION_OR_PREMIUM),
                ("COMMERCIAL_LEASE", "KEY_MONEY", CostCategory.ACQUISITION_OR_PREMIUM),
                ("INTERIOR_QUOTE", "QUOTE_TOTAL", CostCategory.CONSTRUCTION),
                ("EQUIPMENT_QUOTE", "QUOTE_TOTAL", CostCategory.EQUIPMENT),
            ):
                resolved = self._single_cost(
                    source_id=source_id,
                    records=scoped,
                    document_type=document_type,
                    claim_type=claim_type,
                    category=category,
                    open_conflict_keys=open_conflict_keys,
                    sources=sources,
                )
                if resolved is not None:
                    self._replace_category(overrides, resolved)
            occupancy = self._occupancy_cost(
                source_id=source_id,
                records=scoped,
                open_conflict_keys=open_conflict_keys,
                sources=sources,
            )
            if occupancy is not None:
                self._replace_category(overrides, occupancy)
        return CaseFactResolution(overrides=tuple(overrides), sources=sources)

    @staticmethod
    def _replace_category(
        overrides: list[ResolvedCaseCost],
        resolved: ResolvedCaseCost,
    ) -> None:
        # A later document family in the explicit precedence order replaces an
        # earlier equivalent category only when both are independently resolvable.
        overrides[:] = [
            value
            for value in overrides
            if not (
                value.source_id == resolved.source_id
                and value.category == resolved.category
            )
        ]
        overrides.append(resolved)

    def _single_cost(
        self,
        *,
        source_id: str,
        records: list[CaseFactRecord],
        document_type: str,
        claim_type: str,
        category: CostCategory,
        open_conflict_keys: set[tuple[str, str]],
        sources: dict[str, dict[str, Any]],
    ) -> ResolvedCaseCost | None:
        matching = [
            record
            for record in records
            if record.document_type == document_type and record.claim_type == claim_type
        ]
        if not matching:
            return None
        if (document_type, claim_type) in open_conflict_keys:
            return self._unknown(source_id, category, matching)
        numeric = [(record, self._krw_value(record)) for record in matching]
        if any(value is None for _, value in numeric):
            return self._unknown(source_id, category, matching)
        distinct = {value for _, value in numeric if value is not None}
        if len(distinct) != 1:
            return self._unknown(source_id, category, matching)
        selected = max(matching, key=lambda value: (value.created_at, value.claim_id))
        amount = next(iter(distinct))
        assert amount is not None
        evidence_ref = self._document_evidence_ref(selected.document_revision_id)
        sources[evidence_ref] = self._source_metadata(selected)
        return ResolvedCaseCost(
            source_id=source_id,
            category=category,
            amount=MoneyRange(low=amount, base=amount, high=amount),
            provenance=ValueProvenance.USER_INPUT,
            evidence_ref=evidence_ref,
            claim_ids=tuple(sorted(record.claim_id for record in matching)),
        )

    def _occupancy_cost(
        self,
        *,
        source_id: str,
        records: list[CaseFactRecord],
        open_conflict_keys: set[tuple[str, str]],
        sources: dict[str, dict[str, Any]],
    ) -> ResolvedCaseCost | None:
        for document_type in ("COMMERCIAL_LEASE", "PROPERTY_LISTING"):
            scoped = [
                record
                for record in records
                if record.document_type == document_type
                and record.claim_type in {"MONTHLY_RENT", "MANAGEMENT_FEE"}
            ]
            if not scoped:
                continue
            if any(record.conflict_key in open_conflict_keys for record in scoped):
                return self._unknown(source_id, CostCategory.MONTHLY_OCCUPANCY, scoped)

            complete_revisions: list[tuple[CaseFactRecord, CaseFactRecord]] = []
            for revision_id in sorted({record.document_revision_id for record in scoped}):
                revision_records = [
                    record for record in scoped if record.document_revision_id == revision_id
                ]
                rents = [
                    record for record in revision_records if record.claim_type == "MONTHLY_RENT"
                ]
                fees = [
                    record
                    for record in revision_records
                    if record.claim_type == "MANAGEMENT_FEE"
                ]
                if len(rents) == 1 and len(fees) == 1:
                    complete_revisions.append((rents[0], fees[0]))
            if not complete_revisions:
                return self._unknown(source_id, CostCategory.MONTHLY_OCCUPANCY, scoped)

            totals: list[tuple[CaseFactRecord, CaseFactRecord, int]] = []
            for rent, fee in complete_revisions:
                rent_value = self._krw_value(rent)
                fee_value = self._krw_value(fee)
                if rent_value is None or fee_value is None:
                    return self._unknown(source_id, CostCategory.MONTHLY_OCCUPANCY, scoped)
                totals.append((rent, fee, rent_value + fee_value))
            if len({total for _, _, total in totals}) != 1:
                return self._unknown(source_id, CostCategory.MONTHLY_OCCUPANCY, scoped)

            rent, fee, total = max(
                totals,
                key=lambda value: (
                    max(value[0].created_at, value[1].created_at),
                    value[0].document_revision_id,
                ),
            )
            evidence_ref = self._document_evidence_ref(rent.document_revision_id)
            sources[evidence_ref] = self._source_metadata(rent, composite=True)
            return ResolvedCaseCost(
                source_id=source_id,
                category=CostCategory.MONTHLY_OCCUPANCY,
                amount=MoneyRange(low=total, base=total, high=total),
                provenance=ValueProvenance.USER_INPUT,
                evidence_ref=evidence_ref,
                claim_ids=tuple(sorted({rent.claim_id, fee.claim_id})),
            )
        return None

    @staticmethod
    def _krw_value(record: CaseFactRecord) -> int | None:
        if record.unit != "KRW" or isinstance(record.value, bool):
            return None
        if isinstance(record.value, int) and record.value >= 0:
            return record.value
        if isinstance(record.value, float) and record.value >= 0 and record.value.is_integer():
            return int(record.value)
        return None

    @staticmethod
    def _unknown(
        source_id: str,
        category: CostCategory,
        records: list[CaseFactRecord],
    ) -> ResolvedCaseCost:
        return ResolvedCaseCost(
            source_id=source_id,
            category=category,
            amount=MoneyRange(low=None, base=None, high=None),
            provenance=ValueProvenance.UNKNOWN,
            evidence_ref=None,
            claim_ids=tuple(sorted(record.claim_id for record in records)),
        )

    @staticmethod
    def _document_evidence_ref(document_revision_id: str) -> str:
        return f"document-revision:{document_revision_id}"

    @staticmethod
    def _source_metadata(
        record: CaseFactRecord,
        *,
        composite: bool = False,
    ) -> dict[str, Any]:
        page_index = record.anchor.get("page_index") if isinstance(record.anchor, dict) else None
        section_path = (
            record.anchor.get("section_path") if isinstance(record.anchor, dict) else None
        )
        anchor_parts = [record.document_revision_id]
        if isinstance(page_index, int):
            anchor_parts.append(f"page={page_index + 1}")
        if isinstance(section_path, str) and section_path:
            anchor_parts.append(f"section={section_path}")
        if composite:
            anchor_parts.append("fields=MONTHLY_RENT,MANAGEMENT_FEE")
        return {
            "source_title": record.original_filename or "사용자 확인 문서",
            "source_ref": None,
            "data_date": None,
            "geographic_scope": None,
            "source_anchor": "#".join(anchor_parts),
        }


class FinancialInputResolver:
    """Apply current case facts over less-specific proposal finance inputs."""

    def __init__(
        self,
        *,
        property_cost_override: PropertyCostOverride | None = None,
        case_resolution: CaseFactResolution | None = None,
    ) -> None:
        self._property = property_cost_override
        self._case = case_resolution or CaseFactResolution()
        self.decision_sources = dict(self._case.sources)
        self._case_by_key = {
            (value.source_id, value.category): value for value in self._case.overrides
        }

    def resolve_cost_line(self, *, source_id: str, fallback: CostLine) -> CostLine:
        property_line = self._property_line(source_id=source_id, category=fallback.category)
        if property_line is not None:
            return property_line
        case_value = self._case_by_key.get((source_id, fallback.category))
        if case_value is not None:
            return case_value.as_cost_line()
        return fallback

    def _property_line(
        self,
        *,
        source_id: str,
        category: CostCategory,
    ) -> CostLine | None:
        value = self._property
        if value is None or value.source_id != source_id:
            return None
        amount: int | None
        if category == CostCategory.DEPOSIT:
            amount = value.deposit_krw
        elif category == CostCategory.ACQUISITION_OR_PREMIUM:
            amount = value.key_money_krw
        elif category == CostCategory.MONTHLY_OCCUPANCY:
            amount = value.monthly_rent_krw + value.management_fee_krw
        else:
            return None
        if amount is None:
            return None
        return CostLine(
            field_id=category.value,
            category=category,
            amount=MoneyRange(low=amount, base=amount, high=amount),
            provenance=ValueProvenance.USER_INPUT,
            evidence_ref=value.evidence_ref,
        )
