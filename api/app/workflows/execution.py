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
from app.finance.case_fact_repository import load_current_case_fact_resolution
from app.finance.case_facts import CaseFactResolution, PropertyContext
from app.finance.labor_benchmark import (
    MinimumWageReference,
    replay_minimum_wage_references,
)
from app.finance.labor_oncost import (
    replay_employer_oncost_minimum_wage_references,
    replay_employer_social_insurance_references,
)
from app.finance.property_benchmark import replay_property_rent_benchmarks
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
        property_context: PropertyContext | None = None,
        case_fact_resolution: CaseFactResolution | None = None,
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
            case_fact_resolution = load_current_case_fact_resolution(
                connection,
                project_id=workflow["project_id"],
                state=state,
            )

        progress = PostgresProgressSink(self._engine, self._leases, lease, now=self._now)
        if workflow["source_workflow_run_id"] is None:
            bundle = self._pipeline.run(
                state=state,
                head=lease.head,
                workflow_run_id=lease.workflow_run_id,
                evidence_records=evidence_records,
                case_fact_resolution=case_fact_resolution,
                property_context=property_override,
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
            source_bundle = self._load_source_result_bundle(
                workflow["project_id"], workflow["source_result_bundle_id"]
            )
            source_property_context = self._property_context_from_source_bundle(
                source_bundle,
                active_case_id=state.active_case_id,
            )
            replayed_minimum_wage = _merge_minimum_wage_references(
                replay_minimum_wage_references(source_bundle),
                replay_employer_oncost_minimum_wage_references(source_bundle),
            )
            bundle = self._selective_builder.build(
                state=state,
                evidence_records=evidence_records,
                property_context=property_override or source_property_context,
                case_fact_resolution=case_fact_resolution,
                property_rent_benchmarks=replay_property_rent_benchmarks(source_bundle),
                minimum_wage_references=replayed_minimum_wage,
                employer_social_insurance_references=(
                    replay_employer_social_insurance_references(source_bundle)
                ),
                franchise_universe=_franchise_universe_from_bundle(source_bundle),
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

    def _load_source_result_bundle(
        self,
        project_id: object,
        source_result_bundle_id: object,
    ) -> dict[str, Any]:
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
        return bundle

    @staticmethod
    def _property_context_from_source_bundle(
        bundle: dict[str, Any],
        *,
        active_case_id: str | None,
    ) -> PropertyContext | None:
        """Recover the selected property's full context when DB lookup is unavailable.

        The queued workflow stores only immutable head/source-result references. The
        source result is therefore the durable fallback for the selected property's
        address and finance terms; a current candidate_property_intake remains the
        higher-precedence source when present.
        """

        candidates = bundle.get("candidates")
        if not isinstance(candidates, list) or not active_case_id:
            return None
        for candidate in candidates:
            if not isinstance(candidate, dict) or candidate.get("candidate_id") != active_case_id:
                continue
            projection = candidate.get("property_context")
            if not isinstance(projection, dict):
                return None
            source_id = _candidate_source_id(candidate)
            if source_id is None:
                return None
            try:
                return PropertyContext(
                    property_input_id=str(projection["property_input_id"]),
                    source_id=source_id,
                    address=str(projection["address"]),
                    area_sqm=float(projection["area_sqm"]),
                    floor=(
                        str(projection["floor"])
                        if projection.get("floor") is not None
                        else None
                    ),
                    deposit_krw=int(projection["deposit_krw"]),
                    monthly_rent_krw=int(projection["monthly_rent_krw"]),
                    management_fee_krw=int(projection["management_fee_krw"]),
                    key_money_krw=(
                        int(projection["key_money_krw"])
                        if projection.get("key_money_krw") is not None
                        else None
                    ),
                )
            except (KeyError, TypeError, ValueError):
                return None
        return None


def _candidate_source_id(candidate: dict[str, Any]) -> str | None:
    independent = candidate.get("independent_model")
    independent_model_id = independent.get("model_id") if isinstance(independent, dict) else None
    if isinstance(independent_model_id, str):
        return independent_model_id
    franchise = candidate.get("franchise")
    franchise_brand_id = franchise.get("brand_id") if isinstance(franchise, dict) else None
    if isinstance(franchise_brand_id, str):
        return franchise_brand_id
    return None


def _franchise_universe_from_bundle(bundle: dict[str, Any]) -> list[dict[str, Any]]:
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


def _merge_minimum_wage_references(
    *reference_sets: list[MinimumWageReference],
) -> list[MinimumWageReference]:
    merged: dict[tuple[str, str], MinimumWageReference] = {}
    for references in reference_sets:
        for reference in references:
            merged[(reference.effective_from, reference.effective_to)] = reference
    return sorted(merged.values(), key=lambda value: value.effective_from)
