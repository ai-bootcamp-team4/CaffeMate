import json
from typing import Any, Protocol

from app.documents.models import DocumentRevision, DocumentType, ParserBlock, ParserResultRequest


class DocumentRuntime(Protocol):
    def invoke(self, task: dict[str, Any]) -> dict[str, Any]: ...


_MARKER = "CAFFEMATE_DEMO_FIXTURE_V1:"


def _money(predicate: str, value: int, raw: str | None = None) -> dict[str, Any]:
    return {
        "predicate": predicate,
        "raw": raw or f"{predicate} {value:,}원",
        "typed_value": {"kind": "INTEGER", "value": value},
        "unit": "KRW",
        "currency": "KRW",
        "vat_status": "NOT_APPLICABLE",
    }


def _text(predicate: str, value: str, *, unit: str | None = None) -> dict[str, Any]:
    return {
        "predicate": predicate,
        "raw": value,
        "typed_value": {"kind": "STRING", "value": value},
        "unit": unit,
        "currency": None,
        "vat_status": "NOT_APPLICABLE",
    }


def _number(predicate: str, value: int | float, *, unit: str) -> dict[str, Any]:
    return {
        "predicate": predicate,
        "raw": f"{value}{unit}",
        "typed_value": {
            "kind": "INTEGER" if isinstance(value, int) else "DECIMAL",
            "value": value,
        },
        "unit": unit,
        "currency": None,
        "vat_status": "NOT_APPLICABLE",
    }


_FIXTURES: dict[str, tuple[DocumentType, list[dict[str, Any]]]] = {
    "05_demo_property_listing.pdf": (
        DocumentType.PROPERTY_LISTING,
        [
            _text("ADDRESS", "서울특별시 마포구 공덕동 100-1, 1층 101호"),
            _number("AREA", 33.06, unit="m²"),
            _text("FLOOR", "지상 1층"),
            _money("LEASE_DEPOSIT", 30_000_000, "보증금 3,000만원"),
            _money("MONTHLY_RENT", 2_200_000, "월세 220만원, 부가가치세 별도"),
            _money("MANAGEMENT_FEE", 200_000, "관리비 20만원, 전기·수도 별도"),
            _money("KEY_MONEY", 10_000_000, "권리금 1,000만원"),
        ],
    ),
    "01_demo_commercial_lease_terms.pdf": (
        DocumentType.COMMERCIAL_LEASE,
        [
            _money("LEASE_DEPOSIT", 30_000_000, "보증금 3,000만원"),
            _money("MONTHLY_RENT", 2_200_000, "월세 220만원, 부가가치세 별도"),
            _money("MANAGEMENT_FEE", 200_000, "관리비 20만원"),
            _money("KEY_MONEY", 10_000_000, "권리금 1,000만원"),
            _text("LEASE_TERM", "2026-09-01부터 2028-08-31까지, 24개월"),
            _number("AREA", 33.06, unit="m²"),
            _text("FLOOR", "지상 1층"),
            _text("TERMINATION_CONDITION", "3개월 전 서면 통지, 잔여기간 손해배상 별도 협의"),
            _text("RESTORATION_OBLIGATION", "임차인 설치물 철거 후 원상복구"),
        ],
    ),
    "03_demo_interior_quote.pdf": (
        DocumentType.INTERIOR_QUOTE,
        [
            {
                **_money("QUOTE_TOTAL", 26_400_000, "견적 총액 2,640만원, 부가가치세 포함"),
                "vat_status": "INCLUDED",
            },
            _text("VAT_STATUS", "포함"),
            _text("INCLUDED_WORK", "철거·바닥·도장·전기·급배수·조명·바 제작"),
            _text("EXCLUDED_WORK", "냉난방기·소방 보완·간판·가구·장비"),
            _text("PAYMENT_SCHEDULE", "계약 30%, 중도 40%, 잔금 30%"),
            _text("VALID_UNTIL", "2026-09-15"),
        ],
    ),
    "04_demo_equipment_quote.pdf": (
        DocumentType.EQUIPMENT_QUOTE,
        [
            {
                **_money("QUOTE_TOTAL", 19_800_000, "견적 총액 1,980만원, 부가가치세 포함"),
                "vat_status": "INCLUDED",
            },
            _text("VAT_STATUS", "포함"),
            _text("EQUIPMENT_ITEM", "에스프레소 머신·그라인더·제빙기·냉장고·쇼케이스"),
            _text("WARRANTY", "납품일부터 12개월, 소모품·사용자 과실 제외"),
            _text("DELIVERY_COST", "서울 지역 배송·설치 포함"),
            _text("VALID_UNTIL", "2026-09-15"),
        ],
    ),
    "02_demo_franchise_disclosure_summary.pdf": (
        DocumentType.FRANCHISE_DISCLOSURE,
        [
            _money("FRANCHISE_FEE", 12_000_000, "가맹비 1,200만원"),
            _money("EDUCATION_FEE", 2_000_000, "교육비 200만원"),
            _money("FRANCHISE_DEPOSIT", 5_000_000, "가맹보증금 500만원"),
            _number("ROYALTY", 3.0, unit="%"),
            _text("MANDATORY_PURCHASE", "원두·컵·시럽 지정 구매, 월 최소 300만원"),
            _money("AVERAGE_ANNUAL_SALES", 360_000_000, "가맹점 연평균 매출 3억 6,000만원"),
            _number("STORE_COUNT", 120, unit="개"),
            _number("CLOSURE_COUNT", 8, unit="개"),
        ],
    ),
    "06_demo_loan_terms.pdf": (
        DocumentType.LOAN_TERMS,
        [
            _money("PRINCIPAL", 20_000_000, "대출 원금 2,000만원"),
            _number("INTEREST_RATE", 5.2, unit="%"),
            _number("LOAN_PERIOD", 36, unit="개월"),
            _text("REPAYMENT_METHOD", "원리금 균등분할상환"),
            _text("COLLATERAL_REQUIREMENT", "무담보 가정, 실제 심사 시 보증·추가 서류 가능"),
        ],
    ),
}


def demo_parser_request(revision: DocumentRevision) -> ParserResultRequest | None:
    fixture = _FIXTURES.get(revision.original_filename)
    if fixture is None or fixture[0] != revision.document_type:
        return None
    claims = fixture[1]
    return ParserResultRequest(
        project_id=revision.project_id,
        document_id=revision.document_id,
        parser_version="caffemate-demo-fixture.v1",
        blocks=[
            ParserBlock.model_validate(
                {
                    "block_id": "demo-fixture-page-1",
                    "text": _MARKER + json.dumps(claims, ensure_ascii=False),
                    "anchor": {
                        "document_revision_id": revision.document_revision_id,
                        "page_index": 0,
                        "section_path": "데모 입력 예시",
                        "table_id": None,
                        "row": None,
                        "column": None,
                        "bbox": None,
                    },
                }
            )
        ],
        prompt_injection_flags=["DEMO_FIXTURE_NOT_REAL_DOCUMENT"],
    )


class DemoFixtureDocumentRuntime:
    def __init__(self, delegate: DocumentRuntime) -> None:
        self._delegate = delegate

    def invoke(self, task: dict[str, Any]) -> dict[str, Any]:
        payload = task.get("payload")
        blocks = payload.get("parser_blocks") if isinstance(payload, dict) else None
        first = blocks[0] if isinstance(blocks, list) and blocks else None
        text = first.get("text") if isinstance(first, dict) else None
        if not isinstance(text, str) or not text.startswith(_MARKER):
            return self._delegate.invoke(task)
        assert isinstance(payload, dict)
        assert isinstance(first, dict)
        fixture_claims = json.loads(text.removeprefix(_MARKER))
        claim_id_pool = payload["claim_id_pool"]
        revision_id = payload["document_revision"]["document_revision_id"]
        anchor = first["anchor"]
        claims = []
        for index, item in enumerate(fixture_claims):
            claims.append(
                {
                    "claim_id": claim_id_pool[index],
                    "predicate": item["predicate"],
                    "raw_value_text": item["raw"],
                    "typed_value": item["typed_value"],
                    "unit": item["unit"],
                    "currency": item["currency"],
                    "vat_status": item["vat_status"],
                    "inclusion_scope": "데모 입력 예시",
                    "effective_from": None,
                    "effective_to": None,
                    "valid_until": None,
                    "document_revision_id": revision_id,
                    "anchor": anchor,
                    "extraction_status": "REVIEW_REQUIRED",
                    "risk_flags": ["DEMO_FIXTURE_NOT_REAL_DOCUMENT"],
                }
            )
        return {
            "schema_version": "1.0.0",
            "task_id": task["task_id"],
            "invocation_id": task["invocation_id"],
            "agent_name": task["agent_name"],
            "task_type": task["task_type"],
            "workflow_run_id": task["workflow_run_id"],
            "stage_run_id": task["stage_run_id"],
            "venture_project_id": task["venture_project_id"],
            "head_fence_seen": task["head_fence"],
            "input_digest": task["input_digest"],
            "output_schema_id": task["output_schema_id"],
            "status": "COMPLETE",
            "payload": {
                "proposed_claims": claims,
                "unresolved_fields": [],
                "document_risk_flags": ["DEMO_FIXTURE_NOT_REAL_DOCUMENT"],
            },
            "evidence_refs": [],
            "missing_claim_ids": [],
            "reason_codes": [],
            "warnings": ["DEMO_FIXTURE_NOT_REAL_DOCUMENT"],
        }
