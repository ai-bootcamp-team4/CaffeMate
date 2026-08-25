from app.selections.checklist import build_evidence_checklist
from app.selections.models import ChecklistStatus


def test_checklist_uses_actionable_unresolved_inputs_and_external_requirements_only() -> None:
    candidate = {
        "case_type": "FRANCHISE",
        "decision_inputs": [
            {
                "field": "DEPOSIT",
                "resolution_status": "ASSUMED",
                "decision_role": "FINANCE_INPUT",
                "resolution_action": {
                    "action_type": "PROPERTY_TERMS",
                    "target_fields": ["property.deposit_krw"],
                    "accepted_document_types": [],
                },
            },
            {
                "field": "EQUIPMENT",
                "resolution_status": "RESOLVED_USER_CONFIRMED",
                "decision_role": "FINANCE_INPUT",
                "resolution_action": {
                    "action_type": "NONE",
                    "target_fields": [],
                    "accepted_document_types": [],
                },
            },
            {
                "field": "CONSTRUCTION",
                "resolution_status": "ASSUMED",
                "decision_role": "FINANCE_INPUT",
                "resolution_action": {
                    "action_type": "DOCUMENT_INTAKE",
                    "target_fields": ["finance.CONSTRUCTION"],
                    "accepted_document_types": ["INTERIOR_QUOTE"],
                },
            },
        ],
        "verification_requirements": [
            {
                "requirement_id": "FRANCHISE_AREA_APPROVAL",
                "status": "EXTERNAL_CONFIRMATION_REQUIRED",
                "resolver": "FRANCHISE_HQ",
                "reason_code": "FRANCHISE_AREA_AVAILABILITY_UNCONFIRMED",
                "resolution_action": {
                    "action_type": "EXTERNAL_CONFIRMATION",
                    "target_fields": ["franchise.area_availability"],
                    "accepted_document_types": [],
                },
            }
        ],
        "missing_fields": [],
    }

    checklist = build_evidence_checklist(candidate)

    assert [(item.code, item.status) for item in checklist] == [
        ("PROPERTY_TERMS", ChecklistStatus.REFINABLE),
        ("DOCUMENT_CONSTRUCTION", ChecklistStatus.REFINABLE),
        ("FRANCHISE_AREA_APPROVAL", ChecklistStatus.EXTERNAL_CONFIRMATION_REQUIRED),
    ]
    assert all(item.code != "EQUIPMENT_QUOTE" for item in checklist)
    assert all(item.code != "FUNDING_TERMS" for item in checklist)
