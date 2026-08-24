"""사용자 피드백은 검증된 상태 변경 뒤 전체 제안을 한 번 다시 계산한다."""

from copy import deepcopy
from typing import Any

from app.agents.boundary import validate_agent_boundary
from app.agents.task_factory import FEEDBACK_ALLOWED_FIELD_PATHS
from app.domain.errors import ContractValidationError
from app.domain.models import FounderState
from app.workflows.models import HeadFence

_SCALAR_RULES: dict[str, tuple[str, set[str] | None]] = {
    "/founder/target_area_input": ("STRING", None),
    "/founder/own_funds_krw": ("INTEGER", None),
    "/founder/borrowing_intent": ("STRING", {"YES", "NO", "UNDECIDED"}),
    "/founder/cafe_type_preference": (
        "STRING",
        {"OPEN_TO_BOTH", "INDEPENDENT_ONLY", "FRANCHISE_ONLY"},
    ),
    "/founder/operation_mode": (
        "STRING",
        {"DIRECT_FULL_TIME", "DIRECT_PART_TIME", "EMPLOYEE_LED", "UNDECIDED"},
    ),
    "/founder/max_loss_krw": ("INTEGER", None),
}
_COLLECTION_FIELDS = {"/founder/preferences", "/founder/avoidances"}
_MAX_TARGET_AREA_LENGTH = 256
_MAX_COLLECTION_ITEM_LENGTH = 64
_MAX_COLLECTION_ITEMS = 8


def affected_feedback_stages(field_paths: set[str]) -> list[str]:
    if not field_paths:
        return []
    return ["RUN_PROPOSAL"]


def validate_intent_delta_result(
    *,
    task: dict[str, Any],
    result: dict[str, Any],
    current_head: HeadFence,
) -> dict[str, Any] | None:
    boundary = validate_agent_boundary(
        task=task,
        result=result,
        current_head=current_head,
    )
    if not boundary.accepted:
        codes = ",".join(error.code for error in boundary.errors)
        raise ContractValidationError(f"INTENT_DELTA boundary rejected: {codes}")
    if result["status"] != "COMPLETE":
        return None
    payload = result["payload"]
    if not isinstance(payload, dict):
        raise ContractValidationError("INTENT_DELTA complete result requires payload")
    decision = payload["decision"]
    operations = payload["operations"]
    questions = payload["clarifying_questions"]
    if decision == "PROPOSE_DELTA":
        if not operations or questions:
            raise ContractValidationError("Delta proposal must contain only operations")
        field_paths = [operation["field_path"] for operation in operations]
        if len(field_paths) != len(set(field_paths)):
            raise ContractValidationError("Delta proposal contains duplicate fields")
        for operation in operations:
            _validate_operation(task, operation)
    elif decision == "CLARIFY":
        if operations or not questions:
            raise ContractValidationError("Clarification must contain questions only")
    elif decision in {"NOOP", "UNSUPPORTED"}:
        if operations or questions:
            raise ContractValidationError("No-op feedback cannot contain changes or questions")
    else:
        raise ContractValidationError("INTENT_DELTA decision is unsupported")
    affected = payload["affected_workflow_codes"]
    if any(code != "FIRST_PROPOSAL" for code in affected):
        raise ContractValidationError("Feedback may only affect FIRST_PROPOSAL")
    if decision == "PROPOSE_DELTA" and affected != ["FIRST_PROPOSAL"]:
        raise ContractValidationError("State-changing feedback must rerun FIRST_PROPOSAL")
    return payload


def _validate_operation(task: dict[str, Any], operation: dict[str, Any]) -> None:
    field_path = operation["field_path"]
    allowed = set(task["payload"]["allowed_field_paths"])
    if field_path not in allowed or field_path not in FEEDBACK_ALLOWED_FIELD_PATHS:
        raise ContractValidationError("Feedback operation field is not allowed")
    if operation["ambiguity_codes"]:
        raise ContractValidationError("Ambiguous feedback must request clarification")
    if operation["semantic_kind"] not in {
        "HARD_CONSTRAINT",
        "SOFT_PREFERENCE",
        "USER_ASSERTION",
    }:
        raise ContractValidationError("Feedback semantic kind is invalid")
    founder = task["payload"]["current_state_projection"]["founder"]
    field_name = field_path.rsplit("/", 1)[-1]
    current = founder[field_name]
    if field_path in _COLLECTION_FIELDS:
        _validate_collection_operation(operation, current)
        return
    _validate_scalar_operation(operation, current, field_path)


def _validate_collection_operation(operation: dict[str, Any], current: object) -> None:
    if not isinstance(current, list):
        raise ContractValidationError("Feedback collection projection is invalid")
    kind = operation["kind"]
    typed = operation["typed_value"]
    expected = operation["expected_old_value"]
    if (
        typed.get("kind") != "STRING"
        or not isinstance(typed.get("value"), str)
        or not typed["value"].strip()
        or len(typed["value"]) > _MAX_COLLECTION_ITEM_LENGTH
    ):
        raise ContractValidationError("Feedback collection item must be a string")
    item = typed["value"]
    if kind == "ADD":
        if (
            expected != {"kind": "NULL", "value": None}
            or item in current
            or len(current) >= _MAX_COLLECTION_ITEMS
        ):
            raise ContractValidationError("Feedback collection add precondition failed")
        return
    if kind == "REMOVE":
        if expected != typed or item not in current:
            raise ContractValidationError("Feedback collection remove precondition failed")
        return
    raise ContractValidationError("Feedback collection only supports add or remove")


def _validate_scalar_operation(
    operation: dict[str, Any],
    current: object,
    field_path: str,
) -> None:
    kind = operation["kind"]
    expected = operation["expected_old_value"]
    typed = operation["typed_value"]
    if expected != _typed_value(current):
        raise ContractValidationError("Feedback scalar precondition does not match State")
    if kind == "UNSET" and field_path == "/founder/max_loss_krw":
        if typed != {"kind": "NULL", "value": None}:
            raise ContractValidationError("Feedback unset requires a null value")
        if current is None:
            raise ContractValidationError("Feedback scalar value is unchanged")
        return
    if kind != "SET":
        raise ContractValidationError("Feedback scalar only supports set")
    required_kind, choices = _SCALAR_RULES[field_path]
    if typed.get("kind") != required_kind:
        raise ContractValidationError("Feedback scalar type is invalid")
    value = typed.get("value")
    if required_kind == "INTEGER" and (
        isinstance(value, bool) or not isinstance(value, int) or value < 0
    ):
        raise ContractValidationError("Feedback money value is invalid")
    if required_kind == "STRING" and (not isinstance(value, str) or not value.strip()):
        raise ContractValidationError("Feedback string value is invalid")
    if choices is not None and value not in choices:
        raise ContractValidationError("Feedback enum value is invalid")
    if (
        field_path == "/founder/target_area_input"
        and isinstance(value, str)
        and len(value) > _MAX_TARGET_AREA_LENGTH
    ):
        raise ContractValidationError("Feedback string value is invalid")
    if value == current:
        raise ContractValidationError("Feedback scalar value is unchanged")


def _typed_value(value: object) -> dict[str, object]:
    if value is None:
        return {"kind": "NULL", "value": None}
    if isinstance(value, bool):
        return {"kind": "BOOLEAN", "value": value}
    if isinstance(value, int):
        return {"kind": "INTEGER", "value": value}
    if isinstance(value, str):
        return {"kind": "STRING", "value": value}
    raise ContractValidationError("Feedback State value cannot be represented")


def apply_feedback_operations(
    founder: FounderState,
    operations: list[dict[str, Any]],
) -> FounderState:
    """Apply an already validated delta while rechecking its State preconditions."""
    updated = deepcopy(founder.model_dump(mode="python"))
    for operation in operations:
        field_path = operation.get("field_path")
        if field_path not in FEEDBACK_ALLOWED_FIELD_PATHS:
            raise ContractValidationError("Feedback operation field is not allowed")
        field_name = field_path.rsplit("/", 1)[-1]
        current = updated[field_name]
        kind = operation.get("kind")
        typed = operation.get("typed_value")
        expected = operation.get("expected_old_value")
        if not isinstance(typed, dict) or not isinstance(expected, dict):
            raise ContractValidationError("Feedback operation values are invalid")
        value = typed.get("value")
        if kind == "ADD":
            if expected != {"kind": "NULL", "value": None} or not isinstance(
                current, list
            ):
                raise ContractValidationError("Feedback add precondition failed")
            updated[field_name] = [*current, value]
        elif kind == "REMOVE":
            if expected != typed or not isinstance(current, list) or value not in current:
                raise ContractValidationError("Feedback remove precondition failed")
            updated[field_name] = [item for item in current if item != value]
        elif kind in {"SET", "UNSET"}:
            if expected != _typed_value(current):
                raise ContractValidationError("Feedback scalar precondition failed")
            updated[field_name] = value
        else:
            raise ContractValidationError("Feedback operation is unsupported")
    return FounderState.model_validate(updated)
