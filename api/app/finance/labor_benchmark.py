"""Apply official minimum-wage schedules as legal floors to paid-staff assumptions."""

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.candidates.seed_registry import PaidStaffFteProfile
from app.finance.models import CostCategory, CostLine, MoneyRange, ValueProvenance


@dataclass(frozen=True)
class MinimumWageReference:
    evidence_ref: str
    effective_from: str
    effective_to: str
    hourly_rate_krw: int
    monthly_equivalent_hours: int
    monthly_equivalent_krw: int
    source_title: str
    source_ref: str
    data_date: str


@dataclass(frozen=True)
class DerivedLaborBenchmark:
    amount: MoneyRange
    legal_floor: MoneyRange
    formula_code: str


@dataclass(frozen=True)
class ResolvedLaborBenchmarkCost:
    source_id: str
    amount: MoneyRange
    evidence_ref: str

    def as_cost_line(self) -> CostLine:
        return CostLine(
            field_id=CostCategory.MONTHLY_LABOR.value,
            category=CostCategory.MONTHLY_LABOR,
            amount=self.amount,
            provenance=ValueProvenance.BENCHMARK,
            evidence_ref=self.evidence_ref,
        )


@dataclass(frozen=True)
class LaborBenchmarkResolution:
    overrides: tuple[ResolvedLaborBenchmarkCost, ...] = ()
    sources: dict[str, dict[str, object]] = field(default_factory=dict)


def derive_monthly_labor_floor(
    *,
    reference: MinimumWageReference,
    paid_staff_fte: PaidStaffFteProfile,
    seed_labor: MoneyRange,
) -> DerivedLaborBenchmark:
    if None in (seed_labor.low, seed_labor.base, seed_labor.high):
        raise ValueError("minimum-wage floor requires a complete seed labor range")
    if reference.hourly_rate_krw <= 0 or reference.monthly_equivalent_krw <= 0:
        raise ValueError("minimum-wage reference must be positive")
    expected_monthly = reference.hourly_rate_krw * reference.monthly_equivalent_hours
    if expected_monthly != reference.monthly_equivalent_krw:
        raise ValueError("minimum-wage monthly equivalent does not match hourly rate")

    def legal_floor(fte: float) -> int:
        return math.ceil(reference.monthly_equivalent_krw * fte)

    floor = MoneyRange(
        low=legal_floor(paid_staff_fte.low),
        base=legal_floor(paid_staff_fte.base),
        high=legal_floor(paid_staff_fte.high),
    )
    assert seed_labor.low is not None
    assert seed_labor.base is not None
    assert seed_labor.high is not None
    assert floor.low is not None
    assert floor.base is not None
    assert floor.high is not None
    amount = MoneyRange(
        low=max(seed_labor.low, floor.low),
        base=max(seed_labor.base, floor.base),
        high=max(seed_labor.high, floor.high),
    )
    return DerivedLaborBenchmark(
        amount=amount,
        legal_floor=floor,
        formula_code="MINIMUM_WAGE_FTE_FLOOR_V1",
    )


def resolve_seed_labor_benchmarks(
    *,
    seeds: list[tuple[str, PaidStaffFteProfile, MoneyRange]],
    references: list[MinimumWageReference],
    as_of: str | None = None,
) -> LaborBenchmarkResolution:
    reference = _reference_for_date(references, as_of)
    if reference is None:
        return LaborBenchmarkResolution()
    overrides: list[ResolvedLaborBenchmarkCost] = []
    sources: dict[str, dict[str, object]] = {}
    for source_id, paid_staff_fte, seed_labor in seeds:
        if paid_staff_fte.high == 0:
            continue
        derived = derive_monthly_labor_floor(
            reference=reference,
            paid_staff_fte=paid_staff_fte,
            seed_labor=seed_labor,
        )
        if derived.amount == seed_labor:
            continue
        derived_evidence_ref = f"{reference.evidence_ref}:derived:{source_id}"
        overrides.append(
            ResolvedLaborBenchmarkCost(
                source_id=source_id,
                amount=derived.amount,
                evidence_ref=derived_evidence_ref,
            )
        )
        sources[derived_evidence_ref] = {
            "source_title": reference.source_title,
            "source_ref": reference.source_ref,
            "data_date": reference.data_date,
            "geographic_scope": {
                "scope_type": "NATIONAL",
                "scope_id": "KR",
                "boundary_version": None,
            },
            "source_anchor": (
                f"MINIMUM_WAGE:{reference.effective_from}:{reference.effective_to}"
            ),
            "derivation": {
                "formula_code": derived.formula_code,
                "inputs": {
                    "hourly_rate_krw": reference.hourly_rate_krw,
                    "monthly_equivalent_hours": reference.monthly_equivalent_hours,
                    "monthly_equivalent_krw": reference.monthly_equivalent_krw,
                    "paid_staff_fte": paid_staff_fte.model_dump(mode="json"),
                    "seed_labor_assumption_krw": seed_labor.model_dump(mode="json"),
                    "legal_floor_krw": derived.legal_floor.model_dump(mode="json"),
                    "effective_from": reference.effective_from,
                    "effective_to": reference.effective_to,
                },
                "coverage_status": "NATIONAL_LEGAL_FLOOR",
            },
        }
    return LaborBenchmarkResolution(overrides=tuple(overrides), sources=sources)


def minimum_wage_references_from_mcp_results(
    structured_results: list[dict[str, Any]],
) -> list[MinimumWageReference]:
    references: list[MinimumWageReference] = []
    for content in structured_results:
        if content.get("tool_name") != "get_cost_reference" or content.get("status") not in {
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
            if not isinstance(item, dict) or item.get("reference_type") != "MINIMUM_WAGE":
                continue
            evidence_id = item.get("evidence_id")
            evidence = evidence_by_id.get(evidence_id)
            source = evidence.get("source") if isinstance(evidence, dict) else None
            if not isinstance(evidence_id, str) or not isinstance(source, dict):
                continue
            try:
                reference = MinimumWageReference(
                    evidence_ref=str(evidence_id),
                    effective_from=str(item["effective_from"]),
                    effective_to=str(item["effective_to"]),
                    hourly_rate_krw=int(item["hourly_rate_krw"]),
                    monthly_equivalent_hours=int(item["monthly_equivalent_hours"]),
                    monthly_equivalent_krw=int(item["monthly_equivalent_krw"]),
                    source_title=str(source["title"]),
                    source_ref=str(source["source_ref"]),
                    data_date=str(source["published_or_data_date"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            references.append(reference)
    return references


def replay_minimum_wage_references(source_bundle: dict[str, Any]) -> list[MinimumWageReference]:
    candidates = source_bundle.get("candidates")
    if not isinstance(candidates, list):
        return []
    references: dict[tuple[str, str], MinimumWageReference] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        decision_inputs = candidate.get("decision_inputs")
        if not isinstance(decision_inputs, list):
            continue
        for value in decision_inputs:
            reference = _reference_from_decision_input(value)
            if reference is not None:
                references[(reference.effective_from, reference.effective_to)] = reference
    return sorted(references.values(), key=lambda value: value.effective_from)


def _reference_for_date(
    references: list[MinimumWageReference],
    as_of: str | None,
) -> MinimumWageReference | None:
    if as_of is None:
        if len(references) != 1:
            return None
        return references[0]
    target = date.fromisoformat(as_of)
    active = [
        value
        for value in references
        if (
            date.fromisoformat(value.effective_from)
            <= target
            <= date.fromisoformat(value.effective_to)
        )
    ]
    if not active:
        return None
    return max(active, key=lambda value: value.effective_from)


def _reference_from_decision_input(value: object) -> MinimumWageReference | None:
    if not isinstance(value, dict):
        return None
    if value.get("field") != "MONTHLY_LABOR" or value.get("provenance") != "BENCHMARK":
        return None
    derivation = value.get("derivation")
    inputs = derivation.get("inputs") if isinstance(derivation, dict) else None
    if (
        not isinstance(derivation, dict)
        or derivation.get("formula_code") != "MINIMUM_WAGE_FTE_FLOOR_V1"
        or not isinstance(inputs, dict)
    ):
        return None
    source_title = value.get("source_title")
    source_ref = value.get("source_ref")
    data_date = value.get("data_date")
    try:
        effective_from = str(inputs["effective_from"])
        effective_to = str(inputs["effective_to"])
        hourly_rate_krw = int(inputs["hourly_rate_krw"])
        monthly_equivalent_hours = int(inputs["monthly_equivalent_hours"])
        monthly_equivalent_krw = int(inputs["monthly_equivalent_krw"])
        date.fromisoformat(effective_from)
        date.fromisoformat(effective_to)
    except (KeyError, TypeError, ValueError):
        return None
    if not all(isinstance(item, str) and item for item in (source_title, source_ref, data_date)):
        return None
    assert isinstance(source_title, str)
    assert isinstance(source_ref, str)
    assert isinstance(data_date, str)
    replay_identity = json.dumps(
        {
            "source_ref": source_ref,
            "data_date": data_date,
            "effective_from": effective_from,
            "effective_to": effective_to,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    evidence_ref = f"minimum-wage:replay:{hashlib.sha256(replay_identity.encode()).hexdigest()}"
    return MinimumWageReference(
        evidence_ref=evidence_ref,
        effective_from=effective_from,
        effective_to=effective_to,
        hourly_rate_krw=hourly_rate_krw,
        monthly_equivalent_hours=monthly_equivalent_hours,
        monthly_equivalent_krw=monthly_equivalent_krw,
        source_title=source_title,
        source_ref=source_ref,
        data_date=data_date,
    )