from typing import Any

from app.results.projection import project_candidate_results
from app.workflows.calculate_gate_rank import CalculateGateRankStageHandler
from tests.test_agent_boundary import evidence_record
from tests.test_calculate_gate_rank_stage import (
    calculation_context,
    complete_independent_finance,
)


def market_evidence(
    evidence_id: str,
    *,
    claim_type: str,
    metric: str,
    value: int,
    unit: str,
    source_ref: str,
    data_date: str = "2026-03-31",
) -> dict[str, Any]:
    record = evidence_record(evidence_id)
    record.update(
        {
            "claim_type": claim_type,
            "metric": metric,
            "value": {"kind": "INTEGER", "value": value},
            "unit": unit,
        }
    )
    source = record["source"]
    assert isinstance(source, dict)
    source.update(
        {
            "title": "서울시 상권분석서비스",
            "source_ref": source_ref,
            "published_or_data_date": data_date,
        }
    )
    return record


def test_projects_grounded_market_signals_with_source_and_caveat() -> None:
    calculated = CalculateGateRankStageHandler().execute(
        calculation_context(evidence_records=complete_independent_finance())
    )["calculate_gate_rank"]
    assert isinstance(calculated, dict)
    candidate = calculated["candidates"][0]
    records = calculated["evidence_records"]
    market_records = [
        market_evidence(
            "evidence-cafe-count",
            claim_type="AREA_CAFE_COMPETITION",
            metric="CAFE_COUNT",
            value=208,
            unit="STORES",
            source_ref="https://data.seoul.go.kr/dataList/OA-15577/S/1/datasetView.do",
        ),
        market_evidence(
            "evidence-open-count",
            claim_type="AREA_BUSINESS_CHURN",
            metric="OPEN_COUNT",
            value=9,
            unit="STORES_PER_QUARTER",
            source_ref="https://data.seoul.go.kr/dataList/OA-15577/S/1/datasetView.do",
        ),
        market_evidence(
            "evidence-estimated-sales",
            claim_type="AREA_DEMAND_SIGNALS",
            metric="ESTIMATED_SALES",
            value=2_596_733_728,
            unit="KRW_PER_QUARTER_ESTIMATE",
            source_ref="https://data.seoul.go.kr/dataList/OA-15572/A/1/datasetView.do",
        ),
    ]
    candidate["proposal"]["evidence_refs"] = [
        record["evidence_id"] for record in market_records
    ]

    projected = project_candidate_results(
        [candidate],
        project_id="project-1",
        state_version=1,
        evidence_records=[*records, *market_records],
    )[0]

    assert [signal["signal_type"] for signal in projected["market_signals"]] == [
        "CAFE_COUNT",
        "OPEN_COUNT",
        "ESTIMATED_SALES",
    ]
    assert projected["market_signals"][0] == {
        "signal_type": "CAFE_COUNT",
        "value": 208,
        "unit": "STORES",
        "data_date": "2026-03-31",
        "freshness_status": "FRESH",
        "source_title": "서울시 상권분석서비스",
        "source_ref": "https://data.seoul.go.kr/dataList/OA-15577/S/1/datasetView.do",
        "evidence_id": "evidence-cafe-count",
        "caveat": (
            "선택 지역에 연결된 행정동의 카페 업종 집계이며 "
            "개별 점포의 경쟁력을 뜻하지 않습니다."
        ),
    }


def test_does_not_project_unlinked_or_conflicting_market_evidence() -> None:
    calculated = CalculateGateRankStageHandler().execute(calculation_context())[
        "calculate_gate_rank"
    ]
    assert isinstance(calculated, dict)
    candidate = calculated["candidates"][0]
    linked = market_evidence(
        "evidence-conflicting-count",
        claim_type="AREA_CAFE_COMPETITION",
        metric="CAFE_COUNT",
        value=208,
        unit="STORES",
        source_ref="https://data.seoul.go.kr/dataList/OA-15577/S/1/datasetView.do",
    )
    linked["conflict_status"] = "CONFIRMED"
    unlinked = market_evidence(
        "evidence-unlinked-sales",
        claim_type="AREA_DEMAND_SIGNALS",
        metric="ESTIMATED_SALES",
        value=2_596_733_728,
        unit="KRW_PER_QUARTER_ESTIMATE",
        source_ref="https://data.seoul.go.kr/dataList/OA-15572/A/1/datasetView.do",
    )
    candidate["proposal"]["evidence_refs"] = [linked["evidence_id"]]

    projected = project_candidate_results(
        [candidate],
        project_id="project-1",
        state_version=1,
        evidence_records=[linked, unlinked],
    )[0]

    assert projected["market_signals"] == []
