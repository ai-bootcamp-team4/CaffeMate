"""Derive employer statutory labor on-cost floors from official rate schedules."""

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, Decimal
from typing import Any

from app.candidates.seed_registry import PaidStaffFteProfile
from app.finance.labor_benchmark import MinimumWageReference
from app.finance.models import CostCategory, CostLine, MoneyRange, ValueProvenance

REQUIRED_EMPLOYER_COMPONENTS = frozenset(
    {
        "NATIONAL_PENSION",
        "HEALTH_LONG_TERM_CARE",
        "UNEMPLOYMENT_BENEFIT",
        "EMPLOYMENT_STABILIZATION_VOCATIONAL",
    }
)
REQUIRED_UNSUPPORTED_COMPONENTS = frozenset(
    {"WORKERS_COMPENSATION_INDUSTRY_RATE_REQUIRED"}
)
REQUIRED_EXCLUDED_ADJUSTMENTS = frozenset(
    {
        "CONTRIBUTION_BASE_CAPS_AND_FLOORS_NOT_APPLIED",
        "EXEMPTIONS_NOT_APPLIED",
        "SUPPORT_PROGRAMS_NOT_APPLIED",
    }
)
PAYROLL_BASIS_EXCLUSIONS = ("FOUNDER_AND_SELF_LABOR_EXCLUDED",)


@dataclass(frozen=True)
class EmployerInsuranceComponent:
    component: str
    employer_rate_ppm: int
    evidence_ref: str
    source_title: str
    source_ref: str
    data_date: str

    def __post_init__(self) -> None:
        if self.component not in REQUIRED_EMPLOYER_COMPONENTS:
            raise ValueError(f"unsupported employer insurance component: {self.component}")
        if self.employer_rate_ppm <= 0:
            raise ValueError("employer insurance rate must be positive")


@dataclass(frozen=True)
class EmployerSocialInsuranceReference:
    effective_from: str
    effective_to: str
    workplace_employee_upper_bound: int
    components: tuple[EmployerInsuranceComponent, ...]
    unsupported_components: tuple[str, ...] = ()
    excluded_adjustments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        component_names = [value.component for value in self.components]
        if set(component_names) != REQUIRED_EMPLOYER_COMPONENTS:
            raise ValueError(
                "employer social-insurance schedule must include every fixed component"
            )
        if len(component_names) != len(set(component_names)):
            raise ValueError("employer social-insurance components must be unique")
        if (
            set(self.unsupported_components) != REQUIRED_UNSUPPORTED_COMPONENTS
            or len(self.unsupported_components) != len(REQUIRED_UNSUPPORTED_COMPONENTS)
        ):
            raise ValueError("workers-compensation omission must be explicit")
        if (
            set(self.excluded_adjustments) != REQUIRED_EXCLUDED_ADJUSTMENTS
            or len(self.excluded_adjustments) != len(REQUIRED_EXCLUDED_ADJUSTMENTS)
        ):
            raise ValueError("unverified social-insurance adjustments must be explicit")
        if self.workplace_employee_upper_bound < 1:
            raise ValueError("workplace employee upper bound must be positive")
        if date.fromisoformat(self.effective_from) > date.fromisoformat(self.effective_to):
            raise ValueError("employer social-insurance effective range is invalid")

    @property
    def employer_rate_ppm(self) -> int:
        return sum(value.employer_rate_ppm for value in self.components)


@dataclass(frozen=True)
class DerivedEmployerOncost:
    amount: MoneyRange
    payroll_floor: MoneyRange
    employer_rate_ppm: int
    formula_code: str


@dataclass(frozen=True)
class ResolvedEmployerOncostCost:
    source_id: str
    amount: MoneyRange
    provenance: ValueProvenance
    evidence_ref: str | None = None

    def as_cost_line(self) -> CostLine:
        return CostLine(
            field_id=CostCategory.MONTHLY_EMPLOYER_ONCOST.value,
            category=CostCategory.MONTHLY_EMPLOYER_ONCOST,
            amount=self.amount,
            provenance=self.provenance,
            evidence_ref=self.evidence_ref,
        )


@dataclass(frozen=True)
class EmployerOncostResolution:
    overrides: tuple[ResolvedEmployerOncostCost, ...] = ()
    sources: dict[str, dict[str, object]] = field(default_factory=dict)


def derive_employer_oncost_floor(
    *,
    minimum_wage: MinimumWageReference,
    social_insurance: EmployerSocialInsuranceReference,
    paid_staff_fte: PaidStaffFteProfile,
) -> DerivedEmployerOncost:
    if math.ceil(paid_staff_fte.high) > social_insurance.workplace_employee_upper_bound:
        raise ValueError("paid staff exceeds social-insurance schedule workplace scope")

    def payroll_floor(fte: float) -> int:
        return int(
            (Decimal(minimum_wage.monthly_equivalent_krw) * Decimal(str(fte))).to_integral_value(
                rounding=ROUND_CEILING
            )
        )

    payroll = MoneyRange(
        low=payroll_floor(paid_staff_fte.low),
        base=payroll_floor(paid_staff_fte.base),
        high=payroll_floor(paid_staff_fte.high),
    )

    def oncost(value: int | None) -> int | None:
        if value is None:
            return None
        return int(
            (
                Decimal(value)
                * Decimal(social_insurance.employer_rate_ppm)
                / Decimal(1_000_000)
            ).to_integral_value(rounding=ROUND_CEILING)
        )

    return DerivedEmployerOncost(
        amount=MoneyRange(
            low=oncost(payroll.low),
            base=oncost(payroll.base),
            high=oncost(payroll.high),
        ),
        payroll_floor=payroll,
        employer_rate_ppm=social_insurance.employer_rate_ppm,
        formula_code="EMPLOYER_SOCIAL_INSURANCE_FLOOR_V1",
    )


def resolve_seed_employer_oncosts(
    *,
    seeds: list[tuple[str, PaidStaffFteProfile]],
    minimum_wage_references: list[MinimumWageReference],
    social_insurance_references: list[EmployerSocialInsuranceReference],
    as_of: str | None = None,
) -> EmployerOncostResolution:
    wage = _reference_for_date(minimum_wage_references, as_of)
    insurance = _reference_for_date(social_insurance_references, as_of)
    overrides: list[ResolvedEmployerOncostCost] = []
    sources: dict[str, dict[str, object]] = {}

    for source_id, paid_staff_fte in seeds:
        if paid_staff_fte.high == 0:
            overrides.append(
                ResolvedEmployerOncostCost(
                    source_id=source_id,
                    amount=MoneyRange(low=0, base=0, high=0),
                    provenance=ValueProvenance.DERIVED,
                )
            )
            continue
        if wage is None or insurance is None:
            overrides.append(
                ResolvedEmployerOncostCost(
                    source_id=source_id,
                    amount=MoneyRange(low=None, base=None, high=None),
                    provenance=ValueProvenance.UNKNOWN,
                )
            )
            continue
        if math.ceil(paid_staff_fte.high) > insurance.workplace_employee_upper_bound:
            overrides.append(
                ResolvedEmployerOncostCost(
                    source_id=source_id,
                    amount=MoneyRange(low=None, base=None, high=None),
                    provenance=ValueProvenance.UNKNOWN,
                )
            )
            continue

        derived = derive_employer_oncost_floor(
            minimum_wage=wage,
            social_insurance=insurance,
            paid_staff_fte=paid_staff_fte,
        )
        evidence_ref = _derived_evidence_ref(
            source_id=source_id,
            minimum_wage=wage,
            social_insurance=insurance,
        )
        overrides.append(
            ResolvedEmployerOncostCost(
                source_id=source_id,
                amount=derived.amount,
                provenance=ValueProvenance.BENCHMARK,
                evidence_ref=evidence_ref,
            )
        )
        component_rows = [
            {
                "component": value.component,
                "employer_rate_ppm": value.employer_rate_ppm,
                "evidence_ref": value.evidence_ref,
                "source_title": value.source_title,
                "source_ref": value.source_ref,
                "data_date": value.data_date,
            }
            for value in insurance.components
        ]
        sources[evidence_ref] = {
            "source_title": "국민연금공단·국민건강보험공단·고용노동부 공식 사업주 부담 요율",
            "source_ref": None,
            "data_date": max(
                [wage.data_date, *[value.data_date for value in insurance.components]]
            ),
            "geographic_scope": {
                "scope_type": "NATIONAL",
                "scope_id": "KR",
                "boundary_version": None,
            },
            "source_anchor": (
                "EMPLOYER_SOCIAL_INSURANCE:"
                f"{insurance.effective_from}:{insurance.effective_to}:LT150"
            ),
            "derivation": {
                "formula_code": derived.formula_code,
                "inputs": {
                    "minimum_wage_monthly_equivalent_krw": wage.monthly_equivalent_krw,
                    "minimum_wage_reference": {
                        "evidence_ref": wage.evidence_ref,
                        "effective_from": wage.effective_from,
                        "effective_to": wage.effective_to,
                        "hourly_rate_krw": wage.hourly_rate_krw,
                        "monthly_equivalent_hours": wage.monthly_equivalent_hours,
                        "monthly_equivalent_krw": wage.monthly_equivalent_krw,
                        "source_title": wage.source_title,
                        "source_ref": wage.source_ref,
                        "data_date": wage.data_date,
                    },
                    "paid_staff_fte": paid_staff_fte.model_dump(mode="json"),
                    "payroll_basis_exclusions": list(PAYROLL_BASIS_EXCLUSIONS),
                    "payroll_floor_krw": derived.payroll_floor.model_dump(mode="json"),
                    "employer_rate_ppm": derived.employer_rate_ppm,
                    "employer_rate_bps_decimal": format(
                        Decimal(derived.employer_rate_ppm) / Decimal(100),
                        ".2f",
                    ),
                    "components": component_rows,
                    "workplace_employee_upper_bound": insurance.workplace_employee_upper_bound,
                    "unsupported_components": list(insurance.unsupported_components),
                    "excluded_adjustments": list(insurance.excluded_adjustments),
                    "effective_from": insurance.effective_from,
                    "effective_to": insurance.effective_to,
                },
                "coverage_status": "NATIONAL_FIXED_COMPONENT_FLOOR",
                "constituent_evidence_refs": [
                    wage.evidence_ref,
                    *[value.evidence_ref for value in insurance.components],
                ],
            },
        }

    return EmployerOncostResolution(overrides=tuple(overrides), sources=sources)


def employer_social_insurance_references_from_mcp_results(
    structured_results: list[dict[str, Any]],
) -> list[EmployerSocialInsuranceReference]:
    references: list[EmployerSocialInsuranceReference] = []
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
            if (
                not isinstance(item, dict)
                or item.get("reference_type") != "EMPLOYER_SOCIAL_INSURANCE"
            ):
                continue
            components_raw = item.get("components")
            if not isinstance(components_raw, list):
                continue
            components: list[EmployerInsuranceComponent] = []
            malformed = False
            for raw in components_raw:
                if not isinstance(raw, dict):
                    malformed = True
                    break
                evidence_id = raw.get("evidence_id")
                evidence = evidence_by_id.get(evidence_id)
                source = evidence.get("source") if isinstance(evidence, dict) else None
                if not isinstance(evidence_id, str) or not isinstance(source, dict):
                    malformed = True
                    break
                try:
                    components.append(
                        EmployerInsuranceComponent(
                            component=str(raw["component"]),
                            employer_rate_ppm=int(raw["employer_rate_ppm"]),
                            evidence_ref=evidence_id,
                            source_title=str(source["title"]),
                            source_ref=str(source["source_ref"]),
                            data_date=str(source["published_or_data_date"]),
                        )
                    )
                except (KeyError, TypeError, ValueError):
                    malformed = True
                    break
            if malformed:
                continue
            unsupported = item.get("unsupported_components")
            excluded_adjustments = item.get("excluded_adjustments")
            if not isinstance(unsupported, list) or any(
                not isinstance(value, str) for value in unsupported
            ):
                continue
            if not isinstance(excluded_adjustments, list) or any(
                not isinstance(value, str) for value in excluded_adjustments
            ):
                continue
            try:
                references.append(
                    EmployerSocialInsuranceReference(
                        effective_from=str(item["effective_from"]),
                        effective_to=str(item["effective_to"]),
                        workplace_employee_upper_bound=int(
                            item["workplace_employee_upper_bound"]
                        ),
                        components=tuple(components),
                        unsupported_components=tuple(unsupported),
                        excluded_adjustments=tuple(excluded_adjustments),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
    return references


def replay_employer_social_insurance_references(
    source_bundle: dict[str, Any],
) -> list[EmployerSocialInsuranceReference]:
    candidates = source_bundle.get("candidates")
    if not isinstance(candidates, list):
        return []
    references: dict[tuple[str, str], EmployerSocialInsuranceReference] = {}
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


def replay_employer_oncost_minimum_wage_references(
    source_bundle: dict[str, Any],
) -> list[MinimumWageReference]:
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
            reference = _minimum_wage_reference_from_decision_input(value)
            if reference is not None:
                references[(reference.effective_from, reference.effective_to)] = reference
    return sorted(references.values(), key=lambda value: value.effective_from)


def _minimum_wage_reference_from_decision_input(
    value: object,
) -> MinimumWageReference | None:
    if not isinstance(value, dict):
        return None
    if (
        value.get("field") != CostCategory.MONTHLY_EMPLOYER_ONCOST.value
        or value.get("provenance") != ValueProvenance.BENCHMARK.value
    ):
        return None
    derivation = value.get("derivation")
    inputs = derivation.get("inputs") if isinstance(derivation, dict) else None
    raw = inputs.get("minimum_wage_reference") if isinstance(inputs, dict) else None
    if (
        not isinstance(derivation, dict)
        or derivation.get("formula_code") != "EMPLOYER_SOCIAL_INSURANCE_FLOOR_V1"
        or not isinstance(raw, dict)
    ):
        return None
    try:
        return MinimumWageReference(
            evidence_ref=str(raw["evidence_ref"]),
            effective_from=str(raw["effective_from"]),
            effective_to=str(raw["effective_to"]),
            hourly_rate_krw=int(raw["hourly_rate_krw"]),
            monthly_equivalent_hours=int(raw["monthly_equivalent_hours"]),
            monthly_equivalent_krw=int(raw["monthly_equivalent_krw"]),
            source_title=str(raw["source_title"]),
            source_ref=str(raw["source_ref"]),
            data_date=str(raw["data_date"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _reference_from_decision_input(
    value: object,
) -> EmployerSocialInsuranceReference | None:
    if not isinstance(value, dict):
        return None
    if (
        value.get("field") != CostCategory.MONTHLY_EMPLOYER_ONCOST.value
        or value.get("provenance") != ValueProvenance.BENCHMARK.value
    ):
        return None
    derivation = value.get("derivation")
    inputs = derivation.get("inputs") if isinstance(derivation, dict) else None
    if (
        not isinstance(derivation, dict)
        or derivation.get("formula_code") != "EMPLOYER_SOCIAL_INSURANCE_FLOOR_V1"
        or not isinstance(inputs, dict)
    ):
        return None
    components_raw = inputs.get("components")
    unsupported_raw = inputs.get("unsupported_components")
    excluded_adjustments_raw = inputs.get("excluded_adjustments")
    if (
        not isinstance(components_raw, list)
        or not isinstance(unsupported_raw, list)
        or not isinstance(excluded_adjustments_raw, list)
    ):
        return None
    components: list[EmployerInsuranceComponent] = []
    try:
        for raw in components_raw:
            if not isinstance(raw, dict):
                return None
            components.append(
                EmployerInsuranceComponent(
                    component=str(raw["component"]),
                    employer_rate_ppm=int(raw["employer_rate_ppm"]),
                    evidence_ref=str(raw["evidence_ref"]),
                    source_title=str(raw["source_title"]),
                    source_ref=str(raw["source_ref"]),
                    data_date=str(raw["data_date"]),
                )
            )
        reference = EmployerSocialInsuranceReference(
            effective_from=str(inputs["effective_from"]),
            effective_to=str(inputs["effective_to"]),
            workplace_employee_upper_bound=int(inputs["workplace_employee_upper_bound"]),
            components=tuple(components),
            unsupported_components=tuple(str(item) for item in unsupported_raw),
            excluded_adjustments=tuple(
                str(item) for item in excluded_adjustments_raw
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None
    return reference


def _reference_for_date(values: list[Any], as_of: str | None) -> Any | None:
    if as_of is None:
        return values[0] if len(values) == 1 else None
    target = date.fromisoformat(as_of)
    active = [
        value
        for value in values
        if date.fromisoformat(value.effective_from)
        <= target
        <= date.fromisoformat(value.effective_to)
    ]
    if not active:
        return None
    return max(active, key=lambda value: value.effective_from)


def _derived_evidence_ref(
    *,
    source_id: str,
    minimum_wage: MinimumWageReference,
    social_insurance: EmployerSocialInsuranceReference,
) -> str:
    identity = json.dumps(
        {
            "source_id": source_id,
            "minimum_wage": minimum_wage.evidence_ref,
            "components": [
                {
                    "component": value.component,
                    "rate_ppm": value.employer_rate_ppm,
                    "evidence_ref": value.evidence_ref,
                }
                for value in social_insurance.components
            ],
            "effective_from": social_insurance.effective_from,
            "effective_to": social_insurance.effective_to,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"employer-oncost:derived:{hashlib.sha256(identity.encode()).hexdigest()}"
