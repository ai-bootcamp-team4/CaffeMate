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


class ResultFreshness(StrEnum):
    CURRENT = "CURRENT"
    STALE = "STALE"


class ResultOutcomeStatus(StrEnum):
    REVIEWABLE_CANDIDATES = "REVIEWABLE_CANDIDATES"
    NO_REVIEWABLE_CANDIDATES = "NO_REVIEWABLE_CANDIDATES"


class ResultBundlePayload(StrictModel):
    candidates: list[dict[str, Any]] = Field(min_length=1, max_length=3)
    primary_candidate_id: str | None = Field(default=None, min_length=1)
    audit_status: AuditStatus
    outcome_status: ResultOutcomeStatus = ResultOutcomeStatus.REVIEWABLE_CANDIDATES

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
        excluded = [
            candidate.get("review_status") == "EXCLUDED"
            for candidate in self.candidates
        ]
        if self.outcome_status == ResultOutcomeStatus.NO_REVIEWABLE_CANDIDATES:
            if not all(excluded):
                raise ValueError("A no-reviewable result may contain only excluded candidates")
            if self.primary_candidate_id is not None:
                raise ValueError("A no-reviewable result cannot declare a primary candidate")
            if any(candidate.get("rank") is not None for candidate in self.candidates):
                raise ValueError("Excluded result candidates cannot have a rank")
            if any(
                candidate.get("is_primary_next_review") is not False
                for candidate in self.candidates
            ):
                raise ValueError("Excluded result candidates cannot be marked primary")
            return self
        if any(excluded):
            raise ValueError("Excluded candidates require a no-reviewable result")
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
    primary_candidate_id: str | None
    audit_status: AuditStatus
    outcome_status: ResultOutcomeStatus = ResultOutcomeStatus.REVIEWABLE_CANDIDATES
    created_at: datetime


class CandidateDecisionDelta(StrictModel):
    candidate_key: str
    display_name: str | None
    change_type: str
    previous_rank: int | None
    current_rank: int | None
    previous_review_status: str | None
    current_review_status: str | None
    initial_cash_base_delta_krw: int | None
    monthly_fixed_cost_base_delta_krw: int | None
    break_even_monthly_sales_delta_krw: int | None


class ResultDecisionDelta(StrictModel):
    previous_result_bundle_id: str
    current_result_bundle_id: str
    primary_candidate_changed: bool
    candidate_changes: list[CandidateDecisionDelta]
    requires_human_review: bool = False
    human_review_reason_codes: list[str] = Field(default_factory=list)


class ResultView(ResultBundle):
    freshness: ResultFreshness
    stale_head_dimensions: list[str]
    current_head: HeadFence
    decision_delta: ResultDecisionDelta | None = None
    invalidation_reason_codes: list[str] = Field(default_factory=list)
