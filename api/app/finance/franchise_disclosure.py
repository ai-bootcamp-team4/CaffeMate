"""Resolve accepted FTC disclosure facts into authoritative franchise finance inputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.finance.models import CostCategory, CostLine, MoneyRange, ValueProvenance

REQUIRED_INITIAL_FEE_FIELDS = frozenset(
    {
        "FRANCHISE_FEE",
        "EDUCATION_FEE",
        "FRANCHISEE_DEPOSIT",
        "OTHER_INITIAL_FEE",
        "FRANCHISE_INITIAL_FEE_TOTAL",
    }
)


@dataclass(frozen=True)
class ResolvedFranchiseDisclosureCost:
    source_id: str
    amount: MoneyRange
    evidence_ref: str

    def as_cost_line(self) -> CostLine:
        return CostLine(
            field_id=CostCategory.FRANCHISE_INITIAL_FEES.value,
            category=CostCategory.FRANCHISE_INITIAL_FEES,
            amount=self.amount,
            provenance=ValueProvenance.FACT,
            evidence_ref=self.evidence_ref,
        )


@dataclass(frozen=True)
class FranchiseDisclosureResolution:
    overrides: tuple[ResolvedFranchiseDisclosureCost, ...] = ()
    sources: dict[str, dict[str, object]] = field(default_factory=dict)
    disclosure_evidence_refs: dict[str, list[str]] = field(default_factory=dict)


def resolve_franchise_disclosures(
    *,
    structured_results: list[dict[str, Any]],
    accepted_evidence_ids: set[str],
    eligible_brand_ids: set[str],
) -> FranchiseDisclosureResolution:
    groups: dict[tuple[str, str, int], dict[str, dict[str, Any]]] = {}
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for content in structured_results:
        if content.get("tool_name") != "get_franchise_disclosure" or content.get("status") not in {
            "OK",
            "PARTIAL",
        }:
            continue
        for record in content.get("evidence_records", []):
            if isinstance(record, dict) and isinstance(record.get("evidence_id"), str):
                evidence_by_id[str(record["evidence_id"])] = record
        for item in content.get("data", []):
            if not isinstance(item, dict):
                continue
            evidence_id = item.get("evidence_id")
            brand_id = item.get("brand_id")
            version = item.get("source_version")
            reporting_year = item.get("reporting_year")
            field_name = item.get("field")
            if (
                not isinstance(evidence_id, str)
                or evidence_id not in accepted_evidence_ids
                or not isinstance(brand_id, str)
                or brand_id not in eligible_brand_ids
                or not isinstance(version, str)
                or not isinstance(reporting_year, int)
                or isinstance(reporting_year, bool)
                or not isinstance(field_name, str)
            ):
                continue
            groups.setdefault((brand_id, version, reporting_year), {})[field_name] = item

    overrides: list[ResolvedFranchiseDisclosureCost] = []
    sources: dict[str, dict[str, object]] = {}
    refs_by_brand: dict[str, list[str]] = {}
    for (brand_id, version, reporting_year), items in sorted(groups.items()):
        if not REQUIRED_INITIAL_FEE_FIELDS.issubset(items):
            continue
        amounts: dict[str, int] = {}
        valid = True
        for field_name in REQUIRED_INITIAL_FEE_FIELDS:
            item = items[field_name]
            value = item.get("value")
            amount = value.get("value") if isinstance(value, dict) else None
            if (
                item.get("unit") != "KRW"
                or not isinstance(amount, int)
                or isinstance(amount, bool)
                or amount < 0
            ):
                valid = False
                break
            amounts[field_name] = amount
        if not valid:
            continue
        component_total = sum(
            amounts[field_name]
            for field_name in (
                "FRANCHISE_FEE",
                "EDUCATION_FEE",
                "FRANCHISEE_DEPOSIT",
                "OTHER_INITIAL_FEE",
            )
        )
        official_total = amounts["FRANCHISE_INITIAL_FEE_TOTAL"]
        if component_total != official_total:
            continue
        total_item = items["FRANCHISE_INITIAL_FEE_TOTAL"]
        total_evidence_id = total_item.get("evidence_id")
        if not isinstance(total_evidence_id, str):
            continue
        evidence_refs = sorted(
            {
                str(item["evidence_id"])
                for item in items.values()
                if isinstance(item.get("evidence_id"), str)
            }
        )
        total_evidence = evidence_by_id.get(total_evidence_id, {})
        source = total_evidence.get("source")
        anchor = total_evidence.get("original_anchor")
        if not isinstance(source, dict):
            source = {}
        if not isinstance(anchor, dict):
            anchor = {}
        sources[total_evidence_id] = {
            "source_title": source.get("title"),
            "source_ref": source.get("source_ref"),
            "data_date": source.get("published_or_data_date"),
            "geographic_scope": {
                "scope_type": "NATIONAL",
                "scope_id": "KR",
                "boundary_version": None,
            },
            "source_anchor": anchor.get("locator"),
            "derivation": {
                "formula_code": "FTC_INITIAL_FEE_COMPONENT_SUM_V1",
                "inputs": {
                    field_name: amounts[field_name]
                    for field_name in sorted(REQUIRED_INITIAL_FEE_FIELDS)
                },
                "source_version": version,
                "reporting_year": reporting_year,
                "constituent_evidence_refs": evidence_refs,
            },
        }
        overrides.append(
            ResolvedFranchiseDisclosureCost(
                source_id=brand_id,
                amount=MoneyRange(low=official_total, base=official_total, high=official_total),
                evidence_ref=total_evidence_id,
            )
        )
        refs_by_brand[brand_id] = evidence_refs
    return FranchiseDisclosureResolution(
        overrides=tuple(overrides),
        sources=sources,
        disclosure_evidence_refs=refs_by_brand,
    )