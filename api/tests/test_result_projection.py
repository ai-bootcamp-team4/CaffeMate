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
    value: int | float,
    unit: str,
    source_ref: str,
    data_date: str = "2026-03-31",
) -> dict[str, Any]:
    record = evidence_record(evidence_id)
    record.update(
        {
            "claim_type": claim_type,
            "metric": metric,
            "value": {
                "kind": "INTEGER" if isinstance(value, int) else "DECIMAL",
                "value": value,
            },
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


def official_document_evidence(
    evidence_id: str,
    *,
    claim_type: str,
    source_ref: str = "https://easylaw.go.kr/coffee-registration",
) -> dict[str, Any]:
    record = evidence_record(evidence_id)
    record.update(
        {
            "claim_type": claim_type,
            "value": {
                "kind": "STRING",
                "value": "휴게음식점 영업 신고 후 사업자등록을 진행합니다.",
            },
        }
    )
    source = record["source"]
    assert isinstance(source, dict)
    source.update(
        {
            "title": "커피전문점 영업신고 및 사업자등록",
            "source_ref": source_ref,
            "authority": "PRIMARY_OFFICIAL",
            "source_type": "WEB",
            "published_or_data_date": "2026-07-15",
            "document_version": "easylaw-csmSeq-706@2026-07-15",
            "checksum": "sha256:" + "a" * 64,
        }
    )
    record["original_anchor"] = {
        "anchor_type": "SECTION",
        "locator": f"{source_ref}#section=registration",
        "excerpt_hash": "sha256:" + "b" * 64,
    }
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
            "evidence-close-count",
            claim_type="AREA_BUSINESS_CHURN",
            metric="CLOSE_COUNT",
            value=6,
            unit="STORES_PER_QUARTER",
            source_ref="https://data.seoul.go.kr/dataList/OA-15577/S/1/datasetView.do",
        ),
        market_evidence(
            "evidence-closure-rate",
            claim_type="AREA_BUSINESS_CHURN",
            metric="CLOSURE_RATE",
            value=2.88,
            unit="PERCENT_DERIVED",
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
        market_evidence(
            "evidence-foot-traffic",
            claim_type="AREA_DEMAND_SIGNALS",
            metric="FOOT_TRAFFIC",
            value=12_465_323,
            unit="PERSON_VISITS_PER_QUARTER_ESTIMATE",
            source_ref="https://data.seoul.go.kr/dataList/OA-15568/S/1/datasetView.do",
        ),
        market_evidence(
            "evidence-resident-population",
            claim_type="AREA_DEMAND_SIGNALS",
            metric="RESIDENT_POPULATION",
            value=37_068,
            unit="PERSONS",
            source_ref="https://data.seoul.go.kr/dataList/OA-22182/S/1/datasetView.do",
        ),
        market_evidence(
            "evidence-worker-population",
            claim_type="AREA_DEMAND_SIGNALS",
            metric="WORKER_POPULATION",
            value=7_365,
            unit="PERSONS",
            source_ref="https://data.seoul.go.kr/dataList/OA-22184/A/1/datasetView.do",
        ),
    ]
    candidate["proposal"]["evidence_refs"] = [
        "evidence-cafe-count",
        "evidence-open-count",
        "evidence-estimated-sales",
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
        "CLOSE_COUNT",
        "CLOSURE_RATE",
        "ESTIMATED_SALES",
        "FOOT_TRAFFIC",
        "RESIDENT_POPULATION",
        "WORKER_POPULATION",
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


def test_projects_frozen_area_signal_without_proposal_ref_but_excludes_conflict() -> None:
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

    assert [signal["signal_type"] for signal in projected["market_signals"]] == [
        "ESTIMATED_SALES"
    ]
    assert projected["market_signals"][0]["evidence_id"] == "evidence-unlinked-sales"


def test_projects_frozen_area_signal_on_franchise_candidate() -> None:
    area_signal = market_evidence(
        "evidence-franchise-area-count",
        claim_type="AREA_CAFE_COMPETITION",
        metric="CAFE_COUNT",
        value=208,
        unit="STORES",
        source_ref="https://data.seoul.go.kr/dataList/OA-15577/S/1/datasetView.do",
    )
    calculated = CalculateGateRankStageHandler().execute(
        calculation_context(
            evidence_records=[area_signal],
            include_franchise=True,
        )
    )["calculate_gate_rank"]
    assert isinstance(calculated, dict)
    franchise = next(
        candidate
        for candidate in calculated["candidates"]
        if candidate["case_type"] == "FRANCHISE"
    )
    assert area_signal["evidence_id"] not in franchise["proposal"]["evidence_refs"]

    projected = project_candidate_results(
        [franchise],
        project_id="project-1",
        state_version=1,
        evidence_records=calculated["evidence_records"],
    )[0]

    assert [signal["signal_type"] for signal in projected["market_signals"]] == [
        "CAFE_COUNT"
    ]
    assert projected["market_signals"][0]["evidence_id"] == (
        "evidence-franchise-area-count"
    )


def test_projects_accepted_official_documents_and_explicit_gaps() -> None:
    calculated = CalculateGateRankStageHandler().execute(
        calculation_context(evidence_records=complete_independent_finance())
    )["calculate_gate_rank"]
    assert isinstance(calculated, dict)
    candidate = calculated["candidates"][0]
    records = calculated["evidence_records"]
    procedure = official_document_evidence(
        "evidence-official-procedure",
        claim_type="CAFE_OPENING_REQUIRED_PROCEDURES",
    )
    candidate["proposal"]["evidence_refs"] = [procedure["evidence_id"]]

    projected = project_candidate_results(
        [candidate],
        project_id="project-1",
        state_version=1,
        evidence_records=[*records, procedure],
    )[0]

    assert projected["official_documents"] == [
        {
            "title": "커피전문점 영업신고 및 사업자등록",
            "source_ref": "https://easylaw.go.kr/coffee-registration",
            "data_date": "2026-07-15",
            "freshness_status": "FRESH",
            "document_version": "easylaw-csmSeq-706@2026-07-15",
            "excerpt": "휴게음식점 영업 신고 후 사업자등록을 진행합니다.",
            "purposes": ["창업 절차 확인"],
            "evidence_refs": ["evidence-official-procedure"],
            "used_in_candidate": True,
        }
    ]
    assert projected["official_document_gaps"] == ["계약 전 확인 공식 문서"]


def test_official_document_search_miss_is_not_fabricated() -> None:
    calculated = CalculateGateRankStageHandler().execute(calculation_context())[
        "calculate_gate_rank"
    ]
    assert isinstance(calculated, dict)

    projected = project_candidate_results(
        [calculated["candidates"][0]],
        project_id="project-1",
        state_version=1,
        evidence_records=calculated["evidence_records"],
    )[0]

    assert projected["official_documents"] == []
    assert projected["official_document_gaps"] == [
        "창업 절차 공식 문서",
        "계약 전 확인 공식 문서",
    ]
