"""Workflow queue와 결과 commit이 공유하는 PostgreSQL persistence helpers."""

import hashlib
import json
from datetime import datetime
from typing import Any

import rfc8785
from sqlalchemy import text
from sqlalchemy.engine import Connection, RowMapping

from app.domain.errors import ProjectNotFoundError, WorkflowPreconditionError
from app.domain.models import VentureState
from app.results.delta import build_result_decision_delta
from app.workflows.models import HeadFence, WorkflowCode
from app.workflows.simple_proposal import PropertyCostOverride


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
