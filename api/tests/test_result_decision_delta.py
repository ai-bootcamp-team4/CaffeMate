from copy import deepcopy

from app.results.delta import build_result_decision_delta
from tests.test_result_bundle import candidate


def test_result_delta_matches_stable_candidate_identity_and_calculates_amounts() -> None:
    previous_candidate = candidate(candidate_id="old-generated-id")
    previous_candidate["financial_summary"]["break_even_monthly_sales_krw"] = 15_000_000
    current_candidate = deepcopy(previous_candidate)
    current_candidate["candidate_id"] = "new-generated-id"
    current_candidate["financial_summary"]["initial_cash"]["base"] = 50_000_000
    current_candidate["financial_summary"]["monthly_fixed_cost"]["base"] = 6_500_000
    current_candidate["financial_summary"]["break_even_monthly_sales_krw"] = 16_000_000
    current_candidate["review_status"] = "CONDITIONAL_REVIEW"
    delta = build_result_decision_delta(
        previous_result_bundle_id="result-old",
        current_result_bundle_id="result-new",
        previous_bundle={
            "candidates": [previous_candidate],
            "primary_candidate_id": "old-generated-id",
        },
        current_bundle={
            "candidates": [current_candidate],
            "primary_candidate_id": "new-generated-id",
        },
    )

    assert delta.primary_candidate_changed is False
    assert len(delta.candidate_changes) == 1
    change = delta.candidate_changes[0]
    assert change.candidate_key == "INDEPENDENT:independent-v1"
    assert change.change_type == "UPDATED"
    assert change.initial_cash_base_delta_krw == 5_000_000
    assert change.monthly_fixed_cost_base_delta_krw == 500_000
    assert change.break_even_monthly_sales_delta_krw == 1_000_000
    assert change.previous_review_status == "REVIEW_RECOMMENDED"
    assert change.current_review_status == "CONDITIONAL_REVIEW"


def test_result_delta_marks_removed_and_added_candidates_without_inventing_amounts() -> None:
    old = candidate(candidate_id="old")
    new = candidate(candidate_id="new")
    new["case_type"] = "FRANCHISE"
    new["independent_model"] = None
    new["franchise"] = {"brand_id": "brand-a"}
    delta = build_result_decision_delta(
        previous_result_bundle_id="result-old",
        current_result_bundle_id="result-new",
        previous_bundle={"candidates": [old], "primary_candidate_id": "old"},
        current_bundle={"candidates": [new], "primary_candidate_id": "new"},
    )

    assert delta.primary_candidate_changed is True
    assert [value.change_type for value in delta.candidate_changes] == ["ADDED", "REMOVED"]
    assert all(
        value.initial_cash_base_delta_krw is None for value in delta.candidate_changes
    )
