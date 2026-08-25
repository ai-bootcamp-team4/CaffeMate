import re
from typing import Any

from app.selections.models import ChecklistStatus, EvidenceChecklistItem


def build_evidence_checklist(candidate: dict[str, Any]) -> list[EvidenceChecklistItem]:
    """Project only decision-changing in-product actions and external confirmations.

    The checklist is intentionally derived from the current authoritative result.
    It must not resurrect a fixed "collect everything" list after a value has already
    been resolved or when the product has no action that can resolve it.
    """

    values: list[EvidenceChecklistItem] = []
    known_codes: set[str] = set()
    for decision_input in candidate.get("decision_inputs", []):
        if not isinstance(decision_input, dict):
            continue
        action = decision_input.get("resolution_action")
        if not isinstance(action, dict):
            continue
        action_type = action.get("action_type")
        field = decision_input.get("field")
        if not isinstance(field, str) or not field or action_type == "NONE":
            continue
        item = _refinable_item(field=field, action=action)
        if item is not None and item.code not in known_codes:
            values.append(item)
            known_codes.add(item.code)

    for requirement in candidate.get("verification_requirements", []):
        if not isinstance(requirement, dict):
            continue
        if requirement.get("status") != "EXTERNAL_CONFIRMATION_REQUIRED":
            continue
        requirement_id = requirement.get("requirement_id")
        if not isinstance(requirement_id, str) or not requirement_id:
            continue
        code = _code(requirement_id)
        if code in known_codes:
            continue
        values.append(
            EvidenceChecklistItem(
                code=code,
                title=_external_title(requirement_id),
                status=ChecklistStatus.EXTERNAL_CONFIRMATION_REQUIRED,
                reason=str(
                    requirement.get("why_caffemate_cannot_resolve")
                    or requirement.get("reason_code")
                    or "외부 확인이 필요한 항목입니다."
                ),
            )
        )
        known_codes.add(code)

    # Persisted pre-upgrade results can still be selected. Preserve their explicit
    # missing fields, but never recreate the old broad fixed checklist.
    if not candidate.get("decision_inputs"):
        for missing in candidate.get("missing_fields", []):
            field = missing.get("field") if isinstance(missing, dict) else None
            if not isinstance(field, str) or not field:
                continue
            code = f"RESULT_MISSING_{_code(field)}"[:64]
            if code in known_codes:
                continue
            values.append(
                EvidenceChecklistItem(
                    code=code,
                    title=field,
                    status=ChecklistStatus.MISSING_FROM_RESULT,
                    reason=str(
                        missing.get("next_check")
                        or "결과에서 누락된 값을 확인해야 합니다."
                    ),
                )
            )
            known_codes.add(code)
    return values


def _refinable_item(
    *,
    field: str,
    action: dict[str, Any],
) -> EvidenceChecklistItem | None:
    action_type = action.get("action_type")
    if action_type == "PROPERTY_TERMS":
        return EvidenceChecklistItem(
            code="PROPERTY_TERMS",
            title="실제 점포 임대 조건",
            status=ChecklistStatus.REFINABLE,
            reason="실제 보증금·월세·관리비·권리금으로 현재 참고값을 교체할 수 있습니다.",
        )
    if action_type == "DOCUMENT_INTAKE":
        accepted = [
            value
            for value in action.get("accepted_document_types", [])
            if isinstance(value, str)
        ]
        suffix = _code(field)
        return EvidenceChecklistItem(
            code=f"DOCUMENT_{suffix}"[:64],
            title=f"{field} 실제 문서 값",
            status=ChecklistStatus.REFINABLE,
            reason=(
                f"{', '.join(accepted)} 문서를 반영하면 이 값을 실제 조건으로 교체할 수 있습니다."
                if accepted
                else "관련 문서를 반영하면 이 값을 실제 조건으로 교체할 수 있습니다."
            ),
        )
    if action_type == "USER_INPUT":
        suffix = _code(field)
        return EvidenceChecklistItem(
            code=f"INPUT_{suffix}"[:64],
            title=f"{field} 실제 값",
            status=ChecklistStatus.REFINABLE,
            reason="사용자 실제 값을 입력하면 이 판단 입력을 다시 계산할 수 있습니다.",
        )
    return None


def _external_title(requirement_id: str) -> str:
    return {
        "FRANCHISE_AREA_APPROVAL": "본사 출점 가능 확인",
    }.get(requirement_id, requirement_id)


def _code(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_") or "UNKNOWN"
