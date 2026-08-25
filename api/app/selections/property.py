"""사용자가 입력한 실제 점포 조건은 저장 후 durable 재계산 Workflow를 시작한다."""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import rfc8785
from sqlalchemy import Engine, text

from app.contracts.schema_registry import ContractRegistry, VentureStateValidator
from app.domain.errors import (
    CandidateSelectionPreconditionError,
    IdempotencyKeyReusedError,
    PersistenceUnavailableError,
    ProjectNotFoundError,
)
from app.domain.events import PropertyTermsApplied
from app.domain.models import VentureState
from app.domain.reducer import reduce_venture_state
from app.selections.models import PropertyTermsApplication, PropertyTermsInput
from app.workflows.models import HeadFence
from app.workflows.selective_start import start_selective_first_proposal


class PropertyTermsService:
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

    def apply(
        self,
        *,
        project_id: str,
        selection_id: str,
        user_id: str,
        expected_state_version: int,
        terms: PropertyTermsInput,
        idempotency_key: str,
    ) -> PropertyTermsApplication:
        request_value = {
            "project_id": project_id,
            "selection_id": selection_id,
            "expected_state_version": expected_state_version,
            "terms": terms.model_dump(mode="json"),
        }
        request_digest = hashlib.sha256(rfc8785.dumps(cast(Any, request_value))).digest()
        with self._engine.begin() as connection:
            selected = (
                connection.execute(
                    text(
                        """
                    SELECT cs.candidate_id, cs.candidate_json, cs.result_bundle_id,
                           p.current_state_version, s.state_json,
                           h.workflow_generation, h.state_version,
                           h.founder_snapshot_id, h.area_snapshot_id,
                           h.evidence_snapshot_id, h.policy_snapshot_id,
                           h.index_generation_id, h.seed_registry_id,
                           result.workflow_run_id AS source_workflow_run_id
                    FROM candidate_selections cs
                    JOIN venture_projects p ON p.project_id=cs.project_id
                    JOIN venture_states s
                      ON s.project_id=p.project_id
                     AND s.state_version=p.current_state_version
                    JOIN project_heads h ON h.project_id=p.project_id
                    JOIN result_bundles result
                      ON result.result_bundle_id=cs.result_bundle_id
                     AND result.project_id=cs.project_id
                    WHERE cs.project_id=:project_id
                      AND cs.selection_id=:selection_id
                      AND cs.owner_user_id=:user_id
                      AND p.owner_user_id=:user_id
                    FOR UPDATE OF p
                    """
                    ),
                    {
                        "project_id": project_id,
                        "selection_id": selection_id,
                        "user_id": user_id,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if selected is None:
                raise ProjectNotFoundError("Candidate selection was not found")
            replay = (
                connection.execute(
                    text(
                        """
                    SELECT request_digest, response_json
                    FROM candidate_property_intakes
                    WHERE owner_user_id=:user_id AND project_id=:project_id
                      AND idempotency_key=:idempotency_key
                    """
                    ),
                    {
                        "user_id": user_id,
                        "project_id": project_id,
                        "idempotency_key": idempotency_key,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if replay is not None:
                if bytes(replay["request_digest"]) != request_digest:
                    raise IdempotencyKeyReusedError(
                        "Idempotency key was used with other property terms"
                    )
                response_value = replay["response_json"]
                if isinstance(response_value, str):
                    response_value = json.loads(response_value)
                return PropertyTermsApplication.model_validate(response_value)

            state_value = selected["state_json"]
            if isinstance(state_value, str):
                state_value = json.loads(state_value)
            state = VentureState.model_validate(state_value)
            active_case_id = state.active_case_id
            if (
                state.state_version != expected_state_version
                or int(selected["current_state_version"]) != expected_state_version
                or int(selected["state_version"]) != expected_state_version
                or active_case_id is None
                or active_case_id != selected["candidate_id"]
            ):
                raise CandidateSelectionPreconditionError(
                    "Property terms require the current selected candidate"
                )
            candidate = selected["candidate_json"]
            if isinstance(candidate, str):
                candidate = json.loads(candidate)
            if not isinstance(candidate, dict):
                raise CandidateSelectionPreconditionError(
                    "Selected candidate payload is unavailable"
                )
            case_type = candidate.get("case_type")
            source_id = self._source_id(candidate)
            if case_type not in {"INDEPENDENT", "FRANCHISE"} or source_id is None:
                raise CandidateSelectionPreconditionError(
                    "Selected candidate cannot accept property terms"
                )

            occurred_at = self._now()
            property_input_id = self._new_id()
            event_id = self._new_id()
            claim_types = [
                "PROPERTY_ADDRESS",
                "AREA",
                "LEASE_DEPOSIT",
                "MONTHLY_RENT",
                "MANAGEMENT_FEE",
            ]
            if terms.key_money_krw is not None:
                claim_types.append("KEY_MONEY")
            claim_ids = [f"property-input:{property_input_id}:{value}" for value in claim_types]
            event = PropertyTermsApplied(
                event_id=event_id,
                project_id=project_id,
                user_id=user_id,
                occurred_at=occurred_at,
                property_input_id=property_input_id,
                expected_state_version=expected_state_version,
                active_case_id=active_case_id,
                confirmed_claim_ids=claim_ids,
            )
            next_state = reduce_venture_state(state, event)
            if next_state is None:
                raise AssertionError("Property terms reducer returned no State")
            next_state_json = next_state.model_dump(mode="json")
            self._contracts.validate_venture_state(next_state_json)
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
                INSERT INTO venture_states(project_id, state_version, state_json, created_at)
                VALUES (:project_id, :state_version, CAST(:state_json AS JSONB), :created_at)
                """
                ),
                {
                    "project_id": project_id,
                    "state_version": next_state.state_version,
                    "state_json": json.dumps(next_state_json, separators=(",", ":")),
                    "created_at": occurred_at,
                },
            )
            connection.execute(
                text(
                    "UPDATE venture_projects SET current_state_version=:state_version "
                    "WHERE project_id=:project_id"
                ),
                {"state_version": next_state.state_version, "project_id": project_id},
            )
            previous_head = self._head(selected)
            workflow = start_selective_first_proposal(
                connection,
                project_id=project_id,
                user_id=user_id,
                state=next_state,
                source_workflow_run_id=selected["source_workflow_run_id"],
                previous_head=previous_head,
                now=occurred_at,
                new_id=self._new_id,
            )
            previous_financial_summary = candidate.get("financial_summary")
            if not isinstance(previous_financial_summary, dict):
                previous_financial_summary = {}
            response = PropertyTermsApplication(
                property_input_id=property_input_id,
                project_id=project_id,
                selection_id=selection_id,
                candidate_id=selected["candidate_id"],
                applied_state_version=next_state.state_version,
                terms=terms,
                previous_financial_summary=previous_financial_summary,
                recompute_workflow=workflow,
                created_at=occurred_at,
            )
            response_json = response.model_dump(mode="json")
            connection.execute(
                text(
                    """
                INSERT INTO candidate_property_intakes(
                    property_input_id, selection_id, project_id, owner_user_id,
                    candidate_id, case_type, source_id, address, area_sqm, floor,
                    deposit_krw, monthly_rent_krw, management_fee_krw, key_money_krw,
                    expected_state_version, applied_state_version, idempotency_key,
                    request_digest, event_id, recompute_workflow_run_id,
                    response_json, created_at
                ) VALUES (
                    :property_input_id, :selection_id, :project_id, :owner_user_id,
                    :candidate_id, :case_type, :source_id, :address, :area_sqm, :floor,
                    :deposit_krw, :monthly_rent_krw, :management_fee_krw, :key_money_krw,
                    :expected_state_version, :applied_state_version, :idempotency_key,
                    :request_digest, :event_id, :recompute_workflow_run_id,
                    CAST(:response_json AS JSONB), :created_at
                )
                """
                ),
                {
                    "property_input_id": property_input_id,
                    "selection_id": selection_id,
                    "project_id": project_id,
                    "owner_user_id": user_id,
                    "candidate_id": selected["candidate_id"],
                    "case_type": case_type,
                    "source_id": source_id,
                    **terms.model_dump(mode="python"),
                    "expected_state_version": expected_state_version,
                    "applied_state_version": next_state.state_version,
                    "idempotency_key": idempotency_key,
                    "request_digest": request_digest,
                    "event_id": event_id,
                    "recompute_workflow_run_id": workflow.workflow_run_id,
                    "response_json": json.dumps(response_json, separators=(",", ":")),
                    "created_at": occurred_at,
                },
            )
            return response

    @staticmethod
    def _source_id(candidate: dict[str, Any]) -> str | None:
        if candidate.get("case_type") == "INDEPENDENT":
            model = candidate.get("independent_model")
            value = model.get("model_id") if isinstance(model, dict) else None
        else:
            franchise = candidate.get("franchise")
            value = franchise.get("brand_id") if isinstance(franchise, dict) else None
        return value if isinstance(value, str) and value else None

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


class UnavailablePropertyTermsService:
    def apply(self, **_: Any) -> PropertyTermsApplication:
        raise PersistenceUnavailableError("Property terms persistence is unavailable")
