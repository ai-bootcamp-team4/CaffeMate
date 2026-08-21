from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from app.contracts.schema_registry import CandidateContractValidator, ContractRegistry
from app.domain.models import StrictModel
from app.workflows.models import HeadFence


class AuditStatus(StrEnum):
    PASSED = "PASSED"
    REQUIRES_HUMAN = "REQUIRES_HUMAN"
    UNAVAILABLE = "UNAVAILABLE"


class ResultBundlePayload(StrictModel):
    candidates: list[dict[str, Any]] = Field(min_length=1, max_length=3)
    primary_candidate_id: str = Field(min_length=1)
    audit_status: AuditStatus

    @model_validator(mode="after")
    def rank_and_primary_are_consistent(self) -> "ResultBundlePayload":
        candidate_ids = [candidate.get("candidate_id") for candidate in self.candidates]
        if any(
            not isinstance(candidate_id, str) or not candidate_id
            for candidate_id in candidate_ids
        ):
            raise ValueError("Every result candidate requires candidate_id")
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("Result candidate ids must be unique")
        if any(candidate.get("review_status") == "EXCLUDED" for candidate in self.candidates):
            raise ValueError("Excluded candidates cannot enter the current result bundle")
        ranks = [candidate.get("rank") for candidate in self.candidates]
        if ranks != list(range(1, len(self.candidates) + 1)):
            raise ValueError("Result candidates must be ordered by contiguous rank")
        primary = [
            candidate
            for candidate in self.candidates
            if candidate.get("is_primary_next_review") is True
        ]
        if (
            len(primary) != 1
            or primary[0].get("candidate_id") != self.primary_candidate_id
            or primary[0].get("rank") != 1
        ):
            raise ValueError("Exactly rank 1 must be the primary next review candidate")
        return self

    def validate_contracts(
        self,
        *,
        project_id: str,
        state_version: int,
        contracts: CandidateContractValidator | None = None,
    ) -> None:
        validator = contracts or ContractRegistry()
        for candidate in self.candidates:
            validator.validate_candidate_result(candidate)
            if (
                candidate["project_id"] != project_id
                or candidate["state_version"] != state_version
            ):
                raise ValueError("Candidate project and state version must match the result head")


class ResultBundle(StrictModel):
    result_bundle_id: str
    project_id: str
    workflow_run_id: str
    head: HeadFence
    candidates: list[dict[str, Any]]
    primary_candidate_id: str
    audit_status: AuditStatus
    created_at: datetime
