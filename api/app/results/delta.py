from typing import Any

from app.finance.models import MoneyRange
from app.results.models import (
    CandidateDecisionDelta,
    DecisionInputChange,
    DecisionInputDeltaSnapshot,
    GateTransition,
    ResultDecisionDelta,
)


def build_result_decision_delta(
    *,
    previous_result_bundle_id: str,
    current_result_bundle_id: str,
    previous_bundle: dict[str, Any],
    current_bundle: dict[str, Any],
) -> ResultDecisionDelta:
    previous = _candidate_map(previous_bundle)
    current = _candidate_map(current_bundle)
    changes = [
        _candidate_delta(key, previous.get(key), current.get(key))
        for key in sorted(set(previous) | set(current))
    ]
    reason_codes = _human_review_reasons(changes, previous_bundle, current_bundle)
    return ResultDecisionDelta(
        previous_result_bundle_id=previous_result_bundle_id,
        current_result_bundle_id=current_result_bundle_id,
        primary_candidate_changed=(
            _primary_key(previous_bundle) != _primary_key(current_bundle)
        ),
        candidate_changes=changes,
        requires_human_review=bool(reason_codes),
        human_review_reason_codes=reason_codes,
    )


def _human_review_reasons(
    changes: list[CandidateDecisionDelta],
    previous_bundle: dict[str, Any],
    current_bundle: dict[str, Any],
) -> list[str]:
    reasons: set[str] = set()
    if _primary_key(previous_bundle) != _primary_key(current_bundle):
        reasons.add("PRIMARY_CANDIDATE_CHANGED")
    for change in changes:
        if change.change_type in {"ADDED", "REMOVED"}:
            reasons.add("CANDIDATE_SET_CHANGED")
        if change.previous_rank != change.current_rank:
            reasons.add("CANDIDATE_RANK_CHANGED")
        if change.previous_review_status != change.current_review_status:
            reasons.add("REVIEW_STATUS_CHANGED")
        if change.input_changes:
            reasons.add("DECISION_INPUT_CHANGED")
        if change.gate_transitions:
            reasons.add("GATE_DECISION_CHANGED")
        if change.reason_codes_added or change.reason_codes_removed:
            reasons.add("DECISION_REASON_CHANGED")
        if (
            change.initial_cash_base_delta_krw is not None
            and abs(change.initial_cash_base_delta_krw) >= 5_000_000
        ):
            reasons.add("MATERIAL_INITIAL_CASH_CHANGE")
        if (
            change.monthly_fixed_cost_base_delta_krw is not None
            and abs(change.monthly_fixed_cost_base_delta_krw) >= 500_000
        ):
            reasons.add("MATERIAL_MONTHLY_COST_CHANGE")
        if (
            change.break_even_monthly_sales_delta_krw is not None
            and abs(change.break_even_monthly_sales_delta_krw) >= 1_000_000
        ):
            reasons.add("MATERIAL_BREAK_EVEN_CHANGE")
    return sorted(reasons)


def _candidate_map(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values = bundle.get("candidates", [])
    if not isinstance(values, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for candidate in values:
        if not isinstance(candidate, dict):
            continue
        key = _candidate_key(candidate)
        if key is not None:
            result[key] = candidate
    return result


def _candidate_key(candidate: dict[str, Any]) -> str | None:
    case_type = candidate.get("case_type")
    if case_type == "INDEPENDENT":
        model = candidate.get("independent_model")
        source_id = model.get("model_id") if isinstance(model, dict) else None
    elif case_type == "FRANCHISE":
        franchise = candidate.get("franchise")
        source_id = franchise.get("brand_id") if isinstance(franchise, dict) else None
    else:
        return None
    return f"{case_type}:{source_id}" if isinstance(source_id, str) else None


def _primary_key(bundle: dict[str, Any]) -> str | None:
    primary_id = bundle.get("primary_candidate_id")
    candidates = bundle.get("candidates", [])
    if not isinstance(candidates, list):
        return None
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("candidate_id") == primary_id:
            return _candidate_key(candidate)
    return None


def _candidate_delta(
    key: str,
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> CandidateDecisionDelta:
    change_type = "ADDED" if previous is None else "REMOVED" if current is None else "UPDATED"
    current_or_previous = current or previous or {}
    return CandidateDecisionDelta(
        candidate_key=key,
        display_name=current_or_previous.get("display_name"),
        change_type=change_type,
        previous_rank=_integer(previous, "rank"),
        current_rank=_integer(current, "rank"),
        previous_review_status=_string(previous, "review_status"),
        current_review_status=_string(current, "review_status"),
        initial_cash_base_delta_krw=_financial_delta(previous, current, "initial_cash"),
        monthly_fixed_cost_base_delta_krw=_financial_delta(
            previous, current, "monthly_fixed_cost"
        ),
        break_even_monthly_sales_delta_krw=_scalar_financial_delta(
            previous, current, "break_even_monthly_sales_krw"
        ),
        reason_codes_added=sorted(_reason_codes(current) - _reason_codes(previous)),
        reason_codes_removed=sorted(_reason_codes(previous) - _reason_codes(current)),
        input_changes=_decision_input_changes(previous, current),
        gate_transitions=_gate_transitions(previous, current),
    )


def _financial_delta(
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
    field: str,
) -> int | None:
    old = _financial_summary(previous).get(field)
    new = _financial_summary(current).get(field)
    old_base = old.get("base") if isinstance(old, dict) else None
    new_base = new.get("base") if isinstance(new, dict) else None
    if not isinstance(old_base, int) or not isinstance(new_base, int):
        return None
    return new_base - old_base


def _scalar_financial_delta(
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
    field: str,
) -> int | None:
    old = _financial_summary(previous).get(field)
    new = _financial_summary(current).get(field)
    if not isinstance(old, int) or not isinstance(new, int):
        return None
    return new - old


def _financial_summary(candidate: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        return {}
    value = candidate.get("financial_summary")
    return value if isinstance(value, dict) else {}


def _integer(candidate: dict[str, Any] | None, field: str) -> int | None:
    value = candidate.get(field) if isinstance(candidate, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _string(candidate: dict[str, Any] | None, field: str) -> str | None:
    value = candidate.get(field) if isinstance(candidate, dict) else None
    return value if isinstance(value, str) else None


def _reason_codes(candidate: dict[str, Any] | None) -> set[str]:
    if not isinstance(candidate, dict):
        return set()
    values = candidate.get("reason_codes")
    if not isinstance(values, list):
        return set()
    return {value for value in values if isinstance(value, str)}


def _decision_input_changes(
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> list[DecisionInputChange]:
    old = _decision_input_map(previous)
    new = _decision_input_map(current)
    changes: list[DecisionInputChange] = []
    for field in sorted(set(old) | set(new)):
        previous_value = old.get(field)
        current_value = new.get(field)
        if previous_value == current_value:
            continue
        affected = sorted(
            set(_string_list(previous_value, "applied_to"))
            | set(_string_list(current_value, "applied_to"))
        )
        changes.append(
            DecisionInputChange(
                field=field,
                previous=_decision_input_snapshot(previous_value),
                current=_decision_input_snapshot(current_value),
                affected_calculations=affected,
            )
        )
    return changes


def _decision_input_map(candidate: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(candidate, dict):
        return {}
    values = candidate.get("decision_inputs")
    if not isinstance(values, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        field = value.get("field")
        if isinstance(field, str) and field:
            result[field] = value
    return result


def _decision_input_snapshot(
    value: dict[str, Any] | None,
) -> DecisionInputDeltaSnapshot | None:
    if not isinstance(value, dict):
        return None
    amount = value.get("value_range_krw")
    value_bps = value.get("value_bps")
    provenance = value.get("provenance")
    resolution_status = value.get("resolution_status")
    if (
        not isinstance(provenance, str)
        or not isinstance(resolution_status, str)
        or (amount is not None and not isinstance(amount, dict))
        or (value_bps is not None and not isinstance(value_bps, int))
    ):
        return None
    return DecisionInputDeltaSnapshot(
        value_range_krw=(MoneyRange.model_validate(amount) if isinstance(amount, dict) else None),
        value_bps=value_bps,
        provenance=provenance,
        resolution_status=resolution_status,
        source_title=_optional_string(value.get("source_title")),
        source_ref=_optional_string(value.get("source_ref")),
        source_anchor=_optional_string(value.get("source_anchor")),
    )


def _gate_transitions(
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> list[GateTransition]:
    old = _gate_map(previous)
    new = _gate_map(current)
    transitions: list[GateTransition] = []
    for gate_type in sorted(set(old) | set(new)):
        previous_value = old.get(gate_type)
        current_value = new.get(gate_type)
        previous_status = _mapping_string(previous_value, "status")
        current_status = _mapping_string(current_value, "status")
        previous_reason = _mapping_string(previous_value, "reason_code")
        current_reason = _mapping_string(current_value, "reason_code")
        if (
            previous_status == current_status
            and previous_reason == current_reason
        ):
            continue
        transitions.append(
            GateTransition(
                gate_type=gate_type,
                previous_status=previous_status,
                current_status=current_status,
                previous_reason_code=previous_reason,
                current_reason_code=current_reason,
                previous_metrics=_metrics(previous_value),
                current_metrics=_metrics(current_value),
            )
        )
    return transitions


def _gate_map(candidate: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(candidate, dict):
        return {}
    values = candidate.get("gate_results")
    if not isinstance(values, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        gate_type = value.get("gate_type")
        if isinstance(gate_type, str) and gate_type:
            result[gate_type] = value
    return result


def _metrics(value: dict[str, Any] | None) -> dict[str, int | None]:
    if not isinstance(value, dict) or not isinstance(value.get("metrics"), dict):
        return {}
    result: dict[str, int | None] = {}
    for key, item in value["metrics"].items():
        if isinstance(key, str) and (
            item is None or (isinstance(item, int) and not isinstance(item, bool))
        ):
            result[key] = item
    return result


def _string_list(value: dict[str, Any] | None, key: str) -> list[str]:
    if not isinstance(value, dict) or not isinstance(value.get(key), list):
        return []
    return [item for item in value[key] if isinstance(item, str)]


def _mapping_string(value: dict[str, Any] | None, key: str) -> str | None:
    return _optional_string(value.get(key)) if isinstance(value, dict) else None


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None
