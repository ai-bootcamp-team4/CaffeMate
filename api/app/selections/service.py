import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import rfc8785
from sqlalchemy import Engine, text
from sqlalchemy.engine import RowMapping

from app.contracts.schema_registry import ContractRegistry, VentureStateValidator
from app.domain.errors import (
    CandidateSelectionPreconditionError,
    IdempotencyKeyReusedError,
    PersistenceUnavailableError,
    ProjectNotFoundError,
)
from app.domain.events import CandidateSelected
from app.domain.models import VentureState
from app.domain.reducer import reduce_venture_state
from app.results.models import ResultBundlePayload
from app.selections.checklist import build_evidence_checklist
from app.selections.models import CandidateSelection
from app.workflows.models import HeadFence


class CandidateSelectionService:
    def __init__(
        self,
        engine: Engine,
        *,
        now: Callable[[], datetime] | None = None,
        new_id: Callable[[], str] | None = None,
        contracts: VentureStateValidator | None = None,
    ) -> None:
        self._engine = engine
        self._now = now or (lambda: datetime.now(UTC))
        self._new_id = new_id or (lambda: str(uuid4()))
        self._contracts = contracts or ContractRegistry()

    def select(
        self,
        *,
        project_id: str,
        user_id: str,
        result_bundle_id: str,
        candidate_id: str,
        expected_head: HeadFence,
        idempotency_key: str,
    ) -> CandidateSelection:
        request = {
            "project_id": project_id,
            "result_bundle_id": result_bundle_id,
            "candidate_id": candidate_id,
            "expected_head": expected_head.model_dump(mode="json"),
        }
        request_digest = hashlib.sha256(rfc8785.dumps(cast(Any, request))).digest()
        with self._engine.begin() as connection:
            project = connection.execute(
                text(
                    """
                    SELECT p.current_state_version, p.current_result_bundle_id,
                           s.state_json, h.workflow_generation, h.state_version,
                           h.founder_snapshot_id, h.area_snapshot_id,
                           h.evidence_snapshot_id, h.policy_snapshot_id,
                           h.index_generation_id, h.seed_registry_id,
                           r.bundle_json
                    FROM venture_projects p
                    JOIN venture_states s
                      ON s.project_id=p.project_id
                     AND s.state_version=p.current_state_version
                    JOIN project_heads h ON h.project_id=p.project_id
                    JOIN result_bundles r
                      ON r.project_id=p.project_id
                     AND r.result_bundle_id=p.current_result_bundle_id
                    WHERE p.project_id=:project_id AND p.owner_user_id=:user_id
                    FOR UPDATE OF p
                    """
                ),
                {"project_id": project_id, "user_id": user_id},
            ).mappings().one_or_none()
            if project is None:
                raise ProjectNotFoundError("Current candidate result does not exist")
            replay = connection.execute(
                text(
                    """
                    SELECT * FROM candidate_selections
                    WHERE owner_user_id=:user_id AND project_id=:project_id
                      AND idempotency_key=:idempotency_key
                    """
                ),
                {
                    "user_id": user_id,
                    "project_id": project_id,
                    "idempotency_key": idempotency_key,
                },
            ).mappings().one_or_none()
            if replay is not None:
                if bytes(replay["request_digest"]) != request_digest:
                    raise IdempotencyKeyReusedError(
                        "Idempotency key was used with another candidate selection"
                    )
                return self._from_row(replay)
            current_head = self._head(project)
            if (
                project["current_result_bundle_id"] != result_bundle_id
                or current_head != expected_head
                or project["current_state_version"] != expected_head.state_version
            ):
                raise CandidateSelectionPreconditionError(
                    "Candidate selection requires the current result and full head"
                )
            payload = ResultBundlePayload.model_validate(project["bundle_json"])
            candidate = next(
                (
                    value
                    for value in payload.candidates
                    if value.get("candidate_id") == candidate_id
                ),
                None,
            )
            if candidate is None:
                raise CandidateSelectionPreconditionError(
                    "Candidate is not in the current result"
                )
            payload.validate_contracts(
                project_id=project_id,
                state_version=expected_head.state_version,
            )
            state_json = project["state_json"]
            if isinstance(state_json, str):
                state_json = json.loads(state_json)
            current_state = VentureState.model_validate(state_json)
            occurred_at = self._now()
            selection_id = self._new_id()
            event = CandidateSelected(
                event_id=self._new_id(),
                project_id=project_id,
                user_id=user_id,
                occurred_at=occurred_at,
                selection_id=selection_id,
                result_bundle_id=result_bundle_id,
                expected_state_version=current_state.state_version,
                candidate=candidate,
            )
            next_state = reduce_venture_state(current_state, event)
            assert next_state is not None
            state_value = next_state.model_dump(mode="json")
            self._contracts.validate_venture_state(state_value)
            checklist = build_evidence_checklist(candidate)
            checklist_value = [item.model_dump(mode="json") for item in checklist]
            connection.execute(
                text(
                    """
                    INSERT INTO venture_states(project_id, state_version, state_json, created_at)
                    VALUES (:project_id, :state_version, CAST(:state_json AS JSONB), :created_at)
                    """
                ),
                {
                    "project_id": project_id,
                    "state_version": next_state.state_version,
                    "state_json": json.dumps(state_value, separators=(",", ":")),
                    "created_at": occurred_at,
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE venture_projects SET current_state_version=:state_version
                    WHERE project_id=:project_id
                    """
                ),
                {"state_version": next_state.state_version, "project_id": project_id},
            )
            connection.execute(
                text(
                    """
                    UPDATE project_heads
                    SET state_version=:state_version,
                        founder_snapshot_id=:founder_snapshot_id,
                        area_snapshot_id=:area_snapshot_id,
                        updated_at=:updated_at
                    WHERE project_id=:project_id
                    """
                ),
                {
                    "state_version": next_state.state_version,
                    "founder_snapshot_id": (
                        f"{project_id}:state:{next_state.state_version}:founder"
                    ),
                    "area_snapshot_id": f"{project_id}:state:{next_state.state_version}:area",
                    "updated_at": occurred_at,
                    "project_id": project_id,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO project_events(
                        event_id, project_id, event_type, event_json, occurred_at
                    ) VALUES (
                        :event_id, :project_id, :event_type,
                        CAST(:event_json AS JSONB), :occurred_at
                    )
                    """
                ),
                {
                    "event_id": event.event_id,
                    "project_id": project_id,
                    "event_type": event.event_type,
                    "event_json": event.model_dump_json(),
                    "occurred_at": occurred_at,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO candidate_selections(
                        selection_id, project_id, owner_user_id, result_bundle_id,
                        candidate_id, selected_state_version, event_id, request_digest,
                        idempotency_key, candidate_json, checklist_json, created_at
                    ) VALUES (
                        :selection_id, :project_id, :owner_user_id, :result_bundle_id,
                        :candidate_id, :selected_state_version, :event_id, :request_digest,
                        :idempotency_key, CAST(:candidate_json AS JSONB),
                        CAST(:checklist_json AS JSONB), :created_at
                    )
                    """
                ),
                {
                    "selection_id": selection_id,
                    "project_id": project_id,
                    "owner_user_id": user_id,
                    "result_bundle_id": result_bundle_id,
                    "candidate_id": candidate_id,
                    "selected_state_version": next_state.state_version,
                    "event_id": event.event_id,
                    "request_digest": request_digest,
                    "idempotency_key": idempotency_key,
                    "candidate_json": json.dumps(candidate, separators=(",", ":")),
                    "checklist_json": json.dumps(checklist_value, separators=(",", ":")),
                    "created_at": occurred_at,
                },
            )
            return CandidateSelection(
                selection_id=selection_id,
                project_id=project_id,
                result_bundle_id=result_bundle_id,
                candidate_id=candidate_id,
                selected_state_version=next_state.state_version,
                candidate=candidate,
                required_evidence=checklist,
                created_at=occurred_at,
            )

    @staticmethod
    def _head(row: RowMapping) -> HeadFence:
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

    @staticmethod
    def _from_row(row: RowMapping) -> CandidateSelection:
        candidate = row["candidate_json"]
        checklist = row["checklist_json"]
        return CandidateSelection(
            selection_id=row["selection_id"],
            project_id=row["project_id"],
            result_bundle_id=row["result_bundle_id"],
            candidate_id=row["candidate_id"],
            selected_state_version=row["selected_state_version"],
            candidate=candidate if isinstance(candidate, dict) else json.loads(candidate),
            required_evidence=checklist if isinstance(checklist, list) else json.loads(checklist),
            created_at=row["created_at"],
        )


class UnavailableCandidateSelectionService:
    def select(self, **_: Any) -> CandidateSelection:
        raise PersistenceUnavailableError("Candidate selection persistence is unavailable")
