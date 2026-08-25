from typing import Any

from app.finance.franchise_disclosure import resolve_franchise_disclosures


def _content(*, complete: bool = True, brand_id: str = "kr-ediya-coffee") -> dict[str, Any]:
    fields = [
        ("FRANCHISE_FEE", 9_900_000, "ftc:fee"),
        ("EDUCATION_FEE", 3_300_000, "ftc:education"),
        ("FRANCHISEE_DEPOSIT", 5_000_000, "ftc:deposit"),
        ("OTHER_INITIAL_FEE", 109_690_000, "ftc:other"),
        ("FRANCHISE_INITIAL_FEE_TOTAL", 127_890_000, "ftc:total"),
    ]
    if not complete:
        fields = [value for value in fields if value[0] != "OTHER_INITIAL_FEE"]
    data = [
        {
            "brand_id": brand_id,
            "brand_name": "이디야커피",
            "ftc_brand_management_no": "B-EDIYA",
            "ftc_headquarters_management_no": "H-EDIYA",
            "source_version": "FTC_COST_REPORTING_YEAR:2024:B-EDIYA",
            "disclosure_version": None,
            "disclosure_registration_date": None,
            "reporting_year": 2024,
            "field": field,
            "value": {"kind": "INTEGER", "value": amount},
            "unit": "KRW",
            "effective_date": "2024-12-31",
            "evidence_id": evidence_id,
        }
        for field, amount, evidence_id in fields
    ]
    evidence_records = [
        {
            "evidence_id": item["evidence_id"],
            "source": {
                "title": "공정거래위원회 브랜드별 창업 금액 현황",
                "source_ref": "https://www.data.go.kr/data/15110265/openapi.do",
                "published_or_data_date": "2024-12-31",
            },
            "original_anchor": {"locator": f"2024:B-EDIYA:{item['field']}"},
        }
        for item in data
    ]
    return {
        "tool_name": "get_franchise_disclosure",
        "status": "OK",
        "data": data,
        "evidence_records": evidence_records,
    }


def test_complete_accepted_ftc_components_replace_franchise_initial_fee() -> None:
    content = _content()
    accepted = {item["evidence_id"] for item in content["data"]}
    resolution = resolve_franchise_disclosures(
        structured_results=[content],
        accepted_evidence_ids=accepted,
        eligible_brand_ids={"kr-ediya-coffee"},
    )

    assert len(resolution.overrides) == 1
    value = resolution.overrides[0]
    assert value.source_id == "kr-ediya-coffee"
    assert value.amount.low == value.amount.base == value.amount.high == 127_890_000
    assert value.evidence_ref == "ftc:total"
    assert resolution.disclosure_evidence_refs["kr-ediya-coffee"] == sorted(accepted)


def test_incomplete_or_ineligible_ftc_facts_do_not_create_finance_override() -> None:
    incomplete = _content(complete=False)
    accepted = {item["evidence_id"] for item in incomplete["data"]}
    assert not resolve_franchise_disclosures(
        structured_results=[incomplete],
        accepted_evidence_ids=accepted,
        eligible_brand_ids={"kr-ediya-coffee"},
    ).overrides

    complete = _content(brand_id="not-a-proposal-brand")
    accepted_complete = {item["evidence_id"] for item in complete["data"]}
    assert not resolve_franchise_disclosures(
        structured_results=[complete],
        accepted_evidence_ids=accepted_complete,
        eligible_brand_ids={"kr-ediya-coffee"},
    ).overrides