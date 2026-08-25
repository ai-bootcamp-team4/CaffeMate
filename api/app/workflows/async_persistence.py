import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

import rfc8785
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.domain.errors import WorkflowPreconditionError
from app.domain.models import VentureState
from app.workflows.first_proposal import FirstProposalStage, stage_input_digest
from app.workflows.models import HeadFence, WorkflowCode, WorkflowRun, WorkflowStatus
from app.workflows.persistence import (
    _insert_event,
    _insert_result_bundle,
    _insert_result_delta,
    _lock_project,
    _next_head,
    _persist_project_head,
    _set_current_result,
    _workflow_input_digest,
)
from app.workflows.progress import FIRST_PROPOSAL_PROGRESS_STAGES


def enqueue_first_proposal(
    connection: Connection,
    *,
    project_id: str,
    user_id: str,
    state: VentureState,
    policy_snapshot_id: str,
    seed_registry_id: str,
    now: datetime,
    new_id: Callable[[], str],
    source_workflow_run_id: str | None = None,
    source_result_bundle_id: str | None = None,
) -> WorkflowRun:
    """Persist an executable FIRST_PROPOSAL before any external work starts."""

    project = _lock_project(connection, project_id=project_id, user_id=user_id)
    if state.project_id != project_id or state.user_id != user_id:
        raise WorkflowPreconditionError("Workflow State ownership does not match the project")
    if int(project["current_state_version"]) != state.state_version:
        raise WorkflowPreconditionError("Workflow State is no longer current")

    head = _next_head(
        project,
        project_id=project_id,
        state=state,
        policy_snapshot_id=policy_snapshot_id,
        seed_registry_id=seed_registry_id,
    )
    workflow_run_id = new_id()
    input_digest = _workflow_input_digest(
        state=state,
        head=head,
        source_workflow_run_id=source_workflow_run_id,
    )
    execution_stage_run_id = new_id()
    execution_digest = stage_input_digest(
        workflow_run_id=workflow_run_id,
        stage_code=FirstProposalStage.RUN_PROPOSAL,
        head=head,
    )

    _persist_project_head(connection, project_id=project_id, head=head, now=now)
    _insert_queued_workflow_run(
        connection,
        workflow_run_id=workflow_run_id,
        project_id=project_id,
        user_id=user_id,
        head=head,
        input_digest=input_digest,
        source_workflow_run_id=source_workflow_run_id,
        source_result_bundle_id=source_result_bundle_id,
        now=now,
    )
    _insert_stage(
        connection,
        stage_run_id=execution_stage_run_id,
        workflow_run_id=workflow_run_id,
        stage_code=FirstProposalStage.RUN_PROPOSAL.value,
        input_digest=execution_digest,
        status="READY",
        now=now,
    )
    for progress_stage in FIRST_PROPOSAL_PROGRESS_STAGES:
        _insert_stage(
            connection,
            stage_run_id=new_id(),
            workflow_run_id=workflow_run_id,
            stage_code=progress_stage.value,
            input_digest=_progress_input_digest(
                workflow_run_id=workflow_run_id,
                stage_code=progress_stage.value,
                workflow_input_digest=input_digest,
            ),
            status="PENDING",
            now=now,
        )
    _insert_outbox(
        connection,
        workflow_run_id=workflow_run_id,
        stage_run_id=execution_stage_run_id,
        input_digest=execution_digest,
        now=now,
    )
    _insert_event(
        connection,
        workflow_run_id=workflow_run_id,
        event_type="WORKFLOW_QUEUED",
        data={"stage_code": FirstProposalStage.RUN_PROPOSAL.value},
        occurred_at=now,
    )
    return WorkflowRun(
        workflow_run_id=workflow_run_id,
        project_id=project_id,
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        status=WorkflowStatus.QUEUED,
        head=head,
        input_digest=input_digest,
        created_at=now,
        updated_at=now,
    )


def persist_first_proposal_result(
    connection: Connection,
    *,
    workflow_run_id: str,
    project_id: str,
    head: HeadFence,
    bundle: Any,
    source_result_bundle_id: str | None,
    execution_stage_run_id: str,
    now: datetime,
    new_id: Callable[[], str],
) -> dict[str, object]:
    """Atomically publish the result and close the already-running workflow."""

    result_bundle_id = new_id()
    bundle_json = bundle.model_dump(mode="json")
    _insert_result_bundle(
        connection,
        result_bundle_id=result_bundle_id,
        project_id=project_id,
        workflow_run_id=workflow_run_id,
        head=head,
        bundle_json=bundle_json,
        now=now,
    )
    _insert_result_delta(
        connection,
        project_id=project_id,
        result_bundle_id=result_bundle_id,
        source_result_bundle_id=source_result_bundle_id,
        bundle_json=bundle_json,
        now=now,
    )
    _set_current_result(connection, project_id=project_id, result_bundle_id=result_bundle_id)
    connection.execute(
        text(
            """
            UPDATE stage_runs
            SET status='SUCCEEDED', result_json=CAST(:result_json AS JSONB),
                updated_at=:now, completed_at=:now
            WHERE workflow_run_id=:workflow_run_id AND stage_code='COMMIT_RESULT'
            """
        ),
        {
            "result_json": json.dumps(
                {"outcome": "SUCCESS", "result_bundle_id": result_bundle_id},
                separators=(",", ":"),
            ),
            "now": now,
            "workflow_run_id": workflow_run_id,
        },
    )
    connection.execute(
        text(
            """
            UPDATE stage_runs
            SET status='SUCCEEDED', result_json=CAST(:result_json AS JSONB),
                lease_token_digest=NULL, lease_owner=NULL, lease_expires_at=NULL,
                updated_at=:now, completed_at=:now
            WHERE stage_run_id=:stage_run_id AND workflow_run_id=:workflow_run_id
            """
        ),
        {
            "result_json": json.dumps(
                {
                    "outcome": "SUCCESS",
                    "candidate_count": len(bundle.candidates),
                    "result_bundle_id": result_bundle_id,
                },
                separators=(",", ":"),
            ),
            "now": now,
            "stage_run_id": execution_stage_run_id,
            "workflow_run_id": workflow_run_id,
        },
    )
    connection.execute(
        text(
            "UPDATE workflow_runs SET status='SUCCEEDED', updated_at=:now "
            "WHERE workflow_run_id=:workflow_run_id"
        ),
        {"now": now, "workflow_run_id": workflow_run_id},
    )
    _insert_event(
        connection,
        workflow_run_id=workflow_run_id,
        event_type="WORKFLOW_SUCCEEDED",
        data={
            "stage_code": FirstProposalStage.RUN_PROPOSAL.value,
            "candidate_count": len(bundle.candidates),
            "result_bundle_id": result_bundle_id,
        },
        occurred_at=now,
    )
    return {
        "candidate_count": len(bundle.candidates),
        "result_bundle_id": result_bundle_id,
    }


def _insert_queued_workflow_run(
    connection: Connection,
    *,
    workflow_run_id: str,
    project_id: str,
    user_id: str,
    head: HeadFence,
    input_digest: str,
    source_workflow_run_id: str | None,
    source_result_bundle_id: str | None,
    now: datetime,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO workflow_runs(
                workflow_run_id, project_id, owner_user_id, workflow_code, status,
                workflow_generation, state_version, founder_snapshot_id,
                area_snapshot_id, evidence_snapshot_id, policy_snapshot_id,
                index_generation_id, seed_registry_id, input_digest,
                created_at, updated_at, source_workflow_run_id, source_result_bundle_id
            ) VALUES (
                :workflow_run_id, :project_id, :owner_user_id, 'FIRST_PROPOSAL', 'QUEUED',
                :workflow_generation, :state_version, :founder_snapshot_id,
                :area_snapshot_id, :evidence_snapshot_id, :policy_snapshot_id,
                :index_generation_id, :seed_registry_id, :input_digest,
                :created_at, :updated_at, :source_workflow_run_id, :source_result_bundle_id
            )
            """
        ),
        {
            "workflow_run_id": workflow_run_id,
            "project_id": project_id,
            "owner_user_id": user_id,
            **head.model_dump(mode="python"),
            "input_digest": input_digest,
            "created_at": now,
            "updated_at": now,
            "source_workflow_run_id": source_workflow_run_id,
            "source_result_bundle_id": source_result_bundle_id,
        },
    )


def _insert_stage(
    connection: Connection,
    *,
    stage_run_id: str,
    workflow_run_id: str,
    stage_code: str,
    input_digest: str,
    status: str,
    now: datetime,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO stage_runs(
                stage_run_id, workflow_run_id, stage_code, status,
                input_digest, attempt, created_at, updated_at
            ) VALUES (
                :stage_run_id, :workflow_run_id, :stage_code, :status,
                :input_digest, 0, :created_at, :updated_at
            )
            """
        ),
        {
            "stage_run_id": stage_run_id,
            "workflow_run_id": workflow_run_id,
            "stage_code": stage_code,
            "status": status,
            "input_digest": input_digest,
            "created_at": now,
            "updated_at": now,
        },
    )


def _progress_input_digest(
    *,
    workflow_run_id: str,
    stage_code: str,
    workflow_input_digest: str,
) -> str:
    return hashlib.sha256(
        rfc8785.dumps(
            {
                "workflow_run_id": workflow_run_id,
                "stage_code": stage_code,
                "workflow_input_digest": workflow_input_digest,
                "contract_version": "1.0.0",
            }
        )
    ).hexdigest()


def _insert_outbox(
    connection: Connection,
    *,
    workflow_run_id: str,
    stage_run_id: str,
    input_digest: str,
    now: datetime,
) -> None:
    payload = {
        "workflow_run_id": workflow_run_id,
        "stage_run_id": stage_run_id,
        "input_digest": input_digest,
    }
    payload_bytes = rfc8785.dumps(payload)
    connection.execute(
        text(
            """
            INSERT INTO workflow_outbox(
                topic, aggregate_id, payload_json, payload_digest,
                available_at, created_at
            ) VALUES (
                'WORKFLOW_STAGE_READY', :aggregate_id, CAST(:payload_json AS JSONB),
                :payload_digest, :available_at, :created_at
            )
            ON CONFLICT (topic, aggregate_id, payload_digest) DO NOTHING
            """
        ),
        {
            "aggregate_id": workflow_run_id,
            "payload_json": payload_bytes.decode(),
            "payload_digest": hashlib.sha256(payload_bytes).hexdigest(),
            "available_at": now,
            "created_at": now,
        },
    )