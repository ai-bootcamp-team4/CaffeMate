"""사용자 변경은 현재 State를 기준으로 durable 결정론적 재계산 Workflow를 queue한다."""

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.candidates.seed_registry import IndependentSeedRegistry
from app.domain.errors import FeedbackPreconditionError
from app.domain.models import VentureState
from app.workflows.async_persistence import enqueue_first_proposal
from app.workflows.models import HeadFence, WorkflowRun


def start_selective_first_proposal(
    connection: Connection,
    *,
    project_id: str,
    user_id: str,
    state: VentureState,
    source_workflow_run_id: str,
    previous_head: HeadFence,
    now: datetime,
    new_id: Callable[[], str],
) -> WorkflowRun:
    """현재 State를 queue하고 Worker 실행 시 저장된 점포 조건을 다시 읽는다."""

    source_result = connection.execute(
        text(
            "SELECT result_bundle_id FROM result_bundles "
            "WHERE workflow_run_id=:workflow_run_id AND project_id=:project_id"
        ),
        {
            "workflow_run_id": source_workflow_run_id,
            "project_id": project_id,
        },
    ).mappings().one_or_none()
    if source_result is None:
        raise FeedbackPreconditionError("Recompute requires a committed source result")

    registry = IndependentSeedRegistry.load_default()
    return enqueue_first_proposal(
        connection,
        project_id=project_id,
        user_id=user_id,
        state=state,
        policy_snapshot_id=previous_head.policy_snapshot_id,
        seed_registry_id=registry.registry_id,
        now=now,
        new_id=new_id,
        source_workflow_run_id=source_workflow_run_id,
        source_result_bundle_id=str(source_result["result_bundle_id"]),
    )
