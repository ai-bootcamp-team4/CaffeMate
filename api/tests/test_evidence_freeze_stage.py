from copy import deepcopy
from typing import Any, cast

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


def structured_metric_record(
    evidence_id: str,
    metric: str,
    *,
    authority: str = "PRIMARY_DATA",
    conflict_status: str = "NONE",
    freshness_status: str = "FRESH",
) -> dict[str, object]:
    record = cast(
        dict[str, Any],
        evidence_record(evidence_id, freshness_status=freshness_status),
    )
    record["claim_type"] = "AREA_DEMAND_SIGNALS"
    record["metric"] = metric
    record["unit"] = "PEOPLE"
    record["source"] = {
        **record["source"],
        "authority": authority,
        "source_type": "DATASET",
        "source_ref": f"https://data.example/{metric.lower()}",
        "checksum": f"sha256:{'a' * 64}",
    }
    record["conflict_status"] = conflict_status
    record["durable_evidence_refs"] = [f"dataset-row:{metric.lower()}"]
    return cast(dict[str, object], record)


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


def test_accepted_structured_metric_includes_trusted_siblings_from_same_action() -> None:
    value = freeze_context()
    assessment = value.dependency_results["EVIDENCE_ASSESS"]["evidence_assessment"]
    representative = structured_metric_record("evidence-sales", "ESTIMATED_SALES")
    sibling = structured_metric_record("evidence-foot-traffic", "FOOT_TRAFFIC")
    assessment["assessments"][0]["candidate_ref"] = "evidence-sales"
    assessment["executed_actions"][0]["structured_result"]["evidence_records"] = [
        representative,
        sibling,
    ]

    output = EvidenceFreezeStageHandler().execute(value)["evidence_freeze"]

    assert isinstance(output, dict)
    assert [record["evidence_id"] for record in output["evidence_records"]] == [
        "evidence-foot-traffic",
        "evidence-sales",
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("authority", "SECONDARY"),
        ("conflict_status", "POTENTIAL"),
        ("freshness_status", "STALE"),
    ],
)
def test_untrusted_structured_sibling_is_not_implicitly_frozen(
    field: str,
    value: str,
) -> None:
    context = freeze_context()
    assessment = context.dependency_results["EVIDENCE_ASSESS"]["evidence_assessment"]
    representative = structured_metric_record("evidence-sales", "ESTIMATED_SALES")
    sibling = structured_metric_record("evidence-foot-traffic", "FOOT_TRAFFIC")
    if field == "authority":
        source = cast(dict[str, object], sibling["source"])
        source["authority"] = value
    else:
        sibling[field] = value
    assessment["assessments"][0]["candidate_ref"] = "evidence-sales"
    assessment["executed_actions"][0]["structured_result"]["evidence_records"] = [
        representative,
        sibling,
    ]

    output = EvidenceFreezeStageHandler().execute(context)["evidence_freeze"]

    assert isinstance(output, dict)
    assert [record["evidence_id"] for record in output["evidence_records"]] == [
        "evidence-sales"
    ]


def test_rag_or_web_sibling_is_not_implicitly_frozen() -> None:
    value = freeze_context()
    assessment = value.dependency_results["EVIDENCE_ASSESS"]["evidence_assessment"]
    representative = structured_metric_record("evidence-sales", "ESTIMATED_SALES")
    sibling = structured_metric_record("evidence-web", "FOOT_TRAFFIC")
    sibling_source = cast(dict[str, object], sibling["source"])
    sibling["source"] = {
        **sibling_source,
        "authority": "SECONDARY",
        "source_type": "WEB",
    }
    sibling["original_anchor"] = {
        "anchor_type": "SECTION",
        "locator": "section:1",
        "excerpt_hash": None,
    }
    assessment["assessments"][0]["candidate_ref"] = "evidence-sales"
    assessment["executed_actions"][0]["structured_result"]["evidence_records"] = [
        representative,
        sibling,
    ]

    output = EvidenceFreezeStageHandler().execute(value)["evidence_freeze"]

    assert isinstance(output, dict)
    assert [record["evidence_id"] for record in output["evidence_records"]] == [
        "evidence-sales"
    ]


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


def test_repeated_retrieval_of_same_evidence_ignores_only_observation_time() -> None:
    value = freeze_context()
    assessment = value.dependency_results["EVIDENCE_ASSESS"]["evidence_assessment"]
    repeated_action = deepcopy(assessment["executed_actions"][0])
    repeated_record = repeated_action["structured_result"]["evidence_records"][0]
    repeated_record["retrieved_at"] = "2026-08-21T09:00:01Z"
    repeated_record["source"]["source_observed_at"] = "2026-08-21T09:00:01Z"
    assessment["executed_actions"].append(repeated_action)

    output = EvidenceFreezeStageHandler().execute(value)["evidence_freeze"]

    assert len(output["evidence_records"]) == 1
    assert output["evidence_records"][0]["retrieved_at"] == "2026-08-21T09:00:00Z"


def test_repeated_evidence_id_with_changed_content_is_rejected() -> None:
    value = freeze_context()
    assessment = value.dependency_results["EVIDENCE_ASSESS"]["evidence_assessment"]
    repeated_action = deepcopy(assessment["executed_actions"][0])
    repeated_record = repeated_action["structured_result"]["evidence_records"][0]
    repeated_record["value"] = {"kind": "INTEGER", "value": 99999}
    assessment["executed_actions"].append(repeated_action)

    with pytest.raises(ContractValidationError, match="conflicting immutable records"):
        EvidenceFreezeStageHandler().execute(value)


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
