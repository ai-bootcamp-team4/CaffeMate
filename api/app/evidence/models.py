from datetime import datetime
from enum import StrEnum

from pydantic import Field

from app.domain.models import StrictModel


class SourceAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"


class SourceObservation(StrictModel):
    source_ref: str = Field(min_length=1, max_length=2048)
    source_revision: str = Field(min_length=1, max_length=512)
    source_observed_at: datetime
    availability: SourceAvailability


class EvidenceRefreshRequest(StrictModel):
    project_id: str = Field(min_length=1)
    observations: list[SourceObservation] = Field(min_length=1, max_length=200)
    check_expiry: bool = True


class EvidenceRefreshResult(StrictModel):
    refresh_id: str
    project_id: str
    status: str
    changed_source_refs: list[str]
    expired_evidence_ids: list[str]
    affected_evidence_ids: list[str]
    invalidated_result_bundle_id: str | None
    recompute_workflow_run_id: str | None
    requires_human_review: bool
    reason_codes: list[str]
