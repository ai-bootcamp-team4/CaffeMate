"""Convert regional effective-rent benchmarks into provisional occupancy costs."""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from app.candidates.seed_registry import CommercialPropertyClass, SpaceProfile
from app.finance.models import CostCategory, CostLine, MoneyRange, ValueProvenance


@dataclass(frozen=True)
class PropertyRentBenchmark:
    evidence_ref: str
    region_code: str
    region_name: str
    property_class: CommercialPropertyClass
    period: str
    effective_rent_krw_per_sqm_month: int
    conversion_rate_bps: int
    coverage_status: str
    floor_basis: str
    source_title: str
    source_ref: str
    data_date: str


@dataclass(frozen=True)
class DerivedOccupancyBenchmark:
    amount: MoneyRange
    evidence_ref: str
    formula_code: str
    assumptions: dict[str, object]


@dataclass(frozen=True)
class ResolvedBenchmarkCost:
    source_id: str
    amount: MoneyRange
    evidence_ref: str

    def as_cost_line(self) -> CostLine:
        return CostLine(
            field_id=CostCategory.MONTHLY_OCCUPANCY.value,
            category=CostCategory.MONTHLY_OCCUPANCY,
            amount=self.amount,
            provenance=ValueProvenance.BENCHMARK,
            evidence_ref=self.evidence_ref,
        )


@dataclass(frozen=True)
class PropertyBenchmarkResolution:
    overrides: tuple[ResolvedBenchmarkCost, ...] = ()
    sources: dict[str, dict[str, object]] = field(default_factory=dict)


def resolve_seed_property_benchmarks(
    *,
    seeds: list[tuple[str, SpaceProfile, CommercialPropertyClass, int, MoneyRange]],
    benchmarks: list[PropertyRentBenchmark],
) -> PropertyBenchmarkResolution:
    by_class = {value.property_class: value for value in benchmarks}
    overrides: list[ResolvedBenchmarkCost] = []
    sources: dict[str, dict[str, object]] = {}
    for source_id, area_profile, property_class, management_fee_ratio_bps, deposit in seeds:
        benchmark = by_class.get(property_class)
        if benchmark is None:
            continue
        derived = derive_monthly_occupancy_benchmark(
            benchmark=benchmark,
            area_profile=area_profile,
            deposit_range=deposit,
            management_fee_ratio_bps=management_fee_ratio_bps,
        )
        derived_evidence_ref = f"{benchmark.evidence_ref}:derived:{source_id}"
        overrides.append(
            ResolvedBenchmarkCost(
                source_id=source_id,
                amount=derived.amount,
                evidence_ref=derived_evidence_ref,
            )
        )
        sources[derived_evidence_ref] = {
            "source_title": benchmark.source_title,
            "source_ref": benchmark.source_ref,
            "data_date": benchmark.data_date,
            "geographic_scope": {
                "scope_type": "REGION",
                "scope_id": benchmark.region_code,
                "boundary_version": None,
            },
            "source_anchor": (
                f"{benchmark.period}:{benchmark.region_code}:"
                f"{benchmark.property_class.value}:effective-rent"
            ),
            "derivation": {
                "formula_code": derived.formula_code,
                "inputs": {
                    "effective_rent_krw_per_sqm_month": (
                        benchmark.effective_rent_krw_per_sqm_month
                    ),
                    "conversion_rate_bps": benchmark.conversion_rate_bps,
                    **derived.assumptions,
                },
                "coverage_status": benchmark.coverage_status,
                "floor_basis": benchmark.floor_basis,
            },
        }
    return PropertyBenchmarkResolution(overrides=tuple(overrides), sources=sources)


def derive_monthly_occupancy_benchmark(
    *,
    benchmark: PropertyRentBenchmark,
    area_profile: SpaceProfile,
    deposit_range: MoneyRange,
    management_fee_ratio_bps: int,
) -> DerivedOccupancyBenchmark:
    """Translate REB effective rent into a cash-like monthly occupancy estimate.

    REB effective rent includes the opportunity cost of the deposit and excludes
    management fees. We therefore subtract the registered seed's base deposit
    carrying cost, then add an explicit registered management-fee assumption.
    The result is still a benchmark, never an actual lease term.
    """

    if deposit_range.base is None:
        raise ValueError("property benchmark conversion requires a base deposit")
    if benchmark.effective_rent_krw_per_sqm_month < 0:
        raise ValueError("effective rent must be non-negative")
    if not 0 <= benchmark.conversion_rate_bps <= 100_000:
        raise ValueError("conversion rate is outside supported range")
    if not 0 <= management_fee_ratio_bps <= 5_000:
        raise ValueError("management fee ratio is outside supported range")

    deposit_carry = (
        deposit_range.base * benchmark.conversion_rate_bps / 10_000 / 12
    )

    def occupancy(area_sqm: int) -> int:
        effective = benchmark.effective_rent_krw_per_sqm_month * area_sqm
        estimated_rent = max(0.0, effective - deposit_carry)
        with_management = estimated_rent * (10_000 + management_fee_ratio_bps) / 10_000
        return int(with_management)

    amount = MoneyRange(
        low=occupancy(area_profile.low),
        base=occupancy(area_profile.base),
        high=occupancy(area_profile.high),
    )
    return DerivedOccupancyBenchmark(
        amount=amount,
        evidence_ref=benchmark.evidence_ref,
        formula_code="REB_EFFECTIVE_RENT_TO_MONTHLY_OCCUPANCY_V1",
        assumptions={
            "area_sqm": area_profile.model_dump(mode="json"),
            "deposit_base_krw": deposit_range.base,
            "management_fee_ratio_bps": management_fee_ratio_bps,
        },
    )


def property_rent_benchmarks_from_mcp_results(
    structured_results: list[dict[str, Any]],
) -> list[PropertyRentBenchmark]:
    benchmarks: list[PropertyRentBenchmark] = []
    for content in structured_results:
        if content.get("tool_name") != "get_property_reference" or content.get("status") not in {
            "OK",
            "PARTIAL",
        }:
            continue
        evidence_by_id = {
            value.get("evidence_id"): value
            for value in content.get("evidence_records", [])
            if isinstance(value, dict) and isinstance(value.get("evidence_id"), str)
        }
        for item in content.get("data", []):
            if not isinstance(item, dict):
                continue
            evidence_id = item.get("evidence_id")
            evidence = evidence_by_id.get(evidence_id)
            source = evidence.get("source") if isinstance(evidence, dict) else None
            try:
                property_class = CommercialPropertyClass(str(item["property_class"]))
                effective_rent = int(item["effective_rent_krw_per_sqm_month"])
                conversion_rate = int(item["conversion_rate_bps"])
                period = str(item["period"])
                region_code = str(item["region_code"])
                region_name = str(item["region_name"])
                coverage_status = str(item["coverage_status"])
                floor_basis = str(item["floor_basis"])
            except (KeyError, TypeError, ValueError):
                continue
            if (
                not isinstance(evidence_id, str)
                or not isinstance(source, dict)
                or not isinstance(source.get("title"), str)
                or not isinstance(source.get("source_ref"), str)
                or not isinstance(source.get("published_or_data_date"), str)
            ):
                continue
            benchmarks.append(
                PropertyRentBenchmark(
                    evidence_ref=evidence_id,
                    region_code=region_code,
                    region_name=region_name,
                    property_class=property_class,
                    period=period,
                    effective_rent_krw_per_sqm_month=effective_rent,
                    conversion_rate_bps=conversion_rate,
                    coverage_status=coverage_status,
                    floor_basis=floor_basis,
                    source_title=str(source["title"]),
                    source_ref=str(source["source_ref"]),
                    data_date=str(source["published_or_data_date"]),
                )
            )
    return benchmarks


def replay_property_rent_benchmarks(
    source_bundle: dict[str, Any],
) -> list[PropertyRentBenchmark]:
    """Preserve regional benchmark inputs during deterministic selective recompute."""

    candidates = source_bundle.get("candidates")
    if not isinstance(candidates, list):
        return []
    resolved: dict[tuple[str, CommercialPropertyClass], PropertyRentBenchmark] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        decision_inputs = candidate.get("decision_inputs")
        if not isinstance(decision_inputs, list):
            continue
        for decision_input in decision_inputs:
            benchmark = _benchmark_from_decision_input(decision_input)
            if benchmark is None:
                continue
            resolved[(benchmark.region_code, benchmark.property_class)] = benchmark
    return sorted(
        resolved.values(),
        key=lambda value: (value.region_code, value.property_class.value),
    )


def _benchmark_from_decision_input(value: object) -> PropertyRentBenchmark | None:
    if not isinstance(value, dict):
        return None
    if value.get("field") != "MONTHLY_OCCUPANCY" or value.get("provenance") != "BENCHMARK":
        return None
    derivation = value.get("derivation")
    inputs = derivation.get("inputs") if isinstance(derivation, dict) else None
    scope = value.get("geographic_scope")
    anchor = value.get("source_anchor")
    if (
        not isinstance(derivation, dict)
        or derivation.get("formula_code") != "REB_EFFECTIVE_RENT_TO_MONTHLY_OCCUPANCY_V1"
        or not isinstance(inputs, dict)
        or not isinstance(scope, dict)
        or scope.get("scope_type") != "REGION"
        or not isinstance(scope.get("scope_id"), str)
        or not isinstance(anchor, str)
    ):
        return None
    anchor_parts = anchor.split(":")
    if len(anchor_parts) != 4 or anchor_parts[3] != "effective-rent":
        return None
    period, region_code, property_class_value, _ = anchor_parts
    if region_code != scope["scope_id"]:
        return None
    try:
        property_class = CommercialPropertyClass(property_class_value)
        effective_rent = int(inputs["effective_rent_krw_per_sqm_month"])
        conversion_rate = int(inputs["conversion_rate_bps"])
    except (KeyError, TypeError, ValueError):
        return None
    source_title = value.get("source_title")
    source_ref = value.get("source_ref")
    data_date = value.get("data_date")
    coverage_status = derivation.get("coverage_status")
    floor_basis = derivation.get("floor_basis")
    if not all(
        isinstance(item, str) and item
        for item in (source_title, source_ref, data_date, coverage_status, floor_basis)
    ):
        return None
    assert isinstance(source_title, str)
    assert isinstance(source_ref, str)
    assert isinstance(data_date, str)
    assert isinstance(coverage_status, str)
    assert isinstance(floor_basis, str)
    replay_identity = json.dumps(
        {
            "source_ref": source_ref,
            "anchor": anchor,
            "data_date": data_date,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    replay_digest = hashlib.sha256(replay_identity.encode()).hexdigest()[:24]
    return PropertyRentBenchmark(
        evidence_ref=f"property-benchmark-replay:{replay_digest}",
        region_code=region_code,
        region_name=region_code,
        property_class=property_class,
        period=period,
        effective_rent_krw_per_sqm_month=effective_rent,
        conversion_rate_bps=conversion_rate,
        coverage_status=coverage_status,
        floor_basis=floor_basis,
        source_title=source_title,
        source_ref=source_ref,
        data_date=data_date,
    )