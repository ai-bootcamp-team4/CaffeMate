import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

import rfc8785
from sqlalchemy import Engine, text

from app.domain.errors import PersistenceUnavailableError, WorkflowPreconditionError
from app.domain.models import VentureState
from app.evidence.models import EvidenceRefreshRequest, EvidenceRefreshResult
from app.workflows.models import HeadFence
from app.workflows.selective_start import start_selective_first_proposal


class EvidenceRefreshService:
    def __init__(
        self,
        engine: Engine,
        *,
        now: Callable[[], datetime] | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._engine = engine
        self._now = now or (lambda: datetime.now(UTC))
        self._new_id = new_id or (lambda: str(uuid4()))

    def refresh(self, request: EvidenceRefreshRequest) -> EvidenceRefreshResult:
        values = request.model_dump(mode="json")
        digest = f"sha256:{hashlib.sha256(rfc8785.dumps(values)).hexdigest()}"
        now = self._now()
        with self._engine.begin() as connection:
            replay = connection.execute(
                text(
                    "SELECT response_json FROM evidence_refreshes "
                    "WHERE project_id=:project_id AND request_digest=:digest"
                ),
                {"project_id": request.project_id, "digest": digest},
            ).scalar_one_or_none()
            if replay is not None:
                return EvidenceRefreshResult.model_validate(replay)
            row = connection.execute(
                text(
                    """
                    SELECT p.owner_user_id, p.current_result_bundle_id,
                           p.current_state_version, s.state_json,
                           h.workflow_generation, h.state_version,
                           h.founder_snapshot_id, h.area_snapshot_id,
                           h.evidence_snapshot_id, h.policy_snapshot_id,
                           h.index_generation_id, h.seed_registry_id,
                           result.workflow_run_id AS source_workflow_run_id
                    FROM venture_projects p
                    JOIN venture_states s
                      ON s.project_id=p.project_id
                     AND s.state_version=p.current_state_version
                    JOIN project_heads h ON h.project_id=p.project_id
                    LEFT JOIN result_bundles result
                      ON result.project_id=p.project_id
                     AND result.result_bundle_id=p.current_result_bundle_id
                    WHERE p.project_id=:project_id
                    FOR UPDATE OF p
                    """
                ),
                {"project_id": request.project_id},
            ).mappings().one_or_none()
            if row is None:
                raise WorkflowPreconditionError("Evidence refresh project has no State")
            snapshot_records = self._current_snapshot_records(
                connection,
                project_id=request.project_id,
                evidence_snapshot_id=row["evidence_snapshot_id"],
            )
            by_source: dict[str, list[dict[str, Any]]] = {}
            for record in snapshot_records:
                source = record.get("source")
                source_ref = source.get("source_ref") if isinstance(source, dict) else None
                if isinstance(source_ref, str):
                    by_source.setdefault(source_ref, []).append(record)
            changed_refs: set[str] = set()
            unavailable_refs: set[str] = set()
            for observation in request.observations:
                if observation.source_observed_at > now + timedelta(minutes=5):
                    raise WorkflowPreconditionError(
                        "Evidence source observation is in the future"
                    )
                previous = connection.execute(
                    text(
                        "SELECT source_revision, source_observed_at "
                        "FROM evidence_source_heads "
                        "WHERE project_id=:project_id AND source_ref=:source_ref"
                    ),
                    {"project_id": request.project_id, "source_ref": observation.source_ref},
                ).mappings().one_or_none()
                if (
                    previous is not None
                    and observation.source_observed_at < previous["source_observed_at"]
                ):
                    raise WorkflowPreconditionError(
                        "Evidence source observation is older than the current head"
                    )
                known_revisions = {
                    revision
                    for record in by_source.get(observation.source_ref, [])
                    if (revision := self._record_revision(record)) is not None
                }
                baseline = (previous["source_revision"] if previous is not None else None) or (
                    next(iter(known_revisions)) if len(known_revisions) == 1 else None
                )
                if baseline is not None and baseline != observation.source_revision:
                    changed_refs.add(observation.source_ref)
                if observation.availability.value == "MISSING":
                    unavailable_refs.add(observation.source_ref)
                    changed_refs.add(observation.source_ref)
                connection.execute(
                    text(
                        """
                        INSERT INTO evidence_source_heads(
                            project_id, source_ref, source_revision,
                            source_observed_at, updated_at
                        ) VALUES (
                            :project_id, :source_ref, :source_revision,
                            :source_observed_at, :updated_at
                        ) ON CONFLICT(project_id, source_ref) DO UPDATE SET
                            source_revision=EXCLUDED.source_revision,
                            source_observed_at=EXCLUDED.source_observed_at,
                            updated_at=EXCLUDED.updated_at
                        """
                    ),
                    {
                        "project_id": request.project_id,
                        "source_ref": observation.source_ref,
                        "source_revision": observation.source_revision,
                        "source_observed_at": observation.source_observed_at,
                        "updated_at": now,
                    },
                )
            expired_ids = {
                record["evidence_id"]
                for record in snapshot_records
                if request.check_expiry and self._is_expired(record, now)
            }
            affected_ids = {
                record["evidence_id"]
                for source_ref in changed_refs
                for record in by_source.get(source_ref, [])
            } | expired_ids
            reason_codes: set[str] = set()
            if changed_refs:
                reason_codes.add("SOURCE_REVISION_CHANGED")
            if unavailable_refs:
                reason_codes.add("SOURCE_NOW_MISSING")
            if expired_ids:
                reason_codes.add("EVIDENCE_FRESHNESS_EXPIRED")
            refresh_id = self._new_id()
            workflow_id: str | None = None
            invalidated_result_id: str | None = None
            if affected_ids:
                if row["source_workflow_run_id"] is None or row["current_result_bundle_id"] is None:
                    raise WorkflowPreconditionError(
                        "Evidence refresh requires a committed current result"
                    )
                active_recompute = connection.execute(
                    text(
                        """
                        SELECT workflow_run_id FROM workflow_runs
                        WHERE project_id=:project_id AND workflow_code='FIRST_PROPOSAL'
                          AND status IN ('QUEUED', 'RUNNING', 'WAITING_FOR_HUMAN')
                          AND workflow_run_id<>:source_workflow_run_id
                        ORDER BY created_at DESC LIMIT 1
                        """
                    ),
                    {
                        "project_id": request.project_id,
                        "source_workflow_run_id": row["source_workflow_run_id"],
                    },
                ).scalar_one_or_none()
                if active_recompute is not None:
                    raise WorkflowPreconditionError(
                        "Evidence refresh recompute is already active"
                    )
                for evidence_id in sorted(affected_ids):
                    lifecycle_reason = (
                        "EVIDENCE_FRESHNESS_EXPIRED"
                        if evidence_id in expired_ids
                        else "SOURCE_NOW_MISSING"
                        if self._source_for(snapshot_records, evidence_id) in unavailable_refs
                        else "SOURCE_REVISION_CHANGED"
                    )
                    connection.execute(
                        text(
                            """
                            INSERT INTO evidence_lifecycle(
                                project_id, evidence_id, status, reason_code, updated_at
                            ) VALUES (
                                :project_id, :evidence_id, 'STALE', :reason_code, :updated_at
                            ) ON CONFLICT(project_id, evidence_id) DO UPDATE SET
                                status='STALE', reason_code=EXCLUDED.reason_code,
                                updated_at=EXCLUDED.updated_at
                            """
                        ),
                        {
                            "project_id": request.project_id,
                            "evidence_id": evidence_id,
                            "reason_code": lifecycle_reason,
                            "updated_at": now,
                        },
                    )
                invalidated_result_id = row["current_result_bundle_id"]
                connection.execute(
                    text(
                        """
                        INSERT INTO result_invalidations(
                            result_bundle_id, project_id, reason_codes, invalidated_at
                        ) VALUES (
                            :result_id, :project_id, CAST(:reasons AS JSONB), :invalidated_at
                        ) ON CONFLICT(result_bundle_id) DO UPDATE SET
                            reason_codes=EXCLUDED.reason_codes,
                            invalidated_at=EXCLUDED.invalidated_at
                        """
                    ),
                    {
                        "result_id": invalidated_result_id,
                        "project_id": request.project_id,
                        "reasons": json.dumps(sorted(reason_codes)),
                        "invalidated_at": now,
                    },
                )
                workflow = start_selective_first_proposal(
                    connection,
                    project_id=request.project_id,
                    user_id=row["owner_user_id"],
                    state=VentureState.model_validate(row["state_json"]),
                    source_workflow_run_id=row["source_workflow_run_id"],
                    affected_stage_codes=["EVIDENCE_RETRIEVAL"],
                    previous_head=self._head(row),
                    now=now,
                    new_id=self._new_id,
                )
                workflow_id = workflow.workflow_run_id
            result = EvidenceRefreshResult(
                refresh_id=refresh_id,
                project_id=request.project_id,
                status="RECOMPUTE_QUEUED" if affected_ids else "NO_CHANGE",
                changed_source_refs=sorted(changed_refs),
                expired_evidence_ids=sorted(expired_ids),
                affected_evidence_ids=sorted(affected_ids),
                invalidated_result_bundle_id=invalidated_result_id,
                recompute_workflow_run_id=workflow_id,
                requires_human_review=bool(unavailable_refs),
                reason_codes=sorted(reason_codes),
            )
            connection.execute(
                text(
                    """
                    INSERT INTO evidence_refreshes(
                        refresh_id, project_id, request_digest, source_result_bundle_id,
                        recompute_workflow_run_id, response_json, created_at
                    ) VALUES (
                        :refresh_id, :project_id, :digest, :source_result_id,
                        :workflow_id, CAST(:response AS JSONB), :created_at
                    )
                    """
                ),
                {
                    "refresh_id": refresh_id,
                    "project_id": request.project_id,
                    "digest": digest,
                    "source_result_id": row["current_result_bundle_id"],
                    "workflow_id": workflow_id,
                    "response": json.dumps(result.model_dump(mode="json")),
                    "created_at": now,
                },
            )
            return result

    @staticmethod
    def _current_snapshot_records(
        connection: Any, *, project_id: str, evidence_snapshot_id: str | None
    ) -> list[dict[str, Any]]:
        if evidence_snapshot_id is None:
            return []
        rows = connection.execute(
            text(
                """
                SELECT record.record_json
                FROM evidence_snapshot_records member
                JOIN evidence_records record
                  ON record.project_id=member.project_id
                 AND record.evidence_id=member.evidence_id
                WHERE member.project_id=:project_id
                  AND member.evidence_snapshot_id=:snapshot_id
                """
            ),
            {"project_id": project_id, "snapshot_id": evidence_snapshot_id},
        ).scalars().all()
        return [dict(value) for value in rows]

    @staticmethod
    def _record_revision(record: dict[str, Any]) -> str | None:
        source = record.get("source")
        if not isinstance(source, dict):
            return None
        for field in ("checksum", "document_version"):
            value = source.get(field)
            if isinstance(value, str) and value:
                return value
        observed = source.get("source_observed_at")
        return observed if isinstance(observed, str) and observed else None

    @staticmethod
    def _is_expired(record: dict[str, Any], now: datetime) -> bool:
        if record.get("freshness_status") == "NOT_APPLICABLE":
            return False
        if record.get("freshness_status") in {"STALE", "UNKNOWN"}:
            return True
        source = record.get("source")
        if not isinstance(source, dict):
            return True
        source_type = source.get("source_type")
        if not isinstance(source_type, str):
            return True
        ttl = {
            "API": timedelta(days=1),
            "DATASET": timedelta(days=30),
            "WEB": timedelta(days=90),
            "PDF": timedelta(days=90),
        }.get(source_type)
        if ttl is None:
            return False
        observed = source.get("source_observed_at")
        if isinstance(observed, str):
            try:
                return datetime.fromisoformat(observed.replace("Z", "+00:00")) + ttl < now
            except ValueError:
                return True
        published = source.get("published_or_data_date")
        if isinstance(published, str):
            try:
                value = date.fromisoformat(published)
            except ValueError:
                return True
            return datetime.combine(value, datetime.min.time(), tzinfo=UTC) + ttl < now
        return True

    @staticmethod
    def _source_for(records: list[dict[str, Any]], evidence_id: str) -> str | None:
        for record in records:
            if record.get("evidence_id") != evidence_id:
                continue
            source = record.get("source")
            return source.get("source_ref") if isinstance(source, dict) else None
        return None

    @staticmethod
    def _head(row: Any) -> HeadFence:
        return HeadFence(
            workflow_generation=row["workflow_generation"],
            state_version=row["state_version"],
            founder_snapshot_id=row["founder_snapshot_id"],
            area_snapshot_id=row["area_snapshot_id"],
            evidence_snapshot_id=row["evidence_snapshot_id"],
            policy_snapshot_id=row["policy_snapshot_id"],
            index_generation_id=row["index_generation_id"],
            seed_registry_id=row["seed_registry_id"],
        )


class UnavailableEvidenceRefreshService:
    def refresh(self, request: EvidenceRefreshRequest) -> EvidenceRefreshResult:
        del request
        raise PersistenceUnavailableError("Evidence refresh is unavailable")
