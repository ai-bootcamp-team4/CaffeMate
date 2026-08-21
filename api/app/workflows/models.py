from datetime import datetime
from enum import StrEnum

from pydantic import Field

from app.domain.models import StrictModel


class WorkflowCode(StrEnum):
    FIRST_PROPOSAL = "FIRST_PROPOSAL"


class WorkflowStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"


class StageStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    CHECKPOINTED = "CHECKPOINTED"
    SUCCEEDED = "SUCCEEDED"
    SKIPPED = "SKIPPED"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    TIMED_OUT = "TIMED_OUT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class HeadFence(StrictModel):
    workflow_generation: int = Field(ge=1)
    state_version: int = Field(ge=1)
    founder_snapshot_id: str | None = Field(default=None, max_length=128)
    area_snapshot_id: str | None = Field(default=None, max_length=128)
    evidence_snapshot_id: str | None = Field(default=None, max_length=128)
    policy_snapshot_id: str = Field(min_length=1, max_length=128)
    index_generation_id: str | None = Field(default=None, max_length=128)
    seed_registry_id: str | None = Field(default=None, max_length=128)


class WorkflowRun(StrictModel):
    workflow_run_id: str
    project_id: str
    workflow_code: WorkflowCode
    status: WorkflowStatus
    head: HeadFence
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    updated_at: datetime
    cancelled_at: datetime | None = None


class WorkflowEvent(StrictModel):
    sequence_id: int = Field(ge=1)
    workflow_run_id: str
    event_type: str
    data: dict[str, object]
    occurred_at: datetime


class StageLease(StrictModel):
    workflow_run_id: str
    stage_run_id: str
    stage_code: str
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    lease_token: str
    lease_expires_at: datetime
    attempt: int = Field(ge=1)
    head: HeadFence


class CheckpointOutcome(StrEnum):
    APPLIED = "APPLIED"
    DUPLICATE_DISCARDED = "DUPLICATE_DISCARDED"
    LATE_DISCARDED = "LATE_DISCARDED"
    CANCELLED_DISCARDED = "CANCELLED_DISCARDED"
    STALE_DISCARDED = "STALE_DISCARDED"
    LEASE_REJECTED = "LEASE_REJECTED"


class StartWorkflowCommand(StrictModel):
    project_id: str
    user_id: str
    workflow_code: WorkflowCode
    idempotency_key: str = Field(min_length=1, max_length=255)


class CancelWorkflowCommand(StrictModel):
    project_id: str
    workflow_run_id: str
    user_id: str
    idempotency_key: str = Field(min_length=1, max_length=255)
