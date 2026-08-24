"""사용자는 실행 단계 수와 무관하게 승인된 근거가 결과 카드에 남아야 한다."""

from typing import Any

from app.results.projection import project_evidence_for_candidate
from tests.test_agent_boundary import evidence_record


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


def test_projects_grounded_market_signal_with_source_and_caveat() -> None:
    record = market_evidence(
        "evidence-cafe-count",
        claim_type="AREA_CAFE_COMPETITION",
        metric="CAFE_COUNT",
        value=208,
        unit="STORES",
        source_ref="https://data.seoul.go.kr/dataList/OA-15577/S/1/datasetView.do",
    )

    evidence_refs, signals, documents, gaps = project_evidence_for_candidate(
        [record],
        project_id="project-1",
        case_type="INDEPENDENT",
    )

    assert evidence_refs == ["evidence-cafe-count"]
    assert signals == [
        {
            "signal_type": "CAFE_COUNT",
            "value": 208,
            "unit": "STORES",
            "data_date": "2026-03-31",
            "freshness_status": "FRESH",
            "source_title": "서울시 상권분석서비스",
            "source_ref": (
                "https://data.seoul.go.kr/dataList/OA-15577/S/1/datasetView.do"
            ),
            "evidence_id": "evidence-cafe-count",
            "caveat": (
                "선택 지역에 연결된 행정동의 카페 업종 집계이며 "
                "개별 점포의 경쟁력을 뜻하지 않습니다."
            ),
        }
    ]
    assert documents == []
    assert gaps == ["창업 절차 공식 문서", "계약 전 확인 공식 문서"]


def test_excludes_conflicting_market_signal() -> None:
    conflicting = market_evidence(
        "evidence-conflicting-count",
        claim_type="AREA_CAFE_COMPETITION",
        metric="CAFE_COUNT",
        value=208,
        unit="STORES",
        source_ref="https://data.seoul.go.kr/dataList/OA-15577/S/1/datasetView.do",
    )
    conflicting["conflict_status"] = "CONFIRMED"

    evidence_refs, signals, _, _ = project_evidence_for_candidate(
        [conflicting],
        project_id="project-1",
        case_type="INDEPENDENT",
    )

    assert evidence_refs == []
    assert signals == []


def test_projects_accepted_official_document_as_candidate_evidence() -> None:
    procedure = official_document_evidence(
        "evidence-official-procedure",
        claim_type="CAFE_OPENING_REQUIRED_PROCEDURES",
    )

    evidence_refs, signals, documents, gaps = project_evidence_for_candidate(
        [procedure],
        project_id="project-1",
        case_type="INDEPENDENT",
    )

    assert evidence_refs == ["evidence-official-procedure"]
    assert signals == []
    assert documents == [
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
    assert gaps == ["계약 전 확인 공식 문서"]


def test_official_document_search_miss_is_not_fabricated() -> None:
    evidence_refs, signals, documents, gaps = project_evidence_for_candidate(
        [],
        project_id="project-1",
        case_type="FRANCHISE",
    )

    assert evidence_refs == []
    assert signals == []
    assert documents == []
    assert gaps == [
        "창업 절차 공식 문서",
        "계약 전 확인 공식 문서",
        "정보공개서 공식 문서",
    ]
