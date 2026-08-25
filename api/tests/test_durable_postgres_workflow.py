from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError
from testcontainers.community.postgres import PostgresContainer

from app.candidates.seed_registry import IndependentSeedRegistry
from app.domain.errors import ResultNotFoundError
from app.domain.models import BorrowingIntent, CafeTypePreference, FounderState, OperationMode
from app.main import create_app
from app.migrations import apply_migrations
from app.projects.postgres_repository import PostgresProjectRepository
from app.projects.service import ProjectService
from app.results.postgres_repository import PostgresResultRepository
from app.verification.runtime import InlineQueuedWorkflowOperations
from app.workflows.dispatch import PostgresPubSubWorkflowDispatcher
from app.workflows.execution import PostgresFirstProposalExecutor, PostgresProgressSink
from app.workflows.lease import PostgresWorkflowLeaseRepository
from app.workflows.models import FailureOutcome, StageFailure, WorkflowCode, WorkflowStatus
from app.workflows.postgres_repository import PostgresWorkflowRepository
from app.workflows.progress import FirstProposalProgressStage
from app.workflows.service import WorkflowService
from tests.workflow_test_support import (
    IdentityFixture,
    PublisherFixture,
    RepositoryPipeline,
    workflow_service,
)


@pytest.fixture(scope="module")
def postgres_engine() -> Iterator[Engine]:
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


def test_start_queues_workflow_then_executor_checkpoints_real_progress(
    postgres_engine: Engine,
) -> None:
    projects = ProjectService(PostgresProjectRepository(postgres_engine))
    project = projects.create_project(user_id="user-1", idempotency_key="create-1")
    projects.confirm_onboarding(
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="onboarding-1",
        founder=FounderState(
            target_area_input="서울특별시 마포구 공덕동",
            own_funds_krw=400_000_000,
            borrowing_intent=BorrowingIntent.NO,
            cafe_type_preference=CafeTypePreference.OPEN_TO_BOTH,
            operation_mode=OperationMode.DIRECT_FULL_TIME,
        ),
    )
    registry = IndependentSeedRegistry.load_default()
    workflows = WorkflowService(
        PostgresWorkflowRepository(
            postgres_engine,
            policy_snapshot_id="policy-1",
            seed_registry_id=registry.registry_id,
            pipeline=RepositoryPipeline(registry),
            seed_registry=registry,
        )
    )

    first = workflows.start(
        project_id=project.project_id,
        user_id="user-1",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="analysis-1",
    )
    replay = workflows.start(
        project_id=project.project_id,
        user_id="user-1",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="analysis-1",
    )
    progress = workflows.get_progress(
        project_id=project.project_id,
        workflow_run_id=first.workflow_run_id,
        user_id="user-1",
    )
    assert first.status == WorkflowStatus.QUEUED
    assert replay.workflow_run_id == first.workflow_run_id
    assert progress.completed_stage_count == 0
    assert progress.total_stage_count == 6
    assert [stage.stage_code for stage in progress.stages] == [
        "EVIDENCE_RETRIEVAL",
        "EVIDENCE_ASSESS",
        "PROPOSAL_GENERATION",
        "FINANCE_AND_RANK",
        "CANDIDATE_AUDIT",
        "COMMIT_RESULT",
    ]
    assert progress.current_stage_codes == []
    with pytest.raises(ResultNotFoundError):
        PostgresResultRepository(postgres_engine).get_current(
            project_id=project.project_id,
            user_id="user-1",
        )
    with postgres_engine.connect() as connection:
        execution = connection.execute(
            text(
                "SELECT stage_run_id, input_digest FROM stage_runs "
                "WHERE workflow_run_id=:workflow_run_id AND stage_code='RUN_PROPOSAL'"
            ),
            {"workflow_run_id": first.workflow_run_id},
        ).mappings().one()
        assert connection.execute(
            text(
                "SELECT count(*) FROM workflow_outbox "
                "WHERE topic='WORKFLOW_STAGE_READY' AND aggregate_id=:workflow_run_id"
            ),
            {"workflow_run_id": first.workflow_run_id},
        ).scalar_one() == 1

    leases = PostgresWorkflowLeaseRepository(postgres_engine)
    lease = leases.claim(
        stage_run_id=execution["stage_run_id"],
        worker_id="test-worker",
        expected_input_digest=execution["input_digest"],
    )
    assert lease is not None
    PostgresFirstProposalExecutor(
        postgres_engine,
        RepositoryPipeline(registry),
        leases,
    ).execute(lease)

    finished = workflows.get_progress(
        project_id=project.project_id,
        workflow_run_id=first.workflow_run_id,
        user_id="user-1",
    )
    result = PostgresResultRepository(postgres_engine).get_current(
        project_id=project.project_id,
        user_id="user-1",
    )
    assert finished.status == WorkflowStatus.SUCCEEDED
    assert finished.completed_stage_count == 6
    assert finished.total_stage_count == 6
    expected_stage_codes = [stage.value for stage in FirstProposalProgressStage]
    assert [stage.stage_code for stage in finished.stages] == expected_stage_codes
    assert result.workflow_run_id == first.workflow_run_id


def test_second_start_reuses_current_active_workflow(
    postgres_engine: Engine,
) -> None:
    projects = ProjectService(PostgresProjectRepository(postgres_engine))
    project = projects.create_project(user_id="resume-user", idempotency_key="resume-create")
    projects.confirm_onboarding(
        project_id=project.project_id,
        user_id="resume-user",
        idempotency_key="resume-onboarding",
        founder=FounderState(
            target_area_input="서울특별시 마포구 공덕동",
            own_funds_krw=400_000_000,
            borrowing_intent=BorrowingIntent.NO,
            cafe_type_preference=CafeTypePreference.OPEN_TO_BOTH,
            operation_mode=OperationMode.DIRECT_FULL_TIME,
        ),
    )
    workflows = workflow_service(postgres_engine)
    first = workflows.start(
        project_id=project.project_id,
        user_id="resume-user",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="resume-analysis-1",
    )
    resumed = workflows.start(
        project_id=project.project_id,
        user_id="resume-user",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="resume-analysis-2",
    )

    assert resumed.workflow_run_id == first.workflow_run_id
    assert resumed.status == WorkflowStatus.QUEUED
    with postgres_engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT count(*) FROM workflow_runs "
                "WHERE project_id=:project_id AND workflow_code='FIRST_PROPOSAL'"
            ),
            {"project_id": project.project_id},
        ).scalar_one() == 1
        assert connection.execute(
            text(
                "SELECT count(*) FROM workflow_outbox "
                "WHERE aggregate_id=:workflow_run_id AND topic='WORKFLOW_STAGE_READY'"
            ),
            {"workflow_run_id": first.workflow_run_id},
        ).scalar_one() == 1


def test_workflow_outbox_respects_available_at(
    postgres_engine: Engine,
) -> None:
    projects = ProjectService(PostgresProjectRepository(postgres_engine))
    project = projects.create_project(user_id="dispatch-user", idempotency_key="dispatch-create")
    projects.confirm_onboarding(
        project_id=project.project_id,
        user_id="dispatch-user",
        idempotency_key="dispatch-onboarding",
        founder=FounderState(
            target_area_input="서울특별시 마포구 공덕동",
            own_funds_krw=400_000_000,
            borrowing_intent=BorrowingIntent.NO,
            cafe_type_preference=CafeTypePreference.OPEN_TO_BOTH,
            operation_mode=OperationMode.DIRECT_FULL_TIME,
        ),
    )
    workflows = workflow_service(postgres_engine)
    started = workflows.start(
        project_id=project.project_id,
        user_id="dispatch-user",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="dispatch-analysis",
    )
    now = datetime.now(UTC)
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workflow_outbox SET available_at=:available_at "
                "WHERE aggregate_id=:workflow_run_id AND topic='WORKFLOW_STAGE_READY'"
            ),
            {
                "available_at": now + timedelta(minutes=5),
                "workflow_run_id": started.workflow_run_id,
            },
        )
    publisher = PublisherFixture()
    dispatcher = PostgresPubSubWorkflowDispatcher(
        postgres_engine,
        topic_resource="projects/test/topics/workflow-stage-ready",
        publisher_id="dispatch-test",
        client=publisher,
        now=lambda: now,
        new_token=lambda: "claim-token",
    )

    assert dispatcher.dispatch(started.workflow_run_id) is False
    assert publisher.messages == []

    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workflow_outbox SET available_at=:available_at "
                "WHERE aggregate_id=:workflow_run_id AND topic='WORKFLOW_STAGE_READY'"
            ),
            {
                "available_at": now - timedelta(seconds=1),
                "workflow_run_id": started.workflow_run_id,
            },
        )
    assert dispatcher.dispatch(started.workflow_run_id) is True
    assert len(publisher.messages) == 1
    with postgres_engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT status FROM workflow_outbox "
                "WHERE aggregate_id=:workflow_run_id AND topic='WORKFLOW_STAGE_READY'"
            ),
            {"workflow_run_id": started.workflow_run_id},
        ).scalar_one() == "PUBLISHED"


def test_commit_authorization_locks_authoritative_project_row(
    postgres_engine: Engine,
) -> None:
    projects = ProjectService(PostgresProjectRepository(postgres_engine))
    project = projects.create_project(user_id="lock-user", idempotency_key="lock-create")
    projects.confirm_onboarding(
        project_id=project.project_id,
        user_id="lock-user",
        idempotency_key="lock-onboarding",
        founder=FounderState(
            target_area_input="서울특별시 마포구 공덕동",
            own_funds_krw=400_000_000,
            borrowing_intent=BorrowingIntent.NO,
            cafe_type_preference=CafeTypePreference.OPEN_TO_BOTH,
            operation_mode=OperationMode.DIRECT_FULL_TIME,
        ),
    )
    workflows = workflow_service(postgres_engine)
    started = workflows.start(
        project_id=project.project_id,
        user_id="lock-user",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="lock-analysis",
    )
    with postgres_engine.connect() as connection:
        execution = connection.execute(
            text(
                "SELECT stage_run_id, input_digest FROM stage_runs "
                "WHERE workflow_run_id=:workflow_run_id AND stage_code='RUN_PROPOSAL'"
            ),
            {"workflow_run_id": started.workflow_run_id},
        ).mappings().one()
    leases = PostgresWorkflowLeaseRepository(postgres_engine)
    lease = leases.claim(
        stage_run_id=execution["stage_run_id"],
        worker_id="lock-worker",
        expected_input_digest=execution["input_digest"],
    )
    assert lease is not None
    with postgres_engine.begin() as commit_connection:
        assert leases.authorize_mutation(
            commit_connection,
            lease=lease,
            now=datetime.now(UTC),
        )
        with postgres_engine.connect() as contender:
            with pytest.raises(DBAPIError):
                contender.execute(
                    text(
                        "SELECT project_id FROM venture_projects "
                        "WHERE project_id=:project_id FOR UPDATE NOWAIT"
                    ),
                    {"project_id": project.project_id},
                )


def test_retry_resets_public_checkpoints_and_records_second_attempt(
    postgres_engine: Engine,
) -> None:
    projects = ProjectService(PostgresProjectRepository(postgres_engine))
    project = projects.create_project(user_id="user-retry", idempotency_key="retry-create")
    projects.confirm_onboarding(
        project_id=project.project_id,
        user_id="user-retry",
        idempotency_key="retry-onboarding",
        founder=FounderState(
            target_area_input="서울특별시 마포구 공덕동",
            own_funds_krw=400_000_000,
            borrowing_intent=BorrowingIntent.NO,
            cafe_type_preference=CafeTypePreference.OPEN_TO_BOTH,
            operation_mode=OperationMode.DIRECT_FULL_TIME,
        ),
    )
    registry = IndependentSeedRegistry.load_default()
    workflows = WorkflowService(
        PostgresWorkflowRepository(
            postgres_engine,
            policy_snapshot_id="policy-1",
            seed_registry_id=registry.registry_id,
            pipeline=RepositoryPipeline(registry),
            seed_registry=registry,
        )
    )
    started = workflows.start(
        project_id=project.project_id,
        user_id="user-retry",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="retry-analysis",
    )
    with postgres_engine.connect() as connection:
        execution = connection.execute(
            text(
                "SELECT stage_run_id, input_digest FROM stage_runs "
                "WHERE workflow_run_id=:workflow_run_id AND stage_code='RUN_PROPOSAL'"
            ),
            {"workflow_run_id": started.workflow_run_id},
        ).mappings().one()

    leases = PostgresWorkflowLeaseRepository(postgres_engine)
    first_lease = leases.claim(
        stage_run_id=execution["stage_run_id"],
        worker_id="retry-worker-1",
        expected_input_digest=execution["input_digest"],
    )
    assert first_lease is not None
    first_progress = PostgresProgressSink(postgres_engine, leases, first_lease)
    first_progress.start(FirstProposalProgressStage.EVIDENCE_RETRIEVAL)
    first_progress.complete(FirstProposalProgressStage.EVIDENCE_RETRIEVAL)
    first_progress.start(FirstProposalProgressStage.EVIDENCE_ASSESS)

    outcome = leases.record_failure(
        stage_run_id=first_lease.stage_run_id,
        lease_token=first_lease.lease_token,
        input_digest=first_lease.input_digest,
        failure=StageFailure(code="CONTROL_API_TRANSPORT_FAILED", retryable=True),
    )
    assert outcome == FailureOutcome.RETRY_SCHEDULED
    retry_progress = workflows.get_progress(
        project_id=project.project_id,
        workflow_run_id=started.workflow_run_id,
        user_id="user-retry",
    )
    assert retry_progress.status == WorkflowStatus.QUEUED
    assert retry_progress.completed_stage_count == 0
    assert all(stage.status.value == "PENDING" for stage in retry_progress.stages)
    assert all(stage.attempt == 0 for stage in retry_progress.stages)

    second_lease = leases.claim(
        stage_run_id=execution["stage_run_id"],
        worker_id="retry-worker-2",
        expected_input_digest=execution["input_digest"],
    )
    assert second_lease is not None
    assert second_lease.attempt == 2
    PostgresFirstProposalExecutor(
        postgres_engine,
        RepositoryPipeline(registry),
        leases,
    ).execute(second_lease)

    finished = workflows.get_progress(
        project_id=project.project_id,
        workflow_run_id=started.workflow_run_id,
        user_id="user-retry",
    )
    assert finished.status == WorkflowStatus.SUCCEEDED
    assert finished.completed_stage_count == 6
    assert all(stage.attempt == 2 for stage in finished.stages)


def test_terminal_internal_failure_is_projected_to_public_progress(
    postgres_engine: Engine,
) -> None:
    projects = ProjectService(PostgresProjectRepository(postgres_engine))
    project = projects.create_project(
        user_id="failure-user",
        idempotency_key="failure-create",
    )
    projects.confirm_onboarding(
        project_id=project.project_id,
        user_id="failure-user",
        idempotency_key="failure-onboarding",
        founder=FounderState(
            target_area_input="서울특별시 마포구 공덕동",
            own_funds_krw=400_000_000,
            borrowing_intent=BorrowingIntent.NO,
            cafe_type_preference=CafeTypePreference.OPEN_TO_BOTH,
            operation_mode=OperationMode.DIRECT_FULL_TIME,
        ),
    )
    workflows = workflow_service(postgres_engine)
    started = workflows.start(
        project_id=project.project_id,
        user_id="failure-user",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="failure-analysis",
    )
    with postgres_engine.connect() as connection:
        execution = connection.execute(
            text(
                "SELECT stage_run_id, input_digest FROM stage_runs "
                "WHERE workflow_run_id=:workflow_run_id AND stage_code='RUN_PROPOSAL'"
            ),
            {"workflow_run_id": started.workflow_run_id},
        ).mappings().one()

    leases = PostgresWorkflowLeaseRepository(postgres_engine)
    failure = StageFailure(code="CONTROL_API_TRANSPORT_FAILED", retryable=True)
    for attempt in range(1, 4):
        lease = leases.claim(
            stage_run_id=execution["stage_run_id"],
            worker_id=f"failure-worker-{attempt}",
            expected_input_digest=execution["input_digest"],
        )
        assert lease is not None
        outcome = leases.record_failure(
            stage_run_id=lease.stage_run_id,
            lease_token=lease.lease_token,
            input_digest=lease.input_digest,
            failure=failure,
        )
        assert outcome == (
            FailureOutcome.RETRY_SCHEDULED
            if attempt < 3
            else FailureOutcome.TERMINAL_FAILED
        )

    progress = workflows.get_progress(
        project_id=project.project_id,
        workflow_run_id=started.workflow_run_id,
        user_id="failure-user",
    )
    assert progress.status == WorkflowStatus.FAILED
    assert progress.terminal_reason_codes == ["CONTROL_API_TRANSPORT_FAILED"]


def test_verification_runner_executes_queued_workflow_with_public_progress(
    postgres_engine: Engine,
) -> None:
    projects = ProjectService(PostgresProjectRepository(postgres_engine))
    project = projects.create_project(
        user_id="verification-user",
        idempotency_key="verification-create",
    )
    projects.confirm_onboarding(
        project_id=project.project_id,
        user_id="verification-user",
        idempotency_key="verification-onboarding",
        founder=FounderState(
            target_area_input="서울특별시 마포구 공덕동",
            own_funds_krw=400_000_000,
            borrowing_intent=BorrowingIntent.NO,
            cafe_type_preference=CafeTypePreference.OPEN_TO_BOTH,
            operation_mode=OperationMode.DIRECT_FULL_TIME,
        ),
    )
    registry = IndependentSeedRegistry.load_default()
    pipeline = RepositoryPipeline(registry)
    base_workflows = WorkflowService(
        PostgresWorkflowRepository(
            postgres_engine,
            policy_snapshot_id="policy-1",
            seed_registry_id=registry.registry_id,
            pipeline=pipeline,
            seed_registry=registry,
        )
    )
    workflows = InlineQueuedWorkflowOperations(
        base_workflows,
        postgres_engine,
        pipeline,
        heartbeat_interval_seconds=0.01,
    )

    started = workflows.start(
        project_id=project.project_id,
        user_id="verification-user",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="verification-analysis",
    )
    progress = workflows.get_progress(
        project_id=project.project_id,
        workflow_run_id=started.workflow_run_id,
        user_id="verification-user",
    )

    assert started.status == WorkflowStatus.QUEUED
    assert progress.status == WorkflowStatus.SUCCEEDED
    assert progress.completed_stage_count == 6
    assert all(stage.status.value == "SUCCEEDED" for stage in progress.stages)


def test_public_start_returns_queued_run_before_result_exists(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        postgres_engine.url.render_as_string(hide_password=False),
    )
    monkeypatch.setenv("CAFFEMATE_POLICY_SNAPSHOT_ID", "policy-1")
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(
        create_app(
            identity_verifier=IdentityFixture(),
            workflow_service=workflow_service(postgres_engine),
        )
    ) as client:
        created = client.post(
            "/v1/projects",
            headers={**headers, "Idempotency-Key": "create-user-2"},
            json={},
        )
        project_id = created.json()["project_id"]
        onboarded = client.post(
            f"/v1/projects/{project_id}/onboarding/confirm",
            headers={**headers, "Idempotency-Key": "onboard-user-2"},
            json={
                "founder": {
                    "target_area_input": "서울특별시 성동구 성수동1가",
                    "own_funds_krw": 400_000_000,
                    "borrowing_intent": "NO",
                    "cafe_type_preference": "OPEN_TO_BOTH",
                    "operation_mode": "DIRECT_FULL_TIME",
                }
            },
        )
        started = client.post(
            f"/v1/projects/{project_id}/workflows/FIRST_PROPOSAL",
            headers={**headers, "Idempotency-Key": "analysis-user-2"},
            json={},
        )
        result = client.get(f"/v1/projects/{project_id}/result", headers=headers)

    assert created.status_code == 201
    assert onboarded.status_code == 200
    assert started.status_code == 202
    assert started.json()["status"] == "QUEUED"
    assert result.status_code == 404
