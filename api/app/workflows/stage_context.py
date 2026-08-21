import json
from typing import Any

from sqlalchemy import Engine, text

from app.domain.errors import StageLeaseRejectedError
from app.domain.models import StrictModel, VentureState
from app.workflows.first_proposal import FirstProposalStage, stage_input_digest
from app.workflows.models import HeadFence, StageLease


class StageContext(StrictModel):
    lease: StageLease
    project_id: str
    state: VentureState
    dependency_results: dict[str, dict[str, Any]]
    document_claims: list[dict[str, Any]] = []


class PostgresStageContextRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def load(self, lease: StageLease) -> StageContext:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                    SELECT s.stage_code, s.status AS stage_status, s.input_digest,
                           w.workflow_run_id, w.project_id,
                           w.workflow_generation, w.state_version,
                           w.founder_snapshot_id, w.area_snapshot_id,
                           w.evidence_snapshot_id, w.policy_snapshot_id,
                           w.index_generation_id, w.seed_registry_id,
                           h.workflow_generation AS current_workflow_generation,
                           h.state_version AS current_state_version,
                           h.founder_snapshot_id AS current_founder_snapshot_id,
                           h.area_snapshot_id AS current_area_snapshot_id,
                           h.evidence_snapshot_id AS current_evidence_snapshot_id,
                           h.policy_snapshot_id AS current_policy_snapshot_id,
                           h.index_generation_id AS current_index_generation_id,
                           h.seed_registry_id AS current_seed_registry_id,
                           state.state_json
                    FROM stage_runs s
                    JOIN workflow_runs w ON w.workflow_run_id=s.workflow_run_id
                    JOIN project_heads h ON h.project_id=w.project_id
                    JOIN venture_states state
                      ON state.project_id=w.project_id
                     AND state.state_version=w.state_version
                    WHERE s.stage_run_id=:stage_run_id
                    """
                    ),
                    {"stage_run_id": lease.stage_run_id},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise StageLeaseRejectedError("Stage context does not exist")
            stored_head = self._head(row, prefix="")
            current_head = self._head(row, prefix="current_")
            if (
                row["stage_status"] != "RUNNING"
                or row["workflow_run_id"] != lease.workflow_run_id
                or row["stage_code"] != lease.stage_code
                or row["input_digest"] != lease.input_digest
                or stored_head != lease.head
                or current_head != lease.head
            ):
                raise StageLeaseRejectedError("Stage context is stale or mismatched")

            dependencies = (
                connection.execute(
                    text(
                        """
                    SELECT prerequisite.stage_code, prerequisite.input_digest,
                           prerequisite.status, prerequisite.result_json
                    FROM stage_dependencies dependency
                    JOIN stage_runs prerequisite
                      ON prerequisite.stage_run_id=dependency.depends_on_stage_run_id
                    WHERE dependency.stage_run_id=:stage_run_id
                    ORDER BY prerequisite.stage_code
                    """
                    ),
                    {"stage_run_id": lease.stage_run_id},
                )
                .mappings()
                .all()
            )
            if any(
                dependency["status"] != "SUCCEEDED"
                or not isinstance(dependency["result_json"], dict)
                for dependency in dependencies
            ):
                raise StageLeaseRejectedError("Stage dependencies are incomplete")
            digest_dependencies = tuple(
                {
                    "stage_code": dependency["stage_code"],
                    "input_digest": dependency["input_digest"],
                    "result": dependency["result_json"],
                }
                for dependency in dependencies
            )
            expected_digest = stage_input_digest(
                workflow_run_id=lease.workflow_run_id,
                stage_code=FirstProposalStage(lease.stage_code),
                head=lease.head,
                dependencies=digest_dependencies,
            )
            if expected_digest != lease.input_digest:
                raise StageLeaseRejectedError("Stage dependency digest does not match")

            state_json = row["state_json"]
            if isinstance(state_json, str):
                state_json = json.loads(state_json)
            document_claims = (
                connection.execute(
                    text(
                        """
                    SELECT DISTINCT ON (claim.claim_type)
                           claim.claim_id, claim.case_id, claim.case_type,
                           claim.source_id, claim.claim_type, claim.value_json,
                           claim.unit, claim.materiality, document.document_type,
                           EXISTS (
                               SELECT 1 FROM document_claim_conflicts conflict
                               WHERE conflict.project_id=claim.project_id
                                 AND conflict.case_id=claim.case_id
                                 AND conflict.claim_type=claim.claim_type
                                 AND conflict.status='OPEN'
                           ) AS has_open_conflict
                    FROM venture_claims claim
                    JOIN documents document ON document.document_id=claim.document_id
                    WHERE claim.project_id=:project_id AND claim.status='CONFIRMED'
                    ORDER BY claim.claim_type, claim.created_at DESC, claim.claim_id DESC
                    """
                    ),
                    {"project_id": row["project_id"]},
                )
                .mappings()
                .all()
            )
            return StageContext(
                lease=lease,
                project_id=row["project_id"],
                state=VentureState.model_validate(state_json),
                dependency_results={
                    dependency["stage_code"]: dependency["result_json"]
                    for dependency in dependencies
                },
                document_claims=[dict(claim) for claim in document_claims],
            )

    @staticmethod
    def _head(row: Any, *, prefix: str) -> HeadFence:
        return HeadFence(
            workflow_generation=row[f"{prefix}workflow_generation"],
            state_version=row[f"{prefix}state_version"],
            founder_snapshot_id=row[f"{prefix}founder_snapshot_id"],
            area_snapshot_id=row[f"{prefix}area_snapshot_id"],
            evidence_snapshot_id=row[f"{prefix}evidence_snapshot_id"],
            policy_snapshot_id=row[f"{prefix}policy_snapshot_id"],
            index_generation_id=row[f"{prefix}index_generation_id"],
            seed_registry_id=row[f"{prefix}seed_registry_id"],
        )
