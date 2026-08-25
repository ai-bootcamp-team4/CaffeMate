"""Focused regression coverage for durable selective-recompute input replay."""

from typing import Any

from app.finance.labor_benchmark import replay_minimum_wage_references
from app.finance.labor_oncost import (
    replay_employer_oncost_minimum_wage_references,
    replay_employer_social_insurance_references,
)
from app.workflows.execution import (
    PostgresFirstProposalExecutor,
    _franchise_universe_from_bundle,
    _merge_minimum_wage_references,
)


def _source_bundle() -> dict[str, Any]:
    return {
        "candidates": [
            {
                "candidate_id": "candidate-1",
                "case_type": "INDEPENDENT",
                "independent_model": {"model_id": "independent-small-takeout-v1"},
                "property_context": {
                    "property_input_id": "property-1",
                    "address": "서울특별시 마포구 공덕동 실제 점포",
                    "area_sqm": 33.0,
                    "floor": "1층",
                    "deposit_krw": 30_000_000,
                    "monthly_rent_krw": 2_200_000,
                    "management_fee_krw": 200_000,
                    "key_money_krw": 10_000_000,
                    "provenance": "USER_INPUT",
                },
                "decision_inputs": [
                    {
                        "field": "MONTHLY_LABOR",
                        "provenance": "BENCHMARK",
                        "source_title": "최저임금위원회 연도별 최저임금",
                        "source_ref": "https://www.minimumwage.go.kr/",
                        "data_date": "2026-01-01",
                        "derivation": {
                            "formula_code": "MINIMUM_WAGE_FTE_FLOOR_V1",
                            "inputs": {
                                "hourly_rate_krw": 10_320,
                                "monthly_equivalent_hours": 209,
                                "monthly_equivalent_krw": 2_156_880,
                                "effective_from": "2026-01-01",
                                "effective_to": "2026-12-31",
                            },
                        },
                    },
                    {
                        "field": "MONTHLY_EMPLOYER_ONCOST",
                        "provenance": "BENCHMARK",
                        "derivation": {
                            "formula_code": "EMPLOYER_SOCIAL_INSURANCE_FLOOR_V1",
                            "inputs": {
                                "effective_from": "2026-01-01",
                                "effective_to": "2026-12-31",
                                "minimum_wage_reference": {
                                    "evidence_ref": "minimum-wage-2026",
                                    "effective_from": "2026-01-01",
                                    "effective_to": "2026-12-31",
                                    "hourly_rate_krw": 10_320,
                                    "monthly_equivalent_hours": 209,
                                    "monthly_equivalent_krw": 2_156_880,
                                    "source_title": "최저임금위원회 연도별 최저임금",
                                    "source_ref": "https://www.minimumwage.go.kr/",
                                    "data_date": "2026-01-01",
                                },
                                "workplace_employee_upper_bound": 149,
                                "components": [
                                    {
                                        "component": "NATIONAL_PENSION",
                                        "employer_rate_ppm": 47_500,
                                        "evidence_ref": "pension-2026",
                                        "source_title": "국민연금공단",
                                        "source_ref": "https://www.nps.or.kr/",
                                        "data_date": "2026-01-01",
                                    },
                                    {
                                        "component": "HEALTH_LONG_TERM_CARE",
                                        "employer_rate_ppm": 40_674,
                                        "evidence_ref": "health-2026",
                                        "source_title": "국민건강보험공단",
                                        "source_ref": "https://www.nhis.or.kr/",
                                        "data_date": "2026-01-01",
                                    },
                                    {
                                        "component": "UNEMPLOYMENT_BENEFIT",
                                        "employer_rate_ppm": 9_000,
                                        "evidence_ref": "unemployment-2026",
                                        "source_title": "고용노동부",
                                        "source_ref": "https://www.moel.go.kr/",
                                        "data_date": "2026-01-01",
                                    },
                                    {
                                        "component": "EMPLOYMENT_STABILIZATION_VOCATIONAL",
                                        "employer_rate_ppm": 2_500,
                                        "evidence_ref": "stabilization-2026",
                                        "source_title": "고용노동부",
                                        "source_ref": "https://www.moel.go.kr/",
                                        "data_date": "2026-01-01",
                                    },
                                ],
                                "unsupported_components": [
                                    "WORKERS_COMPENSATION_INDUSTRY_RATE_REQUIRED"
                                ],
                                "excluded_adjustments": [
                                    "CONTRIBUTION_BASE_CAPS_AND_FLOORS_NOT_APPLIED",
                                    "EXEMPTIONS_NOT_APPLIED",
                                    "SUPPORT_PROGRAMS_NOT_APPLIED",
                                ],
                            },
                        },
                    },
                ],
            },
            {
                "candidate_id": "candidate-2",
                "case_type": "FRANCHISE",
                "display_name": "메가MGC커피",
                "franchise": {
                    "brand_id": "kr-mega-mgc-coffee",
                    "eligibility": "VERIFIED",
                    "eligibility_evidence_refs": ["eligibility:mega"],
                    "finance_profile": {
                        "monthly_royalty_krw": None,
                        "sales_royalty_bps": 300,
                        "known_initial_cost_range_krw": {
                            "low": 127_890_000,
                            "base": 127_890_000,
                            "high": 127_890_000,
                        },
                    },
                },
            },
        ]
    }


def test_durable_selective_recompute_replays_source_grounding_bundle() -> None:
    bundle = _source_bundle()
    context = PostgresFirstProposalExecutor._property_context_from_source_bundle(
        bundle,
        active_case_id="candidate-1",
    )

    assert context is not None
    assert context.address == "서울특별시 마포구 공덕동 실제 점포"
    assert context.monthly_rent_krw == 2_200_000

    universe = _franchise_universe_from_bundle(bundle)
    assert universe[0]["finance_profile"]["sales_royalty_bps"] == 300
    minimum_wage = _merge_minimum_wage_references(
        replay_minimum_wage_references(bundle),
        replay_employer_oncost_minimum_wage_references(bundle),
    )
    assert len(minimum_wage) == 1
    social_insurance = replay_employer_social_insurance_references(bundle)
    assert len(social_insurance) == 1
    assert social_insurance[0].employer_rate_ppm == 99_674
