import json
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from app.candidates.seed_registry import IndependentSeedRegistry
from app.domain.errors import StageLeaseRejectedError, WorkflowPreconditionError
from app.domain.models import VentureState
from app.results.models import ResultBundlePayload
from app.workflows.async_persistence import persist_first_proposal_result
from app.workflows.lease import PostgresWorkflowLeaseRepository
from app.workflows.models import HeadFence, StageLease
from app.workflows.persistence import _active_evidence, _load_current_property_override
from app.workflows.progress import FirstProposalProgressStage, WorkflowProgressSink
from app.workflows.simple_proposal import SimpleProposalBuilder


class FirstProposalPipeline(Protocol):
    def run(
        self,
        *,
        state: VentureState,
        head: HeadFence,
        workflow_run_id: str,
        evidence_records: list[dict[str, Any]],
        property_cost_override: Any = None,
        progress: WorkflowProgressSink | None = None,
    ) -> ResultBundlePayload: ...


class PostgresProgressSink:
    def __init__(
        self,
        engine: Engine,
        lease_repository: PostgresWorkflowLeaseRepository,
        lease: StageLease,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._engine = engine
        self._leases = lease_repository
        self._lease = lease
        self._now = now or (lambda: datetime.now(UTC))

    def start(self, stage: FirstProposalProgressStage) -> None:
        self._transition(stage=stage, expected={"PENDING", "RUNNING"}, target="RUNNING")

    def complete(self, stage: FirstProposalProgressStage) -> None:
        self._transition(stage=stage, expected={"RUNNING", "SUCCEEDED"}, target="SUCCEEDED")

    def skip(self, stage: FirstProposalProgressStage) -> None:
        self._transition(stage=stage, expected={"PENDING", "SKIPPED"}, target="SKIPPED")

    def _transition(
        self,
        *,
        stage: FirstProposalProgressStage,
        expected: set[str],
        target: str,
    ) -> None:
        now = self._now()
        with self._engine.begin() as connection:
            if not self._leases.authorize_mutation(connection, lease=self._lease, now=now):
                raise StageLeaseRejectedError("Workflow lease is no longer authoritative")
            row = connection.execute(
                text(
                    """
                    SELECT status FROM stage_runs
                    WHERE workflow_run_id=:workflow_run_id AND stage_code=:stage_code
                    FOR UPDATE
                    """
                ),
                {
                    "workflow_run_id": self._lease.workflow_run_id,
                    "stage_code": stage.value,
                },
            ).scalar_one_or_none()
            if row is None or row not in expected:
                raise StageLeaseRejectedError("Workflow progress transition is stale")
            if row == target:
                return
            completed_assignment = (
                ", completed_at=:now" if target in {"SUCCEEDED", "SKIPPED"} else ""
            )
            attempt_assignment = ", attempt=:attempt" if target == "RUNNING" else ""
            connection.execute(
                text(
                    "UPDATE stage_runs SET status=:status, updated_at=:now"
                    + attempt_assignment
                    + completed_assignment
                    + " WHERE workflow_run_id=:workflow_run_id AND stage_code=:stage_code"
                ),
                {
                    "status": target,
                    "attempt": self._lease.attempt,
                    "now": now,
                    "workflow_run_id": self._lease.workflow_run_id,
                    "stage_code": stage.value,
                },
            )


class PostgresFirstProposalExecutor:
    def __init__(
        self,
        engine: Engine,
        pipeline: FirstProposalPipeline,
        leases: PostgresWorkflowLeaseRepository,
        *,
        selective_builder: SimpleProposalBuilder | None = None,
        now: Callable[[], datetime] | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._engine = engine
        self._pipeline = pipeline
        self._leases = leases
        self._selective_builder = selective_builder or SimpleProposalBuilder(
            IndependentSeedRegistry.load_default()
        )
        self._now = now or (lambda: datetime.now(UTC))
        self._new_id = new_id or (lambda: secrets.token_hex(16))

    def execute(self, lease: StageLease) -> dict[str, object]:
        if not self._leases.authorize(lease):
            raise StageLeaseRejectedError("Workflow lease is stale before execution")
        with self._engine.connect() as connection:
            workflow = self._load_workflow(connection, lease.workflow_run_id)
            state_json = connection.execute(
                text(
                    """
                    SELECT state_json FROM venture_states
                    WHERE project_id=:project_id AND state_version=:state_version
                    """
                ),
                {
                    "project_id": workflow["project_id"],
                    "state_version": workflow["state_version"],
                },
            ).scalar_one()
            state = VentureState.model_validate(state_json)
            evidence_records = _active_evidence(connection, project_id=workflow["project_id"])
            property_override = _load_current_property_override(
                connection,
                project_id=workflow["project_id"],
                user_id=workflow["owner_user_id"],
                state=state,
            )

        progress = PostgresProgressSink(self._engine, self._leases, lease, now=self._now)
        if workflow["source_workflow_run_id"] is None:
            bundle = self._pipeline.run(
                state=state,
                head=lease.head,
                workflow_run_id=lease.workflow_run_id,
                evidence_records=evidence_records,
                property_cost_override=property_override,
                progress=progress,
            )
        else:
            for stage in (
                FirstProposalProgressStage.EVIDENCE_RETRIEVAL,
                FirstProposalProgressStage.EVIDENCE_ASSESS,
                FirstProposalProgressStage.PROPOSAL_GENERATION,
            ):
                progress.skip(stage)
            progress.start(FirstProposalProgressStage.FINANCE_AND_RANK)
            bundle = self._selective_builder.build(
                state=state,
                evidence_records=evidence_records,
                property_cost_override=property_override,
                franchise_universe=self._load_source_franchise_universe(
                    workflow["project_id"],
                    workflow["source_result_bundle_id"]
                ),
            )
            progress.complete(FirstProposalProgressStage.FINANCE_AND_RANK)
            progress.skip(FirstProposalProgressStage.CANDIDATE_AUDIT)
        progress.start(FirstProposalProgressStage.COMMIT_RESULT)
        now = self._now()
        with self._engine.begin() as connection:
            if not self._leases.authorize_mutation(connection, lease=lease, now=now):
                raise StageLeaseRejectedError("Workflow lease is stale before result commit")
            return persist_first_proposal_result(
                connection,
                workflow_run_id=lease.workflow_run_id,
                project_id=workflow["project_id"],
                head=lease.head,
                bundle=bundle,
                source_result_bundle_id=workflow["source_result_bundle_id"],
                execution_stage_run_id=lease.stage_run_id,
                now=now,
                new_id=self._new_id,
            )

    @staticmethod
    def _load_workflow(connection: Connection, workflow_run_id: str) -> RowMapping:
        return (
            connection.execute(
                text(
                    """
                    SELECT workflow_run_id, project_id, owner_user_id, state_version,
                           source_workflow_run_id, source_result_bundle_id
                    FROM workflow_runs WHERE workflow_run_id=:workflow_run_id
                    """
                ),
                {"workflow_run_id": workflow_run_id},
            )
            .mappings()
            .one()
        )

    def _load_source_franchise_universe(
        self,
        project_id: object,
        source_result_bundle_id: object,
    ) -> list[dict[str, Any]]:
        if (
            not isinstance(project_id, str)
            or not project_id
            or not isinstance(source_result_bundle_id, str)
            or not source_result_bundle_id
        ):
            raise WorkflowPreconditionError("Selective recompute source result is unavailable")
        with self._engine.connect() as connection:
            bundle = connection.execute(
                text(
                    "SELECT bundle_json FROM result_bundles "
                    "WHERE project_id=:project_id AND result_bundle_id=:result_bundle_id"
                ),
                {
                    "project_id": project_id,
                    "result_bundle_id": source_result_bundle_id,
                },
            ).scalar_one_or_none()
        if isinstance(bundle, str):
            bundle = json.loads(bundle)
        if not isinstance(bundle, dict):
            raise WorkflowPreconditionError("Selective recompute source result is invalid")
        candidates = bundle.get("candidates")
        if not isinstance(candidates, list):
            return []
        universe: list[dict[str, Any]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            franchise = candidate.get("franchise")
            if not isinstance(franchise, dict) or franchise.get("eligibility") != "VERIFIED":
                continue
            brand_id = franchise.get("brand_id")
            profile = franchise.get("finance_profile")
            if not isinstance(brand_id, str) or not isinstance(profile, dict):
                continue
            universe.append(
                {
                    "brand_id": brand_id,
                    "display_name": candidate.get("display_name", brand_id),
                    "individual_franchise_eligibility": "VERIFIED",
                    "evidence_refs": franchise.get("eligibility_evidence_refs", []),
                    "finance_profile": profile,
                }
            )
        return universe