"""사용자 변경은 현재 State와 저장된 실제 조건으로 결과를 한 번 다시 계산한다."""

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.candidates.seed_registry import IndependentSeedRegistry
from app.domain.errors import FeedbackPreconditionError
from app.domain.models import VentureState
from app.workflows.models import HeadFence, WorkflowRun
from app.workflows.persistence import persist_completed_first_proposal
from app.workflows.simple_proposal import PropertyCostOverride, SimpleProposalBuilder


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
    property_cost_override: PropertyCostOverride | None = None,
) -> WorkflowRun:
    """현재 State를 다시 계산하되 사용자가 확정한 점포 조건은 계속 보존한다."""

    source_result_bundle_id = connection.execute(
        text(
            "SELECT result_bundle_id FROM result_bundles "
            "WHERE workflow_run_id=:workflow_run_id AND project_id=:project_id"
        ),
        {
            "workflow_run_id": source_workflow_run_id,
            "project_id": project_id,
        },
    ).scalar_one_or_none()
    if source_result_bundle_id is None:
        raise FeedbackPreconditionError("Recompute requires a committed source result")

    registry = IndependentSeedRegistry.load_default()
    return persist_completed_first_proposal(
        connection,
        project_id=project_id,
        user_id=user_id,
        state=state,
        policy_snapshot_id=previous_head.policy_snapshot_id,
        seed_registry_id=registry.registry_id,
        builder=SimpleProposalBuilder(registry),
        now=now,
        new_id=new_id,
        source_workflow_run_id=source_workflow_run_id,
        source_result_bundle_id=str(source_result_bundle_id),
        property_cost_override=property_cost_override,
    )
