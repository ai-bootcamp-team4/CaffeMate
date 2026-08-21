from copy import deepcopy

import pytest

from app.domain.errors import ContractValidationError
from app.workflows.evidence_freeze import EvidenceFreezeStageHandler
from tests.test_agent_boundary import evidence_record
from tests.test_evidence_assess_stage import assess_context


def freeze_context(*, accepted: bool = True, cross_project: bool = False):
    value = assess_context()
    value.lease = value.lease.model_copy(
        update={"stage_run_id": "freeze-1", "stage_code": "EVIDENCE_FREEZE"}
    )
    record = evidence_record("evidence-area-profile")
    if cross_project:
        record["project_id"] = "another-project"
    assessment = {
        "claim_id": "claim:AREA_PROFILE",
        "candidate_ref": "evidence-area-profile",
        "relation": "SUPPORTS",
        "scope_status": "MATCH",
        "date_status": "MATCH",
        "freshness_status": "FRESH",
        "anchor_status": "VALID" if accepted else "MISSING",
        "authority_status": "ACCEPTABLE",
        "missing_context": [],
    }
    claims = value.dependency_results["EVIDENCE_RETRIEVAL"]["evidence_retrieval"]["claims"]
    value.dependency_results = {
        "EVIDENCE_ASSESS": {
            "evidence_assessment": {
                "status": "COMPLETE",
                "claims": claims,
                "assessments": [assessment],
                "missing_claims": [],
                "conflict_proposals": [],
                "evidence_refs": ["evidence-area-profile"],
                "missing_claim_ids": [] if accepted else ["claim:AREA_PROFILE"],
                "reason_codes": [],
                "warnings": [],
                "failed_actions": [],
                "retrieval_completeness": "COMPLETE",
                "executed_actions": [
                    {
                        "action_id": "action-01",
                        "claim_id": "claim:AREA_PROFILE",
                        "polarity": "SUPPORT",
                        "tool_name": "get_area_profile",
                        "request_id": "request-1",
                        "structured_result": {
                            "evidence_records": [record],
                        },
                    }
                ],
            }
        }
    }
    return value


def test_only_fully_validated_records_enter_immutable_snapshot() -> None:
    result = EvidenceFreezeStageHandler().execute(freeze_context())

    output = result["evidence_freeze"]
    assert isinstance(output, dict)
    assert output["snapshot_id"].startswith("evidence-")
    assert output["snapshot_digest"].startswith("sha256:")
    assert [record["evidence_id"] for record in output["evidence_records"]] == [
        "evidence-area-profile"
    ]
    assert output["missing_claim_ids"] == []


def test_invalid_anchor_never_enters_snapshot_and_missing_claim_survives() -> None:
    output = EvidenceFreezeStageHandler().execute(freeze_context(accepted=False))["evidence_freeze"]

    assert isinstance(output, dict)
    assert output["evidence_records"] == []
    assert output["missing_claim_ids"] == ["claim:AREA_PROFILE"]


def test_snapshot_id_and_digest_are_deterministic_for_same_frozen_input() -> None:
    handler = EvidenceFreezeStageHandler()
    first = handler.execute(freeze_context())["evidence_freeze"]
    second = handler.execute(freeze_context())["evidence_freeze"]

    assert first == second


def test_cross_project_record_is_rejected_before_checkpoint() -> None:
    with pytest.raises(ContractValidationError, match="crossed project scope"):
        EvidenceFreezeStageHandler().execute(freeze_context(cross_project=True))


def test_conflict_may_reference_only_accepted_records() -> None:
    value = freeze_context()
    assessment = value.dependency_results["EVIDENCE_ASSESS"]["evidence_assessment"]
    assessment["conflict_proposals"] = [
        {
            "claim_id": "claim:AREA_PROFILE",
            "candidate_refs": ["evidence-area-profile", "invented-evidence"],
            "reason": "값이 다름",
        }
    ]

    with pytest.raises(ContractValidationError, match="unaccepted Evidence"):
        EvidenceFreezeStageHandler().execute(deepcopy(value))


def test_franchise_universe_is_frozen_with_its_assessed_evidence() -> None:
    value = freeze_context()
    assessment = value.dependency_results["EVIDENCE_ASSESS"]["evidence_assessment"]
    assessment["executed_actions"][0]["tool_name"] = "list_franchise_universe"
    assessment["executed_actions"][0]["structured_result"]["data"] = [
        {
            "brand_id": "brand-1",
            "display_name": "검증 브랜드",
            "individual_franchise_eligibility": "VERIFIED",
            "eligibility_evidence_id": "evidence-area-profile",
            "disclosure_status": "MISSING",
        }
    ]

    output = EvidenceFreezeStageHandler().execute(value)["evidence_freeze"]

    assert isinstance(output, dict)
    assert output["franchise_universe"] == [
        {
            "brand_id": "brand-1",
            "display_name": "검증 브랜드",
            "individual_franchise_eligibility": "VERIFIED",
            "eligibility_evidence_id": "evidence-area-profile",
            "disclosure_status": "MISSING",
        }
    ]
