from app.grounding.franchise_disclosure_ingest import normalize_franchise_disclosure


def test_normalize_franchise_disclosure_joins_exact_ftc_brand_identity_and_converts_thousand_krw(
) -> None:
    brands, facts = normalize_franchise_disclosure(
        brand_rows=[
            {
                "jngBizStrtDate": "20011201",
                "jngBizCrtraYr": "2024",
                "brandMnno": "B-EDIYA",
                "jnghdqrtrsMnno": "H-EDIYA",
                "brno": "1234567890",
                "crno": "1101110000000",
                "corpNm": "(주)이디야",
                "jnghdqrtrsRprsvNm": "문창기",
                "brandNm": "이디야커피",
                "indutyLclasNm": "외식",
                "indutyMlsfcNm": "커피",
                "majrGdsNm": "커피",
            }
        ],
        startup_cost_rows=[
            {
                "yr": "2024",
                "indutyLclasNm": "외식",
                "indutyMlsfcNm": "커피",
                "brandNm": "이디야커피",
                "corpNm": "(주)이디야",
                "jngBzmnJngAmt": 9900,
                "jngBzmnEduAmt": 3300,
                "jngBzmnAssrncAmt": 5000,
                "jngBzmnEtcAmt": 109690,
                "smtnAmt": 127890,
            }
        ],
        ingestion_id="ftc-1",
        loaded_at="2026-08-25T06:00:00Z",
    )

    assert brands == [
        {
            "ingestion_id": "ftc-1",
            "reporting_year": 2024,
            "brand_management_no": "B-EDIYA",
            "headquarters_management_no": "H-EDIYA",
            "brand_name": "이디야커피",
            "corporation_name": "(주)이디야",
            "business_registration_no": "1234567890",
            "corporate_registration_no": "1101110000000",
            "business_start_date": "2001-12-01",
            "industry_major": "외식",
            "industry_middle": "커피",
            "loaded_at": "2026-08-25T06:00:00Z",
        }
    ]
    assert [(fact["field"], fact["value_krw"]) for fact in facts] == [
        ("FRANCHISE_FEE", 9_900_000),
        ("EDUCATION_FEE", 3_300_000),
        ("FRANCHISEE_DEPOSIT", 5_000_000),
        ("OTHER_INITIAL_FEE", 109_690_000),
        ("FRANCHISE_INITIAL_FEE_TOTAL", 127_890_000),
    ]
    assert all(fact["unit"] == "KRW" for fact in facts)
    assert all(fact["brand_management_no"] == "B-EDIYA" for fact in facts)


def test_normalize_franchise_disclosure_rejects_total_that_disagrees_with_components() -> None:
    try:
        normalize_franchise_disclosure(
            brand_rows=[
                {
                    "jngBizCrtraYr": "2024",
                    "brandMnno": "B-1",
                    "jnghdqrtrsMnno": "H-1",
                    "brandNm": "테스트커피",
                    "corpNm": "테스트",
                    "indutyLclasNm": "외식",
                    "indutyMlsfcNm": "커피",
                }
            ],
            startup_cost_rows=[
                {
                    "yr": "2024",
                    "brandNm": "테스트커피",
                    "corpNm": "테스트",
                    "indutyLclasNm": "외식",
                    "indutyMlsfcNm": "커피",
                    "jngBzmnJngAmt": 1,
                    "jngBzmnEduAmt": 2,
                    "jngBzmnAssrncAmt": 3,
                    "jngBzmnEtcAmt": 4,
                    "smtnAmt": 11,
                }
            ],
            ingestion_id="ftc-1",
            loaded_at="2026-08-25T06:00:00Z",
        )
    except RuntimeError as error:
        assert str(error) == "FTC_STARTUP_COST_TOTAL_MISMATCH:테스트커피:2024"
    else:
        raise AssertionError("mismatching FTC total must be rejected")