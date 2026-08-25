"""사용자는 한 번의 분석 요청으로 결과를 받아야 하며 13단계 제어부를 보지 않는다."""

from inspect import signature

from app.finance.property_benchmark import replay_property_rent_benchmarks
from app.workflows.first_proposal import (
    FirstProposalStage,
    stage_input_digest,
)
from app.workflows.models import HeadFence
from app.workflows.selective_start import (
    _franchise_universe,
    start_selective_first_proposal,
)


def test_first_proposal_has_one_execution_unit() -> None:
    assert list(FirstProposalStage) == [FirstProposalStage.RUN_PROPOSAL]


def test_single_execution_contract_has_no_stage_selection_or_dependencies() -> None:
    assert "dependencies" not in signature(stage_input_digest).parameters
    assert (
        "affected_stage_codes"
        not in signature(start_selective_first_proposal).parameters
    )


def test_single_stage_digest_is_stable() -> None:
    head = HeadFence(
        workflow_generation=2,
        state_version=3,
        founder_snapshot_id="founder-3",
        area_snapshot_id="area-3",
        evidence_snapshot_id="evidence-2",
        policy_snapshot_id="policy-1",
        index_generation_id=None,
        seed_registry_id="seeds-1",
    )

    first = stage_input_digest(
        workflow_run_id="workflow-2",
        stage_code=FirstProposalStage.RUN_PROPOSAL,
        head=head,
    )
    second = stage_input_digest(
        workflow_run_id="workflow-2",
        stage_code=FirstProposalStage.RUN_PROPOSAL,
        head=head,
    )

    assert first == second


def test_selective_recompute_replays_regional_occupancy_benchmark() -> None:
    source_bundle = {
        "candidates": [
            {
                "decision_inputs": [
                    {
                        "field": "MONTHLY_OCCUPANCY",
                        "provenance": "BENCHMARK",
                        "source_title": "한국부동산원 상업용부동산 임대동향조사",
                        "source_ref": "https://www.reb.or.kr/r-one/",
                        "data_date": "2026-06-30",
                        "geographic_scope": {
                            "scope_type": "REGION",
                            "scope_id": "11",
                            "boundary_version": None,
                        },
                        "source_anchor": "2026Q2:11:SMALL_RETAIL:effective-rent",
                        "derivation": {
                            "formula_code": "REB_EFFECTIVE_RENT_TO_MONTHLY_OCCUPANCY_V1",
                            "inputs": {
                                "effective_rent_krw_per_sqm_month": 95_000,
                                "conversion_rate_bps": 680,
                                "area_sqm": {"low": 20, "base": 30, "high": 40},
                                "deposit_base_krw": 35_000_000,
                                "management_fee_ratio_bps": 1_000,
                            },
                            "coverage_status": "PARENT_REGION",
                            "floor_basis": "FIRST_FLOOR",
                        },
                    }
                ]
            }
        ]
    }

    benchmarks = replay_property_rent_benchmarks(source_bundle)

    assert len(benchmarks) == 1
    benchmark = benchmarks[0]
    assert benchmark.region_code == "11"
    assert benchmark.property_class.value == "SMALL_RETAIL"
    assert benchmark.period == "2026Q2"
    assert benchmark.effective_rent_krw_per_sqm_month == 95_000
    assert benchmark.conversion_rate_bps == 680
    assert benchmark.coverage_status == "PARENT_REGION"


def test_selective_recompute_replays_grounded_franchise_finance_profile() -> None:
    source_bundle = {
        "candidates": [
            {
                "case_type": "FRANCHISE",
                "display_name": "메가MGC커피",
                "franchise": {
                    "brand_id": "kr-mega-mgc-coffee",
                    "eligibility": "VERIFIED",
                    "eligibility_evidence_refs": ["eligibility:mega"],
                    "finance_profile": {
                        "currency": "KRW",
                        "coverage": "PARTIAL",
                        "value_kind": "EVIDENCED_FACT",
                        "known_initial_cost_range_krw": {
                            "low": 127_890_000,
                            "base": 127_890_000,
                            "high": 127_890_000,
                        },
                        "reference_area_sqm": 33,
                        "monthly_royalty_krw": None,
                        "sales_royalty_bps": 300,
                        "evidence_refs": ["ftc:mega:2024:startup-cost-schedule"],
                        "source_refs": [
                            "https://www.data.go.kr/data/15110265/openapi.do"
                        ],
                        "scope_note": "공정거래위원회 신고연도 기준 공식 초기비용 합계",
                        "missing_costs": ["DEPOSIT", "OPERATING_RESERVE"],
                    },
                },
            }
        ]
    }

    universe = _franchise_universe(source_bundle)

    assert universe == [
        {
            "brand_id": "kr-mega-mgc-coffee",
            "display_name": "메가MGC커피",
            "individual_franchise_eligibility": "VERIFIED",
            "evidence_refs": ["eligibility:mega"],
            "finance_profile": source_bundle["candidates"][0]["franchise"][
                "finance_profile"
            ],
        }
    ]
    assert universe[0]["finance_profile"]["sales_royalty_bps"] == 300
