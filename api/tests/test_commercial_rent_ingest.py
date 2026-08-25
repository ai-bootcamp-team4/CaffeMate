from __future__ import annotations

import json

import httpx

from app.candidates.seed_registry import CommercialPropertyClass
from app.grounding.commercial_rent_ingest import (
    RebEasyStatClient,
    normalize_property_reference,
)


def _preview(item_code: str, *, seoul: float, busan: float) -> dict[str, object]:
    return {
        "DATA": [
            {
                "CATE1": "전국",
                "CATE2": "전국",
                "CATE3": "전국",
                f"COL_202602{item_code}OD": "30.0",
            },
            {
                "CATE1": "서울",
                "CATE2": "서울",
                "CATE3": "서울",
                f"COL_202601{item_code}OD": str(seoul - 0.5),
                f"COL_202602{item_code}OD": str(seoul),
            },
            {
                "CATE1": "부산",
                "CATE2": "부산",
                "CATE3": "부산",
                f"COL_202602{item_code}OD": str(busan),
            },
            {
                "CATE1": "서울",
                "CATE2": "영등포신촌",
                "CATE3": "공덕역",
                f"COL_202602{item_code}OD": "999.9",
            },
        ]
    }


def test_normalize_property_reference_uses_latest_common_quarter_and_province_rows() -> None:
    rows = normalize_property_reference(
        property_class=CommercialPropertyClass.SMALL_RETAIL,
        rent_table_id="T-rent",
        conversion_table_id="T-conversion",
        rent_preview=_preview("100001", seoul=42.5, busan=25.0),
        conversion_preview=_preview("100002", seoul=7.1, busan=7.5),
        ingestion_id="ingestion-1",
        loaded_at="2026-08-25T04:00:00Z",
    )

    assert [(row["region_code"], row["region_name"]) for row in rows] == [
        ("11", "서울"),
        ("26", "부산"),
    ]
    assert all(row["period_code"] == "2026Q2" for row in rows)
    assert rows[0]["effective_rent_krw_per_sqm_month"] == 42_500
    assert rows[0]["conversion_rate_bps"] == 710
    assert rows[0]["coverage_status"] == "PARENT_REGION"
    assert rows[0]["floor_basis"] == "FIRST_FLOOR"


def test_reb_client_fetches_page_parameter_then_preview_json() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200,
                text=(
                    '<input type="hidden" name="firParam" id="firParam" '
                    'value="statblId=T123&amp;dtacycleCd=QY&amp;wrttimeMaxYear=2026" />'
                ),
            )
        assert request.content.decode().endswith("&searchType=")
        return httpx.Response(200, json={"DATA": [{"CATE1": "서울"}]})

    client = RebEasyStatClient(
        base_url="https://reb.example.test/r-one",
        transport=httpx.MockTransport(handler),
    )

    snapshot = client.fetch_table("T123")

    assert calls == [
        ("GET", "/r-one/portal/stat/easyStatPage/T123.do"),
        ("POST", "/r-one/portal/stat/sttsDataPreviewList.do"),
    ]
    assert json.loads(snapshot.preview_json) == {"DATA": [{"CATE1": "서울"}]}
    assert snapshot.table_id == "T123"