from copy import deepcopy
from typing import Any

import pytest

from app.domain.errors import ContractValidationError
from app.workflows.candidate_audit import CandidateAuditStageHandler
from app.workflows.commit_result import CommitResultStageHandler
from app.workflows.stage_context import StageContext
from tests.test_candidate_audit_stage import audit_context, audit_result
from tests.test_proposal_stages import FakeRuntime


def commit_context(
    *,
    include_franchise: bool = False,
    audit_status: str = "PASS",
    findings: list[dict[str, Any]] | None = None,
) -> StageContext:
    source = audit_context(include_franchise=include_franchise)
    audit_output = CandidateAuditStageHandler(
        FakeRuntime(
            lambda task: audit_result(
                task,
                audit_status=audit_status,
                findings=findings,
            )
        )
    ).execute(source)
    return StageContext(
        lease=source.lease.model_copy(
            update={
                "stage_run_id": "stage-commit-result",
                "stage_code": "COMMIT_RESULT",
            }
        ),
        project_id=source.project_id,
        state=source.state,
        dependency_results={"CANDIDATE_AUDIT": audit_output},
    )


def output_candidate_audit(context: StageContext) -> dict[str, Any]:
    value = context.dependency_results["CANDIDATE_AUDIT"]["candidate_audit"]
    assert isinstance(value, dict)
    return value


def test_commit_result_builds_schema_valid_bundle_from_audited_candidates() -> None:
    context = commit_context(include_franchise=True)

    result = CommitResultStageHandler().execute(context)

    bundle = result["result_bundle"]
    assert isinstance(bundle, dict)
    assert bundle["audit_status"] == "PASSED"
    assert len(bundle["candidates"]) == 2
    assert [candidate["rank"] for candidate in bundle["candidates"]] == [1, 2]
    assert bundle["primary_candidate_id"] == bundle["candidates"][0]["candidate_id"]
    assert all(
        candidate["review_status"] != "EXCLUDED"
        for candidate in bundle["candidates"]
    )
    commit = result["commit_result"]
    assert isinstance(commit, dict)
    assert commit["status"] == "READY_TO_COMMIT"


def test_excluded_candidates_are_never_committed() -> None:
    context = commit_context(include_franchise=True)
    audit = output_candidate_audit(context)
    excluded = audit["candidates"][1]
    excluded.update(
        {
            "review_status": "EXCLUDED",
            "reason_codes": ["CONFIRMED_HARD_CONSTRAINT"],
            "rank": None,
            "rank_basis": "NOT_RANKED",
            "is_primary_next_review": False,
        }
    )

    result = CommitResultStageHandler().execute(context)

    bundle = result["result_bundle"]
    assert isinstance(bundle, dict)
    assert len(bundle["candidates"]) == 1
    assert excluded["candidate_id"] not in {
        candidate["candidate_id"] for candidate in bundle["candidates"]
    }


def test_no_reviewable_candidate_abstains_without_result_bundle() -> None:
    context = commit_context()
    audit = output_candidate_audit(context)
    candidate = audit["candidates"][0]
    candidate.update(
        {
            "review_status": "EXCLUDED",
            "reason_codes": ["CONFIRMED_HARD_CONSTRAINT"],
            "rank": None,
            "rank_basis": "NOT_RANKED",
            "is_primary_next_review": False,
        }
    )

    result = CommitResultStageHandler().execute(context)

    assert "result_bundle" not in result
    stage_control = result["stage_control"]
    assert isinstance(stage_control, dict)
    assert stage_control == {
        "disposition": "ABSTAIN",
        "reason_codes": ["NO_REVIEWABLE_CANDIDATES"],
    }


def test_only_first_three_ranked_candidates_are_committed() -> None:
    context = commit_context()
    audit = output_candidate_audit(context)
    first = audit["candidates"][0]
    audit["candidates"] = []
    audit["candidate_audits"] = []
    for rank in range(1, 5):
        candidate = deepcopy(first)
        candidate["candidate_id"] = f"candidate-{rank}"
        candidate["rank"] = rank
        candidate["is_primary_next_review"] = rank == 1
        audit["candidates"].append(candidate)
        audit["candidate_audits"].append(
            {"candidate_id": candidate["candidate_id"], "status": "PASS", "findings": []}
        )

    result = CommitResultStageHandler().execute(context)

    bundle = result["result_bundle"]
    assert isinstance(bundle, dict)
    assert [candidate["candidate_id"] for candidate in bundle["candidates"]] == [
        "candidate-1",
        "candidate-2",
        "candidate-3",
    ]
    commit = result["commit_result"]
    assert isinstance(commit, dict)
    assert commit["omitted_candidate_ids"] == ["candidate-4"]


def test_noncontiguous_rank_or_wrong_primary_is_rejected() -> None:
    context = commit_context(include_franchise=True)
    audit = output_candidate_audit(context)
    audit["candidates"][1]["rank"] = 3

    with pytest.raises(ContractValidationError, match="contiguous rank"):
        CommitResultStageHandler().execute(context)

    context = commit_context()
    output_candidate_audit(context)["candidates"][0]["is_primary_next_review"] = False
    with pytest.raises(ContractValidationError, match="exactly rank 1"):
        CommitResultStageHandler().execute(context)


def test_duplicate_or_hidden_tail_rank_is_rejected_before_truncation() -> None:
    context = commit_context()
    audit = output_candidate_audit(context)
    first = audit["candidates"][0]
    audit["candidates"] = []
    audit["candidate_audits"] = []
    for rank in range(1, 5):
        candidate = deepcopy(first)
        candidate["candidate_id"] = f"candidate-{rank}"
        candidate["rank"] = rank if rank < 4 else 5
        candidate["is_primary_next_review"] = rank == 1
        audit["candidates"].append(candidate)
        audit["candidate_audits"].append(
            {"candidate_id": candidate["candidate_id"], "status": "PASS", "findings": []}
        )

    with pytest.raises(ContractValidationError, match="before truncation"):
        CommitResultStageHandler().execute(context)

    audit["candidates"][-1]["rank"] = 4
    audit["candidates"][-1]["candidate_id"] = "candidate-3"
    audit["candidate_audits"][-1]["candidate_id"] = "candidate-3"
    with pytest.raises(ContractValidationError, match="identifiers must be unique"):
        CommitResultStageHandler().execute(context)


def test_serious_audit_finding_cannot_be_hidden_as_passed() -> None:
    context = commit_context()
    audit = output_candidate_audit(context)
    audit["candidate_audits"][0] = {
        "candidate_id": audit["candidates"][0]["candidate_id"],
        "status": "REQUIRES_HUMAN",
        "findings": [
            {
                "code": "MISSING_COST",
                "severity": "HIGH",
                "field_path": "/financial_summary",
                "claim_refs": [],
                "evidence_refs": [],
                "calculation_refs": [],
                "disposition": "REQUIRE_HUMAN",
            }
        ],
    }
    audit["status"] = "PASSED"

    with pytest.raises(ContractValidationError, match="cannot hide serious"):
        CommitResultStageHandler().execute(context)


def test_candidate_audit_coverage_is_revalidated_before_commit() -> None:
    context = commit_context()
    output_candidate_audit(context)["candidate_audits"] = []

    with pytest.raises(ContractValidationError, match="coverage is incomplete"):
        CommitResultStageHandler().execute(context)


def test_unavailable_audit_is_visible_without_changing_candidates() -> None:
    context = commit_context()
    audit = output_candidate_audit(context)
    original_candidates = deepcopy(audit["candidates"])
    audit["status"] = "UNAVAILABLE"
    audit["agent_status"] = "ABSTAIN"
    audit["candidate_audits"] = []
    audit["reason_codes"] = ["CANDIDATE_AUDIT_RUNTIME_UNAVAILABLE"]

    result = CommitResultStageHandler().execute(context)

    bundle = result["result_bundle"]
    assert isinstance(bundle, dict)
    assert bundle["audit_status"] == "UNAVAILABLE"
    assert bundle["candidates"] == original_candidates


def test_candidate_must_match_project_and_state_head() -> None:
    context = commit_context()
    output_candidate_audit(context)["candidates"][0]["project_id"] = "other-project"

    with pytest.raises(ContractValidationError, match="authoritative full head"):
        CommitResultStageHandler().execute(context)
