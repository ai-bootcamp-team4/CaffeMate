"""사용자 요청은 한 번 계산해 결과와 단일 실행 기록을 같은 트랜잭션에 저장한다."""

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

import rfc8785
from sqlalchemy import text
from sqlalchemy.engine import Connection, RowMapping

from app.domain.errors import ProjectNotFoundError, WorkflowPreconditionError
from app.domain.models import VentureState
from app.results.delta import build_result_decision_delta
from app.workflows.first_proposal import FirstProposalStage, stage_input_digest
from app.workflows.models import HeadFence, WorkflowCode, WorkflowRun, WorkflowStatus
from app.workflows.simple_proposal import PropertyCostOverride, SimpleProposalBuilder


def persist_completed_first_proposal(
    connection: Connection,
    *,
    project_id: str,
    user_id: str,
    state: VentureState,
    policy_snapshot_id: str,
    seed_registry_id: str,
    builder: SimpleProposalBuilder,
    now: datetime,
    new_id: Callable[[], str],
    source_workflow_run_id: str | None = None,
    source_result_bundle_id: str | None = None,
    property_cost_override: PropertyCostOverride | None = None,
) -> WorkflowRun:
    """Run and persist the complete first proposal in one transaction."""

    project = _lock_project(connection, project_id=project_id, user_id=user_id)
    if state.project_id != project_id or state.user_id != user_id:
        raise WorkflowPreconditionError("Workflow State ownership does not match the project")
    if int(project["current_state_version"]) != state.state_version:
        raise WorkflowPreconditionError("Workflow State is no longer current")

    effective_property_override = property_cost_override
    if effective_property_override is None:
        effective_property_override = _load_current_property_override(
            connection,
            project_id=project_id,
            user_id=user_id,
            state=state,
        )

    head = _next_head(
        project,
        project_id=project_id,
        state=state,
        policy_snapshot_id=policy_snapshot_id,
        seed_registry_id=seed_registry_id,
    )
    evidence_records = _active_evidence(connection, project_id=project_id)
    bundle = builder.build(
        state=state,
        evidence_records=evidence_records,
        property_cost_override=effective_property_override,
    )
    workflow_run_id = new_id()
    result_bundle_id = new_id()
    stage_run_id = new_id()
    input_digest = _workflow_input_digest(
        state=state,
        head=head,
        source_workflow_run_id=source_workflow_run_id,
    )
    stage_digest = stage_input_digest(
        workflow_run_id=workflow_run_id,
        stage_code=FirstProposalStage.RUN_PROPOSAL,
        head=head,
    )
    bundle_json = bundle.model_dump(mode="json")

    _persist_project_head(connection, project_id=project_id, head=head, now=now)
    _insert_workflow_run(
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
    _insert_stage_run(
        connection,
        stage_run_id=stage_run_id,
        workflow_run_id=workflow_run_id,
        input_digest=stage_digest,
        candidate_count=len(bundle.candidates),
        result_bundle_id=result_bundle_id,
        now=now,
    )
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
    _set_current_result(
        connection,
        project_id=project_id,
        result_bundle_id=result_bundle_id,
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
    return WorkflowRun(
        workflow_run_id=workflow_run_id,
        project_id=project_id,
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        status=WorkflowStatus.SUCCEEDED,
        head=head,
        input_digest=input_digest,
        created_at=now,
        updated_at=now,
    )


def _next_head(
    project: RowMapping,
    *,
    project_id: str,
    state: VentureState,
    policy_snapshot_id: str,
    seed_registry_id: str,
) -> HeadFence:
    return HeadFence(
        workflow_generation=int(project["workflow_generation"]) + 1,
        state_version=state.state_version,
        founder_snapshot_id=f"{project_id}:state:{state.state_version}:founder",
        area_snapshot_id=f"{project_id}:state:{state.state_version}:area",
        evidence_snapshot_id=(project["evidence_snapshot_id"] or state.evidence_snapshot_id),
        policy_snapshot_id=policy_snapshot_id,
        index_generation_id=project["index_generation_id"],
        seed_registry_id=seed_registry_id,
    )


def _workflow_input_digest(
    *,
    state: VentureState,
    head: HeadFence,
    source_workflow_run_id: str | None,
) -> str:
    return hashlib.sha256(
        rfc8785.dumps(
            {
                "workflow_code": WorkflowCode.FIRST_PROPOSAL.value,
                "state": state.model_dump(mode="json"),
                "head": head.model_dump(mode="json"),
                "source_workflow_run_id": source_workflow_run_id,
                "contract_version": "2.0.0",
            }
        )
    ).hexdigest()


def _persist_project_head(
    connection: Connection,
    *,
    project_id: str,
    head: HeadFence,
    now: datetime,
) -> None:
    connection.execute(
        text(
            "UPDATE venture_projects SET workflow_generation=:generation "
            "WHERE project_id=:project_id"
        ),
        {"generation": head.workflow_generation, "project_id": project_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO project_heads(
                project_id, workflow_generation, state_version,
                founder_snapshot_id, area_snapshot_id, evidence_snapshot_id,
                policy_snapshot_id, index_generation_id, seed_registry_id, updated_at
            ) VALUES (
                :project_id, :workflow_generation, :state_version,
                :founder_snapshot_id, :area_snapshot_id, :evidence_snapshot_id,
                :policy_snapshot_id, :index_generation_id, :seed_registry_id, :updated_at
            )
            ON CONFLICT (project_id) DO UPDATE SET
                workflow_generation=EXCLUDED.workflow_generation,
                state_version=EXCLUDED.state_version,
                founder_snapshot_id=EXCLUDED.founder_snapshot_id,
                area_snapshot_id=EXCLUDED.area_snapshot_id,
                evidence_snapshot_id=EXCLUDED.evidence_snapshot_id,
                policy_snapshot_id=EXCLUDED.policy_snapshot_id,
                index_generation_id=EXCLUDED.index_generation_id,
                seed_registry_id=EXCLUDED.seed_registry_id,
                updated_at=EXCLUDED.updated_at
            """
        ),
        {"project_id": project_id, **head.model_dump(mode="python"), "updated_at": now},
    )


def _insert_workflow_run(
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
                :workflow_run_id, :project_id, :owner_user_id, 'FIRST_PROPOSAL', 'SUCCEEDED',
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


def _insert_stage_run(
    connection: Connection,
    *,
    stage_run_id: str,
    workflow_run_id: str,
    input_digest: str,
    candidate_count: int,
    result_bundle_id: str,
    now: datetime,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO stage_runs(
                stage_run_id, workflow_run_id, stage_code, status,
                input_digest, attempt, result_json,
                created_at, updated_at, completed_at
            ) VALUES (
                :stage_run_id, :workflow_run_id, 'RUN_PROPOSAL', 'SUCCEEDED',
                :input_digest, 1, CAST(:result_json AS JSONB),
                :created_at, :updated_at, :completed_at
            )
            """
        ),
        {
            "stage_run_id": stage_run_id,
            "workflow_run_id": workflow_run_id,
            "input_digest": input_digest,
            "result_json": json.dumps(
                {
                    "outcome": "SUCCESS",
                    "candidate_count": candidate_count,
                    "result_bundle_id": result_bundle_id,
                },
                separators=(",", ":"),
            ),
            "created_at": now,
            "updated_at": now,
            "completed_at": now,
        },
    )


def _insert_result_bundle(
    connection: Connection,
    *,
    result_bundle_id: str,
    project_id: str,
    workflow_run_id: str,
    head: HeadFence,
    bundle_json: dict[str, Any],
    now: datetime,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO result_bundles(
                result_bundle_id, project_id, workflow_run_id,
                workflow_generation, state_version, founder_snapshot_id,
                area_snapshot_id, evidence_snapshot_id, policy_snapshot_id,
                index_generation_id, seed_registry_id, bundle_json, created_at
            ) VALUES (
                :result_bundle_id, :project_id, :workflow_run_id,
                :workflow_generation, :state_version, :founder_snapshot_id,
                :area_snapshot_id, :evidence_snapshot_id, :policy_snapshot_id,
                :index_generation_id, :seed_registry_id,
                CAST(:bundle_json AS JSONB), :created_at
            )
            """
        ),
        {
            "result_bundle_id": result_bundle_id,
            "project_id": project_id,
            "workflow_run_id": workflow_run_id,
            **head.model_dump(mode="python"),
            "bundle_json": json.dumps(bundle_json, separators=(",", ":")),
            "created_at": now,
        },
    )


def _insert_result_delta(
    connection: Connection,
    *,
    project_id: str,
    result_bundle_id: str,
    source_result_bundle_id: str | None,
    bundle_json: dict[str, Any],
    now: datetime,
) -> None:
    if source_result_bundle_id is None:
        return
    previous_bundle = connection.execute(
        text(
            "SELECT bundle_json FROM result_bundles "
            "WHERE result_bundle_id=:result_bundle_id AND project_id=:project_id"
        ),
        {"result_bundle_id": source_result_bundle_id, "project_id": project_id},
    ).scalar_one_or_none()
    if not isinstance(previous_bundle, dict):
        return
    delta = build_result_decision_delta(
        previous_result_bundle_id=source_result_bundle_id,
        current_result_bundle_id=result_bundle_id,
        previous_bundle=previous_bundle,
        current_bundle=bundle_json,
    )
    connection.execute(
        text(
            """
            INSERT INTO result_decision_deltas(
                result_bundle_id, project_id, previous_result_bundle_id,
                delta_json, created_at
            ) VALUES (
                :result_bundle_id, :project_id, :previous_result_bundle_id,
                CAST(:delta_json AS JSONB), :created_at
            )
            """
        ),
        {
            "result_bundle_id": result_bundle_id,
            "project_id": project_id,
            "previous_result_bundle_id": source_result_bundle_id,
            "delta_json": delta.model_dump_json(),
            "created_at": now,
        },
    )


def _set_current_result(
    connection: Connection,
    *,
    project_id: str,
    result_bundle_id: str,
) -> None:
    connection.execute(
        text(
            "UPDATE venture_projects SET current_result_bundle_id=:result_bundle_id "
            "WHERE project_id=:project_id"
        ),
        {"result_bundle_id": result_bundle_id, "project_id": project_id},
    )


def _lock_project(
    connection: Connection,
    *,
    project_id: str,
    user_id: str,
) -> RowMapping:
    row = (
        connection.execute(
            text(
                """
            SELECT p.current_state_version, p.workflow_generation,
                   h.evidence_snapshot_id, h.index_generation_id
            FROM venture_projects p
            LEFT JOIN project_heads h ON h.project_id=p.project_id
            WHERE p.project_id=:project_id AND p.owner_user_id=:user_id
            FOR UPDATE OF p
            """
            ),
            {"project_id": project_id, "user_id": user_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ProjectNotFoundError("Project does not exist")
    if not isinstance(row["current_state_version"], int):
        raise WorkflowPreconditionError("Onboarding State must exist before workflow start")
    return row


def _active_evidence(
    connection: Connection,
    *,
    project_id: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        text(
            """
            SELECT record.record_json
            FROM evidence_records record
            LEFT JOIN evidence_lifecycle lifecycle
              ON lifecycle.project_id=record.project_id
             AND lifecycle.evidence_id=record.evidence_id
            WHERE record.project_id=:project_id
              AND (lifecycle.status IS NULL OR lifecycle.status='ACTIVE')
            ORDER BY record.evidence_id
            """
        ),
        {"project_id": project_id},
    ).scalars()
    return [dict(value) for value in rows if isinstance(value, dict)]


def _load_current_property_override(
    connection: Connection,
    *,
    project_id: str,
    user_id: str,
    state: VentureState,
) -> PropertyCostOverride | None:
    """모든 재실행에서 선택 후보의 최신 실제 점포 비용을 자동으로 이어받는다."""

    if state.active_case_id is None:
        return None
    row = (
        connection.execute(
            text(
                """
                SELECT property_input_id, source_id, deposit_krw,
                       monthly_rent_krw, management_fee_krw, key_money_krw
                FROM candidate_property_intakes
                WHERE project_id=:project_id
                  AND owner_user_id=:user_id
                  AND candidate_id=:candidate_id
                  AND applied_state_version <= :state_version
                ORDER BY applied_state_version DESC, created_at DESC,
                         property_input_id DESC
                LIMIT 1
                """
            ),
            {
                "project_id": project_id,
                "user_id": user_id,
                "candidate_id": state.active_case_id,
                "state_version": state.state_version,
            },
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    return PropertyCostOverride(
        property_input_id=str(row["property_input_id"]),
        source_id=str(row["source_id"]),
        deposit_krw=int(row["deposit_krw"]),
        monthly_rent_krw=int(row["monthly_rent_krw"]),
        management_fee_krw=int(row["management_fee_krw"]),
        key_money_krw=(int(row["key_money_krw"]) if row["key_money_krw"] is not None else None),
    )


def _insert_event(
    connection: Connection,
    *,
    workflow_run_id: str,
    event_type: str,
    data: dict[str, object],
    occurred_at: datetime,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO workflow_events(
                workflow_run_id, event_type, event_json, occurred_at
            ) VALUES (
                :workflow_run_id, :event_type, CAST(:event_json AS JSONB), :occurred_at
            )
            """
        ),
        {
            "workflow_run_id": workflow_run_id,
            "event_type": event_type,
            "event_json": json.dumps(data, separators=(",", ":")),
            "occurred_at": occurred_at,
        },
    )
