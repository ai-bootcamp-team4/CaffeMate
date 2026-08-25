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
    assert delta.requires_human_review is True
    assert delta.human_review_reason_codes == [
        "MATERIAL_BREAK_EVEN_CHANGE",
        "MATERIAL_INITIAL_CASH_CHANGE",
        "MATERIAL_MONTHLY_COST_CHANGE",
        "REVIEW_STATUS_CHANGED",
    ]


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
    assert delta.requires_human_review is True
    assert [value.change_type for value in delta.candidate_changes] == ["ADDED", "REMOVED"]
    assert all(
        value.initial_cash_base_delta_krw is None for value in delta.candidate_changes
    )


def test_result_delta_explains_changed_inputs_reasons_and_gate_transition() -> None:
    previous_candidate = candidate(candidate_id="candidate-1")
    previous_candidate["reason_codes"] = ["MINIMUM_INITIAL_CASH_EXCEEDS_OWN_FUNDS"]
    previous_candidate["decision_inputs"] = [
        {
            "field": "CONSTRUCTION",
            "value_range_krw": {"low": 20_000_000, "base": 32_000_000, "high": 48_000_000},
            "provenance": "ASSUMPTION",
            "resolution_status": "ASSUMED",
            "decision_role": "FINANCE_INPUT",
            "source_title": "등록 창업안 가정",
            "source_ref": None,
            "data_date": None,
            "geographic_scope": None,
            "source_anchor": None,
            "applied_to": ["INITIAL_CASH", "CAPITAL_GATE", "RANK"],
            "replaceable_by": ["DOCUMENT_INTAKE"],
            "resolution_action": {
                "action_type": "DOCUMENT_INTAKE",
                "target_fields": ["finance.CONSTRUCTION"],
                "accepted_document_types": ["INTERIOR_QUOTE"],
            },
            "limitation_code": "REPLACE_WITH_CASE_DATA",
        }
    ]
    previous_candidate["gate_results"] = [
        {
            "gate_type": "CAPITAL",
            "status": "FAIL",
            "reason_code": "MINIMUM_INITIAL_CASH_EXCEEDS_OWN_FUNDS",
            "decisive_input_refs": ["founder.own_funds_krw", "finance.initial_cash.low"],
            "metrics": {
                "own_funds_krw": 100_000_000,
                "minimum_required_krw": 110_000_000,
                "maximum_required_krw": 150_000_000,
                "shortfall_krw": 10_000_000,
            },
        }
    ]
    current_candidate = deepcopy(previous_candidate)
    current_candidate["reason_codes"] = ["OWN_FUNDS_COVER_HIGH_SCENARIO"]
    current_candidate["decision_inputs"][0].update(
        {
            "value_range_krw": {
                "low": 26_400_000,
                "base": 26_400_000,
                "high": 26_400_000,
            },
            "provenance": "USER_INPUT",
            "resolution_status": "RESOLVED_USER_CONFIRMED",
            "source_title": "interior.pdf",
            "source_anchor": "revision-1#page=1",
            "replaceable_by": [],
            "resolution_action": {
                "action_type": "NONE",
                "target_fields": [],
                "accepted_document_types": [],
            },
            "limitation_code": None,
        }
    )
    current_candidate["gate_results"][0].update(
        {
            "status": "PASS",
            "reason_code": "OWN_FUNDS_COVER_HIGH_SCENARIO",
            "metrics": {
                "own_funds_krw": 100_000_000,
                "minimum_required_krw": 80_000_000,
                "maximum_required_krw": 95_000_000,
                "shortfall_krw": 0,
            },
        }
    )

    delta = build_result_decision_delta(
        previous_result_bundle_id="result-old",
        current_result_bundle_id="result-new",
        previous_bundle={"candidates": [previous_candidate], "primary_candidate_id": "candidate-1"},
        current_bundle={"candidates": [current_candidate], "primary_candidate_id": "candidate-1"},
    )

    change = delta.candidate_changes[0]
    assert change.reason_codes_removed == ["MINIMUM_INITIAL_CASH_EXCEEDS_OWN_FUNDS"]
    assert change.reason_codes_added == ["OWN_FUNDS_COVER_HIGH_SCENARIO"]
    assert len(change.input_changes) == 1
    input_change = change.input_changes[0]
    assert input_change.field == "CONSTRUCTION"
    assert input_change.previous is not None
    assert input_change.previous.provenance == "ASSUMPTION"
    assert input_change.current is not None
    assert input_change.current.provenance == "USER_INPUT"
    assert input_change.affected_calculations == ["CAPITAL_GATE", "INITIAL_CASH", "RANK"]
    assert len(change.gate_transitions) == 1
    transition = change.gate_transitions[0]
    assert transition.gate_type == "CAPITAL"
    assert transition.previous_status == "FAIL"
    assert transition.current_status == "PASS"
    assert transition.previous_metrics["shortfall_krw"] == 10_000_000
    assert transition.current_metrics["shortfall_krw"] == 0
    assert "DECISION_INPUT_CHANGED" in delta.human_review_reason_codes
    assert "GATE_DECISION_CHANGED" in delta.human_review_reason_codes
