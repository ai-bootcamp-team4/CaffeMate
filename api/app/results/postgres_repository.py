from sqlalchemy import Engine, text
from sqlalchemy.engine import RowMapping

from app.domain.errors import ResultNotFoundError
from app.results.models import (
    ResultBundlePayload,
    ResultDecisionDelta,
    ResultFreshness,
    ResultView,
)
from app.workflows.models import HeadFence


class PostgresResultRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_current(self, *, project_id: str, user_id: str) -> ResultView:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT r.result_bundle_id, r.project_id, r.workflow_run_id,
                           r.workflow_generation, r.state_version,
                           r.founder_snapshot_id, r.area_snapshot_id,
                           r.evidence_snapshot_id, r.policy_snapshot_id,
                           r.index_generation_id, r.seed_registry_id,
                           r.bundle_json, r.created_at,
                           delta.delta_json,
                           invalidation.reason_codes AS invalidation_reason_codes,
                           h.workflow_generation AS current_workflow_generation,
                           h.state_version AS current_state_version,
                           h.founder_snapshot_id AS current_founder_snapshot_id,
                           h.area_snapshot_id AS current_area_snapshot_id,
                           h.evidence_snapshot_id AS current_evidence_snapshot_id,
                           h.policy_snapshot_id AS current_policy_snapshot_id,
                           h.index_generation_id AS current_index_generation_id,
                           h.seed_registry_id AS current_seed_registry_id
                    FROM venture_projects p
                    JOIN result_bundles r
                      ON r.project_id = p.project_id
                     AND r.result_bundle_id = p.current_result_bundle_id
                    JOIN project_heads h ON h.project_id = p.project_id
                    LEFT JOIN result_decision_deltas delta
                      ON delta.result_bundle_id=r.result_bundle_id
                    LEFT JOIN result_invalidations invalidation
                      ON invalidation.result_bundle_id=r.result_bundle_id
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
    def _from_row(row: RowMapping) -> ResultView:
        payload = ResultBundlePayload.model_validate(row["bundle_json"])
        result_head = PostgresResultRepository._head(row, prefix="")
        current_head = PostgresResultRepository._head(row, prefix="current_")
        dimensions = (
            "workflow_generation",
            "state_version",
            "founder_snapshot_id",
            "area_snapshot_id",
            "evidence_snapshot_id",
            "policy_snapshot_id",
            "index_generation_id",
            "seed_registry_id",
        )
        stale_dimensions = [
            dimension
            for dimension in dimensions
            if getattr(result_head, dimension) != getattr(current_head, dimension)
        ]
        invalidation_reasons = row["invalidation_reason_codes"] or []
        return ResultView(
            result_bundle_id=row["result_bundle_id"],
            project_id=row["project_id"],
            workflow_run_id=row["workflow_run_id"],
            head=result_head,
            candidates=payload.candidates,
            primary_candidate_id=payload.primary_candidate_id,
            audit_status=payload.audit_status,
            outcome_status=payload.outcome_status,
            created_at=row["created_at"],
            freshness=(
                ResultFreshness.STALE
                if stale_dimensions or invalidation_reasons
                else ResultFreshness.CURRENT
            ),
            stale_head_dimensions=stale_dimensions,
            current_head=current_head,
            decision_delta=(
                ResultDecisionDelta.model_validate(row["delta_json"])
                if row["delta_json"] is not None
                else None
            ),
            invalidation_reason_codes=list(invalidation_reasons),
        )

    @staticmethod
    def _head(row: RowMapping, *, prefix: str) -> HeadFence:
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
