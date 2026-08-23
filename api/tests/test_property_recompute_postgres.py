from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, text
from testcontainers.community.postgres import PostgresContainer
from worker.outbox import OutboxPublisher, PostgresOutboxRepository
from worker.runtime import DurableWorker

from app.candidates.seed_registry import IndependentSeedRegistry
from app.domain.models import (
    BorrowingIntent,
    CafeTypePreference,
    FounderState,
    OperationMode,
)
from app.migrations import apply_migrations
from app.projects.postgres_repository import PostgresProjectRepository
from app.projects.service import ProjectService
from app.results.postgres_repository import PostgresResultRepository
from app.results.service import ResultService
from app.selections.models import PropertyTermsInput
from app.selections.property import PropertyTermsService
from app.selections.service import CandidateSelectionService
from app.workflows.area_resolution import AreaResolutionStageHandler
from app.workflows.calculate_gate_rank import CalculateGateRankStageHandler
from app.workflows.candidate_audit import CandidateAuditStageHandler
from app.workflows.candidate_inputs import IndependentSeedStageHandler
from app.workflows.claim_plan import ClaimPlanStageHandler
from app.workflows.commit_result import CommitResultStageHandler
from app.workflows.evidence_assess import EvidenceAssessStageHandler
from app.workflows.evidence_freeze import EvidenceFreezeStageHandler
from app.workflows.evidence_plan import EvidencePlanStageHandler
from app.workflows.evidence_retrieval import EvidenceRetrievalStageHandler
from app.workflows.execution_repository import PostgresStageExecutionRepository
from app.workflows.first_proposal import FirstProposalStage
from app.workflows.models import WorkflowCode, WorkflowStatus
from app.workflows.postgres_repository import PostgresWorkflowRepository
from app.workflows.proposal import ProposalStageHandler
from app.workflows.service import WorkflowService
from app.workflows.stage_context import PostgresStageContextRepository
from app.workflows.stage_router import FirstProposalStageHandler, FirstProposalStageRouter
from tests.test_postgres_repository import (
    FirstProposalAgentFixture,
    FirstProposalMcpFixture,
    ImmediateWorkerPublisher,
    RouterStageProcessor,
)


@pytest.fixture(scope="module")
def property_postgres_engine() -> Engine:
    with PostgresContainer(
        image="postgres:17-alpine",
        username="caffemate",
        password="integration-test-only",
        dbname="caffemate",
        driver="pg8000",
    ) as postgres:
        engine = create_engine(postgres.get_connection_url(), pool_pre_ping=True)
        apply_migrations(engine)
        yield engine
        engine.dispose()


@pytest.fixture
def property_repository(
    property_postgres_engine: Engine,
) -> PostgresProjectRepository:
    with property_postgres_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE result_bundles, workflow_outbox, workflow_idempotency_records, "
                "workflow_events, stage_runs, workflow_runs, idempotency_records, "
                "project_events, venture_states, venture_projects CASCADE"
            )
        )
    return PostgresProjectRepository(property_postgres_engine)


class RecordingFirstProposalAgentFixture(FirstProposalAgentFixture):
    def __init__(self) -> None:
        super().__init__()
        self.tasks: list[dict[str, Any]] = []

    def invoke(self, task: dict[str, Any]) -> dict[str, Any]:
        self.tasks.append(task)
        return super().invoke(task)


def _independent_project(repository: PostgresProjectRepository):
    projects = ProjectService(repository)
    draft = projects.create_project(user_id="user-1", idempotency_key="property-create")
    return projects.confirm_onboarding(
        project_id=draft.project_id,
        user_id="user-1",
        idempotency_key="property-onboarding",
        founder=FounderState(
            target_area_input="수원 아주대 부근",
            own_funds_krw=50_000_000,
            borrowing_intent=BorrowingIntent.UNDECIDED,
            cafe_type_preference=CafeTypePreference.INDEPENDENT_ONLY,
            operation_mode=OperationMode.DIRECT_FULL_TIME,
        ),
    )


def _pipeline(postgres_engine: Engine):
    seed_registry = IndependentSeedRegistry.load_default()
    runtime = RecordingFirstProposalAgentFixture()
    mcp = FirstProposalMcpFixture()
    handlers: dict[FirstProposalStage, FirstProposalStageHandler] = {
        FirstProposalStage.AREA_RESOLUTION: AreaResolutionStageHandler(mcp),
        FirstProposalStage.CLAIM_PLAN: ClaimPlanStageHandler(),
        FirstProposalStage.EVIDENCE_PLAN: EvidencePlanStageHandler(),
        FirstProposalStage.EVIDENCE_RETRIEVAL: EvidenceRetrievalStageHandler(mcp),
        FirstProposalStage.EVIDENCE_ASSESS: EvidenceAssessStageHandler(runtime),
        FirstProposalStage.EVIDENCE_FREEZE: EvidenceFreezeStageHandler(),
        FirstProposalStage.INDEPENDENT_SEED: IndependentSeedStageHandler(seed_registry),
        FirstProposalStage.PROPOSE_INDEPENDENT: ProposalStageHandler.independent(runtime),
        FirstProposalStage.CALCULATE_GATE_RANK: CalculateGateRankStageHandler(seed_registry),
        FirstProposalStage.CANDIDATE_AUDIT: CandidateAuditStageHandler(runtime),
        FirstProposalStage.COMMIT_RESULT: CommitResultStageHandler(),
    }
    workflows = WorkflowService(
        PostgresWorkflowRepository(
            postgres_engine,
            policy_snapshot_id="policy-v1",
            seed_registry_id=seed_registry.registry_id,
        )
    )
    processor = RouterStageProcessor(
        FirstProposalStageRouter(PostgresStageContextRepository(postgres_engine), handlers)
    )
    immediate = ImmediateWorkerPublisher(
        DurableWorker(
            PostgresStageExecutionRepository(postgres_engine),
            processor,
            worker_id="property-integration-worker",
        )
    )
    dispatcher = OutboxPublisher(
        PostgresOutboxRepository(postgres_engine),
        immediate,
        publisher_id="property-integration-publisher",
        logical_topic="WORKFLOW_STAGE_READY",
    )
    return workflows, runtime, processor, dispatcher


def _drain(dispatcher: OutboxPublisher) -> int:
    published = 0
    while dispatcher.publish_one():
        published += 1
    return published


def test_property_terms_recompute_reaches_audit_and_commit_with_canonical_contracts(
    property_repository: PostgresProjectRepository,
    property_postgres_engine: Engine,
) -> None:
    project = _independent_project(property_repository)
    workflows, runtime, processor, dispatcher = _pipeline(property_postgres_engine)
    initial = workflows.start(
        project_id=project.project_id,
        user_id="user-1",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="property-initial-workflow",
    )
    assert _drain(dispatcher) > 0
    assert (
        workflows.get(
            project_id=project.project_id,
            workflow_run_id=initial.workflow_run_id,
            user_id="user-1",
        ).status
        == WorkflowStatus.SUCCEEDED
    )
    before = ResultService(PostgresResultRepository(property_postgres_engine)).get_current(
        project_id=project.project_id,
        user_id="user-1",
    )

    selection = CandidateSelectionService(property_postgres_engine).select(
        project_id=project.project_id,
        user_id="user-1",
        result_bundle_id=before.result_bundle_id,
        candidate_id=before.primary_candidate_id,
        expected_head=before.head,
        idempotency_key="property-selection",
    )
    application = PropertyTermsService(property_postgres_engine).apply(
        project_id=project.project_id,
        selection_id=selection.selection_id,
        user_id="user-1",
        expected_state_version=selection.selected_state_version,
        terms=PropertyTermsInput(
            address="서울 마포구 공덕동 데모 점포 · 실매물 아님",
            area_sqm=33,
            floor=None,
            deposit_krw=30_000_000,
            monthly_rent_krw=2_200_000,
            management_fee_krw=200_000,
            key_money_krw=10_000_000,
        ),
        idempotency_key="property-terms",
    )

    assert _drain(dispatcher) == 3
    completed = workflows.get(
        project_id=project.project_id,
        workflow_run_id=application.recompute_workflow.workflow_run_id,
        user_id="user-1",
    )
    assert completed.status == WorkflowStatus.SUCCEEDED, processor.errors
    assert processor.errors == []

    after = ResultService(PostgresResultRepository(property_postgres_engine)).get_current(
        project_id=project.project_id,
        user_id="user-1",
    )
    assert after.workflow_run_id == application.recompute_workflow.workflow_run_id
    assert after.head.state_version == application.applied_state_version
    assert after.audit_status.value == "PASSED"

    recompute_audit = [task for task in runtime.tasks if task["task_type"] == "CANDIDATE_AUDIT"][-1]
    property_evidence = [
        record
        for record in recompute_audit["payload"]["evidence_records"]
        if record["source"]["source_type"] == "USER_FIELD"
    ]
    assert {record["claim_type"] for record in property_evidence} >= {
        "COST_DEPOSIT",
        "COST_ACQUISITION_OR_PREMIUM",
        "COST_MONTHLY_OCCUPANCY",
    }
    assert all(record["schema_version"] == "2.0.0" for record in property_evidence)
