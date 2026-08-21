import re
from typing import Any

from app.selections.models import ChecklistStatus, EvidenceChecklistItem


def build_evidence_checklist(candidate: dict[str, Any]) -> list[EvidenceChecklistItem]:
    common = [
        ("PROPERTY_LISTING", "실제 점포 매물 자료"),
        ("LEASE_TERMS", "보증금·월세·관리비·권리금 조건"),
        ("PROPERTY_FLOOR_AREA", "점포 면적·층·시설 조건"),
        ("INTERIOR_QUOTE", "인테리어 견적"),
        ("EQUIPMENT_QUOTE", "장비 견적"),
        ("FUNDING_TERMS", "자기자금·대출 조건"),
    ]
    values = [
        EvidenceChecklistItem(
            code=code,
            title=title,
            status=ChecklistStatus.REQUIRED,
            reason="개념 후보를 실제 점포 조건으로 검증해야 합니다.",
        )
        for code, title in common
    ]
    if candidate.get("case_type") == "FRANCHISE":
        values.extend(
            [
                EvidenceChecklistItem(
                    code="FRANCHISE_DISCLOSURE",
                    title="최신 정보공개서",
                    status=ChecklistStatus.REQUIRED,
                    reason="가맹 비용·점포 현황·계약 조건의 공식 확인이 필요합니다.",
                ),
                EvidenceChecklistItem(
                    code="FRANCHISE_AGREEMENT",
                    title="가맹계약서 초안",
                    status=ChecklistStatus.REQUIRED,
                    reason="로열티·필수 구매·해지·위약 조건을 검토해야 합니다.",
                ),
                EvidenceChecklistItem(
                    code="HQ_AVAILABILITY",
                    title="본사 출점 가능 확인",
                    status=ChecklistStatus.HQ_CONFIRMATION_REQUIRED,
                    reason="해당 지역의 실제 출점 가능 여부는 본사가 확인해야 합니다.",
                ),
            ]
        )
    else:
        values.append(
            EvidenceChecklistItem(
                code="SUPPLIER_TERMS",
                title="원두·소모품 공급 조건",
                status=ChecklistStatus.REQUIRED,
                reason="개인카페의 실제 원가와 최소 주문 조건을 확인해야 합니다.",
            )
        )
    known_codes = {item.code for item in values}
    for missing in candidate.get("missing_fields", []):
        field = missing.get("field") if isinstance(missing, dict) else None
        if not isinstance(field, str) or not field:
            continue
        suffix = re.sub(r"[^A-Z0-9]+", "_", field.upper()).strip("_")
        code = f"RESULT_MISSING_{suffix}"[:64]
        if not suffix or code in known_codes:
            continue
        values.append(
            EvidenceChecklistItem(
                code=code,
                title=field,
                status=ChecklistStatus.MISSING_FROM_RESULT,
                reason=str(missing.get("next_check") or "결과에서 누락된 값을 확인해야 합니다."),
            )
        )
        known_codes.add(code)
    return values
