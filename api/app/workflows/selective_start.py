import hashlib
import json
from collections.abc import Callable
from datetime import datetime

import rfc8785
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.domain.errors import FeedbackPreconditionError
from app.domain.models import CafeTypePreference, VentureState
from app.workflows.first_proposal import (
    FirstProposalStage,
    compile_first_proposal_plan,
    stage_input_digest,
)
from app.workflows.models import HeadFence, WorkflowCode, WorkflowRun, WorkflowStatus


def start_selective_first_proposal(
    connection: Connection,
    *,
    project_id: str,
    user_id: str,
    state: VentureState,
    source_workflow_run_id: str,
    affected_stage_codes: list[str],
    previous_head: HeadFence,
    now: datetime,
    new_id: Callable[[], str],
) -> WorkflowRun:
    """Create a rerun and reuse only successful, unaffected source stage results."""
    project = connection.execute(
        text(
            """
            SELECT workflow_generation
            FROM venture_projects
            WHERE project_id=:project_id AND owner_user_id=:user_id
            FOR UPDATE
            """
        ),
        {"project_id": project_id, "user_id": user_id},
    ).mappings().one_or_none()
    if project is None:
        raise FeedbackPreconditionError("Feedback project no longer exists")
    source_rows = connection.execute(
        text(
            """
            SELECT stage_code, status, input_digest, result_json
            FROM stage_runs
            WHERE workflow_run_id=:workflow_run_id
            """
        ),
        {"workflow_run_id": source_workflow_run_id},
    ).mappings().all()
    source = {row["stage_code"]: row for row in source_rows}
    generation = int(project["workflow_generation"]) + 1
    founder_snapshot_id = f"{project_id}:state:{state.state_version}:founder"
    area_snapshot_id = f"{project_id}:state:{state.state_version}:area"
    head = HeadFence(
        workflow_generation=generation,
        state_version=state.state_version,
        founder_snapshot_id=founder_snapshot_id,
        area_snapshot_id=area_snapshot_id,
        evidence_snapshot_id=previous_head.evidence_snapshot_id,
        policy_snapshot_id=previous_head.policy_snapshot_id,
        index_generation_id=previous_head.index_generation_id,
        seed_registry_id=previous_head.seed_registry_id,
    )
    connection.execute(
        text(
            """
            UPDATE venture_projects
            SET workflow_generation=:generation
            WHERE project_id=:project_id
            """
        ),
        {"generation": generation, "project_id": project_id},
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
    plan = compile_first_proposal_plan(
        CafeTypePreference(state.founder.cafe_type_preference)
    )
    requested = set(affected_stage_codes)
    affected: set[FirstProposalStage] = set()
    for stage in plan:
        old = source.get(stage.code.value)
        if (
            stage.code.value in requested
            or old is None
            or old["status"] != "SUCCEEDED"
            or not isinstance(old["result_json"], dict)
            or any(dependency in affected for dependency in stage.dependencies)
        ):
            affected.add(stage.code)
    if not affected:
        raise FeedbackPreconditionError("Feedback did not affect any executable stage")

    workflow_run_id = new_id()
    input_digest = hashlib.sha256(
        rfc8785.dumps(
            {
                "command": {"project_id": project_id, "workflow_code": "FIRST_PROPOSAL"},
                "head": head.model_dump(mode="json"),
                "source_workflow_run_id": source_workflow_run_id,
                "affected_stage_codes": sorted(stage.value for stage in affected),
                "contract_version": "1.0.0",
            }
        )
    ).hexdigest()
    connection.execute(
        text(
            """
            INSERT INTO workflow_runs(
                workflow_run_id, project_id, owner_user_id, workflow_code, status,
                workflow_generation, state_version, founder_snapshot_id,
                area_snapshot_id, evidence_snapshot_id, policy_snapshot_id,
                index_generation_id, seed_registry_id, input_digest, created_at, updated_at
            ) VALUES (
                :workflow_run_id, :project_id, :owner_user_id, 'FIRST_PROPOSAL', 'QUEUED',
                :workflow_generation, :state_version, :founder_snapshot_id,
                :area_snapshot_id, :evidence_snapshot_id, :policy_snapshot_id,
                :index_generation_id, :seed_registry_id, :input_digest, :created_at, :updated_at
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
        },
    )
    stage_ids = {stage.code: new_id() for stage in plan}
    copied_results: dict[FirstProposalStage, dict[str, object]] = {}
    for stage in plan:
        if stage.code not in affected:
            copied_results[stage.code] = source[stage.code.value]["result_json"]

    ready: list[tuple[str, str]] = []
    for stage in plan:
        if stage.code not in affected:
            status = "SUCCEEDED"
            digest = source[stage.code.value]["input_digest"]
            result_json = copied_results[stage.code]
            completed_at = now
        else:
            dependency_values = tuple(
                {
                    "stage_code": dependency.value,
                    "input_digest": source[dependency.value]["input_digest"],
                    "result": copied_results[dependency],
                }
                for dependency in stage.dependencies
                if dependency not in affected
            )
            can_start = all(dependency not in affected for dependency in stage.dependencies)
            status = "READY" if can_start else "PENDING"
            digest = stage_input_digest(
                workflow_run_id=workflow_run_id,
                stage_code=stage.code,
                head=head,
                dependencies=dependency_values if can_start else (),
            )
            result_json = None
            completed_at = None
            if can_start:
                ready.append((stage_ids[stage.code], digest))
        connection.execute(
            text(
                """
                INSERT INTO stage_runs(
                    stage_run_id, workflow_run_id, stage_code, status, input_digest,
                    result_json, created_at, updated_at, completed_at
                ) VALUES (
                    :stage_run_id, :workflow_run_id, :stage_code, :status, :input_digest,
                    CAST(:result_json AS JSONB), :created_at, :updated_at, :completed_at
                )
                """
            ),
            {
                "stage_run_id": stage_ids[stage.code],
                "workflow_run_id": workflow_run_id,
                "stage_code": stage.code.value,
                "status": status,
                "input_digest": digest,
                "result_json": (
                    json.dumps(result_json, separators=(",", ":"))
                    if result_json is not None
                    else None
                ),
                "created_at": now,
                "updated_at": now,
                "completed_at": completed_at,
            },
        )
        for dependency in stage.dependencies:
            connection.execute(
                text(
                    """
                    INSERT INTO stage_dependencies(stage_run_id, depends_on_stage_run_id)
                    VALUES (:stage_run_id, :depends_on_stage_run_id)
                    """
                ),
                {
                    "stage_run_id": stage_ids[stage.code],
                    "depends_on_stage_run_id": stage_ids[dependency],
                },
            )
    connection.execute(
        text(
            """
            INSERT INTO workflow_events(workflow_run_id, event_type, event_json, occurred_at)
            VALUES (:workflow_run_id, 'WORKFLOW_QUEUED', CAST(:event_json AS JSONB), :occurred_at)
            """
        ),
        {
            "workflow_run_id": workflow_run_id,
            "event_json": json.dumps(
                {
                    "stage_count": len(plan),
                    "affected_stage_codes": sorted(stage.value for stage in affected),
                    "reused_stage_codes": sorted(
                        stage.code.value for stage in plan if stage.code not in affected
                    ),
                    "source_workflow_run_id": source_workflow_run_id,
                    "head": head.model_dump(mode="json"),
                },
                separators=(",", ":"),
            ),
            "occurred_at": now,
        },
    )
    for stage_run_id, digest in ready:
        payload = {
            "workflow_run_id": workflow_run_id,
            "stage_run_id": stage_run_id,
            "input_digest": digest,
        }
        payload_bytes = rfc8785.dumps(payload)
        connection.execute(
            text(
                """
                INSERT INTO workflow_outbox(
                    topic, aggregate_id, payload_json, payload_digest, available_at, created_at
                ) VALUES (
                    'WORKFLOW_STAGE_READY', :aggregate_id, CAST(:payload_json AS JSONB),
                    :payload_digest, :available_at, :created_at
                )
                """
            ),
            {
                "aggregate_id": stage_run_id,
                "payload_json": payload_bytes.decode(),
                "payload_digest": hashlib.sha256(payload_bytes).hexdigest(),
                "available_at": now,
                "created_at": now,
            },
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
