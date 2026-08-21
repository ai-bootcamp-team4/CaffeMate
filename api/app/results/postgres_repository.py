from sqlalchemy import Engine, text
from sqlalchemy.engine import RowMapping

from app.domain.errors import ResultNotFoundError
from app.results.models import ResultBundle, ResultBundlePayload
from app.workflows.models import HeadFence


class PostgresResultRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_current(self, *, project_id: str, user_id: str) -> ResultBundle:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT r.result_bundle_id, r.project_id, r.workflow_run_id,
                           r.workflow_generation, r.state_version,
                           r.founder_snapshot_id, r.area_snapshot_id,
                           r.evidence_snapshot_id, r.policy_snapshot_id,
                           r.index_generation_id, r.seed_registry_id,
                           r.bundle_json, r.created_at
                    FROM venture_projects p
                    JOIN result_bundles r
                      ON r.project_id = p.project_id
                     AND r.result_bundle_id = p.current_result_bundle_id
                    WHERE p.project_id = :project_id
                      AND p.owner_user_id = :user_id
                    """
                ),
                {"project_id": project_id, "user_id": user_id},
            ).mappings().one_or_none()
        if row is None:
            raise ResultNotFoundError("Current result does not exist")
        return self._from_row(row)

    @staticmethod
    def _from_row(row: RowMapping) -> ResultBundle:
        payload = ResultBundlePayload.model_validate(row["bundle_json"])
        return ResultBundle(
            result_bundle_id=row["result_bundle_id"],
            project_id=row["project_id"],
            workflow_run_id=row["workflow_run_id"],
            head=HeadFence(
                workflow_generation=row["workflow_generation"],
                state_version=row["state_version"],
                founder_snapshot_id=row["founder_snapshot_id"],
                area_snapshot_id=row["area_snapshot_id"],
                evidence_snapshot_id=row["evidence_snapshot_id"],
                policy_snapshot_id=row["policy_snapshot_id"],
                index_generation_id=row["index_generation_id"],
                seed_registry_id=row["seed_registry_id"],
            ),
            candidates=payload.candidates,
            primary_candidate_id=payload.primary_candidate_id,
            audit_status=payload.audit_status,
            created_at=row["created_at"],
        )
