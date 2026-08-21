from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, create_engine, text
from testcontainers.community.postgres import PostgresContainer
from worker.outbox import PostgresOutboxRepository

from app.agents.runtime import PostgresAgentCleanupSink
from app.domain.errors import (
    ContractValidationError,
    IdempotencyKeyReusedError,
    ProjectNotFoundError,
    ResultNotFoundError,
    StateVersionConflictError,
)
from app.domain.models import (
    BorrowingIntent,
    CafeTypePreference,
    FounderState,
    OperationMode,
    Project,
)
from app.main import create_app
from app.migrations import apply_migrations
from app.projects.postgres_repository import PostgresProjectRepository
from app.projects.service import ProjectService
from app.results.postgres_repository import PostgresResultRepository
from app.results.service import ResultService
from app.workflows.execution_repository import PostgresStageExecutionRepository
from app.workflows.models import (
    CheckpointOutcome,
    FailureOutcome,
    StageFailure,
    WorkflowCode,
    WorkflowStatus,
)
from app.workflows.postgres_repository import PostgresWorkflowRepository
from app.workflows.service import WorkflowService
from app.workflows.stage_context import PostgresStageContextRepository


class FixedIdentityVerifier:
    def verify(self, bearer_token: str) -> str:
        assert bearer_token == "valid-token"
        return "user-1"


@pytest.fixture(scope="module")
def postgres_engine() -> Engine:
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
def repository(postgres_engine: Engine) -> PostgresProjectRepository:
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE result_bundles, workflow_outbox, workflow_idempotency_records, "
                "workflow_events, "
                "stage_runs, workflow_runs, idempotency_records, project_events, "
                "venture_states, venture_projects CASCADE"
            )
        )
    return PostgresProjectRepository(postgres_engine)


def founder(*, own_funds_krw: int = 50_000_000) -> FounderState:
    return FounderState(
        target_area_input="수원 아주대 부근",
        own_funds_krw=own_funds_krw,
        borrowing_intent=BorrowingIntent.UNDECIDED,
        cafe_type_preference=CafeTypePreference.OPEN_TO_BOTH,
        operation_mode=OperationMode.DIRECT_FULL_TIME,
    )


def onboarded_project(repository: PostgresProjectRepository, *, user_id: str = "user-1"):
    service = ProjectService(repository)
    project = service.create_project(user_id=user_id, idempotency_key=f"create-{user_id}")
    return service.confirm_onboarding(
        project_id=project.project_id,
        user_id=user_id,
        idempotency_key=f"onboarding-{user_id}",
        founder=founder(),
    )


def test_migrations_are_idempotent(postgres_engine: Engine) -> None:
    apply_migrations(postgres_engine)
    with postgres_engine.connect() as connection:
        versions = connection.execute(
            text("SELECT version FROM schema_migrations ORDER BY version")
        ).scalars()
        assert list(versions) == [
            "0001_project_state.sql",
            "0002_workflow_outbox.sql",
            "0003_worker_lease_fence.sql",
            "0004_result_bundle.sql",
            "0005_stage_failure.sql",
            "0006_first_proposal_dag.sql",
        ]


def test_agent_session_cleanup_outbox_is_durable_and_idempotent(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    del repository
    sink = PostgresAgentCleanupSink(postgres_engine)
    arguments = {
        "runtime_resource": (
            "projects/gcp-project/locations/asia-northeast3/reasoningEngines/runtime-1"
        ),
        "user_id": "p-pseudonymous",
        "session_id": "session-1",
    }

    sink.enqueue_session_delete(**arguments)
    sink.enqueue_session_delete(**arguments)

    with postgres_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT topic, aggregate_id, payload_json "
                "FROM workflow_outbox WHERE topic = 'AGENT_SESSION_CLEANUP'"
            )
        ).mappings()
        assert list(rows) == [
            {
                "topic": "AGENT_SESSION_CLEANUP",
                "aggregate_id": "session-1",
                "payload_json": arguments,
            }
        ]


def test_project_and_state_survive_repository_recreation(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    service = ProjectService(repository)
    project = service.create_project(user_id="user-1", idempotency_key="create-1")
    service.confirm_onboarding(
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="onboarding-1",
        founder=founder(),
    )

    recreated = ProjectService(PostgresProjectRepository(postgres_engine))
    loaded = recreated.get_project(project_id=project.project_id, user_id="user-1")

    assert loaded.state is not None
    assert loaded.state.state_version == 1
    assert loaded.state.founder.own_funds_krw == 50_000_000


def test_fastapi_runtime_uses_configured_postgres_repository(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE result_bundles, workflow_outbox, workflow_idempotency_records, "
                "workflow_events, "
                "stage_runs, workflow_runs, idempotency_records, project_events, "
                "venture_states, venture_projects CASCADE"
            )
        )
    monkeypatch.setenv(
        "DATABASE_URL",
        postgres_engine.url.render_as_string(hide_password=False),
    )

    from fastapi.testclient import TestClient

    with TestClient(create_app(identity_verifier=FixedIdentityVerifier())) as client:
        created = client.post(
            "/v1/projects",
            headers={"Authorization": "Bearer valid-token", "Idempotency-Key": "create-1"},
            json={},
        )
        listed = client.get(
            "/v1/projects",
            headers={"Authorization": "Bearer valid-token"},
        )

    assert created.status_code == 201
    assert listed.status_code == 200
    assert listed.json() == [created.json()]


def test_project_queries_are_owner_scoped(repository: PostgresProjectRepository) -> None:
    service = ProjectService(repository)
    own = service.create_project(user_id="user-1", idempotency_key="create-1")
    other = service.create_project(user_id="user-2", idempotency_key="create-2")

    assert [project.project_id for project in service.list_projects(user_id="user-1")] == [
        own.project_id
    ]
    with pytest.raises(ProjectNotFoundError):
        service.get_project(project_id=other.project_id, user_id="user-1")


def test_same_request_is_replayed_and_changed_body_is_rejected(
    repository: PostgresProjectRepository,
) -> None:
    service = ProjectService(repository)
    project = service.create_project(user_id="user-1", idempotency_key="create-1")
    first = service.confirm_onboarding(
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="onboarding-1",
        founder=founder(),
    )
    replay = service.confirm_onboarding(
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="onboarding-1",
        founder=founder(),
    )

    assert replay == first
    with pytest.raises(IdempotencyKeyReusedError):
        service.confirm_onboarding(
            project_id=project.project_id,
            user_id="user-1",
            idempotency_key="onboarding-1",
            founder=founder(own_funds_krw=40_000_000),
        )


def test_concurrent_duplicate_commits_one_event_and_one_state(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    service = ProjectService(repository)
    project = service.create_project(user_id="user-1", idempotency_key="create-1")

    def confirm(_index: int) -> int:
        result = service.confirm_onboarding(
            project_id=project.project_id,
            user_id="user-1",
            idempotency_key="same-request",
            founder=founder(),
        )
        assert result.state is not None
        return result.state.state_version

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(confirm, range(2))) == [1, 1]

    with postgres_engine.connect() as connection:
        event_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM project_events "
                "WHERE project_id = :project_id AND event_type = 'ONBOARDING_CONFIRMED'"
            ),
            {"project_id": project.project_id},
        ).scalar_one()
        state_count = connection.execute(
            text("SELECT COUNT(*) FROM venture_states WHERE project_id = :project_id"),
            {"project_id": project.project_id},
        ).scalar_one()
    assert event_count == state_count == 1


def test_distinct_second_onboarding_is_a_state_version_conflict(
    repository: PostgresProjectRepository,
) -> None:
    service = ProjectService(repository)
    project = service.create_project(user_id="user-1", idempotency_key="create-1")
    service.confirm_onboarding(
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="onboarding-1",
        founder=founder(),
    )

    with pytest.raises(StateVersionConflictError):
        service.confirm_onboarding(
            project_id=project.project_id,
            user_id="user-1",
            idempotency_key="onboarding-2",
            founder=founder(),
        )


def test_validation_failure_rolls_back_idempotency_event_and_state(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    service = ProjectService(repository)
    project = service.create_project(user_id="user-1", idempotency_key="create-1")

    class RejectAllContracts:
        def validate_venture_state(self, _value: dict[str, object]) -> None:
            raise ContractValidationError("rejected for rollback test")

    rejecting_service = ProjectService(
        PostgresProjectRepository(postgres_engine, contracts=RejectAllContracts())
    )
    with pytest.raises(ContractValidationError):
        rejecting_service.confirm_onboarding(
            project_id=project.project_id,
            user_id="user-1",
            idempotency_key="onboarding-1",
            founder=founder(),
        )

    recovered = service.confirm_onboarding(
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="onboarding-1",
        founder=founder(),
    )
    assert recovered.state is not None
    assert recovered.state.state_version == 1


def test_workflow_requires_confirmed_onboarding_and_rolls_back_command(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    projects = ProjectService(repository)
    project = projects.create_project(user_id="user-1", idempotency_key="create-1")
    workflows = WorkflowService(
        PostgresWorkflowRepository(postgres_engine, policy_snapshot_id="policy-v1")
    )

    from app.domain.errors import WorkflowPreconditionError

    with pytest.raises(WorkflowPreconditionError):
        workflows.start(
            project_id=project.project_id,
            user_id="user-1",
            workflow_code=WorkflowCode.FIRST_PROPOSAL,
            idempotency_key="workflow-1",
        )

    projects.confirm_onboarding(
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="onboarding-1",
        founder=founder(),
    )
    run = workflows.start(
        project_id=project.project_id,
        user_id="user-1",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="workflow-1",
    )
    assert run.status == WorkflowStatus.QUEUED


def test_workflow_start_atomically_writes_run_stage_event_and_outbox(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project = onboarded_project(repository)
    workflows = WorkflowService(
        PostgresWorkflowRepository(postgres_engine, policy_snapshot_id="policy-v1")
    )

    run = workflows.start(
        project_id=project.project_id,
        user_id="user-1",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="workflow-1",
    )

    assert run.head.workflow_generation == 1
    assert run.head.state_version == 1
    assert run.head.policy_snapshot_id == "policy-v1"
    assert run.head.founder_snapshot_id == f"{project.project_id}:state:1:founder"
    assert run.head.area_snapshot_id == f"{project.project_id}:state:1:area"
    with postgres_engine.connect() as connection:
        counts = {
            table: connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            for table in (
                "workflow_runs",
                "stage_runs",
                "workflow_events",
                "workflow_idempotency_records",
                "workflow_outbox",
            )
        }
        outbox = connection.execute(
            text("SELECT topic, status, payload_json FROM workflow_outbox")
        ).mappings().one()
        digest_lengths = list(
            connection.execute(
                text(
                    "SELECT octet_length(request_digest) FROM idempotency_records "
                    "UNION ALL "
                    "SELECT octet_length(request_digest) FROM workflow_idempotency_records"
                )
            ).scalars()
        )
    assert counts == {
        "workflow_runs": 1,
        "stage_runs": 13,
        "workflow_events": 1,
        "workflow_idempotency_records": 1,
        "workflow_outbox": 1,
    }
    assert outbox["topic"] == "WORKFLOW_STAGE_READY"
    assert outbox["status"] == "PENDING"
    assert outbox["payload_json"]["workflow_run_id"] == run.workflow_run_id
    assert digest_lengths == [32, 32, 32]


def test_concurrent_workflow_redelivery_returns_one_run(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project = onboarded_project(repository)
    workflows = WorkflowService(
        PostgresWorkflowRepository(postgres_engine, policy_snapshot_id="policy-v1")
    )

    def start(_index: int) -> str:
        return workflows.start(
            project_id=project.project_id,
            user_id="user-1",
            workflow_code=WorkflowCode.FIRST_PROPOSAL,
            idempotency_key="same-command",
        ).workflow_run_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        workflow_ids = list(executor.map(start, range(2)))

    assert workflow_ids[0] == workflow_ids[1]
    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM workflow_runs")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM workflow_outbox")).scalar_one() == 1


def test_workflow_is_hidden_from_other_project_owner(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project = onboarded_project(repository)
    workflows = WorkflowService(
        PostgresWorkflowRepository(postgres_engine, policy_snapshot_id="policy-v1")
    )
    run = workflows.start(
        project_id=project.project_id,
        user_id="user-1",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="workflow-1",
    )

    from app.domain.errors import WorkflowNotFoundError

    with pytest.raises(WorkflowNotFoundError):
        workflows.get(
            project_id=project.project_id,
            workflow_run_id=run.workflow_run_id,
            user_id="user-2",
        )


def test_cancel_increments_generation_and_durably_cancels_stage(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project = onboarded_project(repository)
    workflows = WorkflowService(
        PostgresWorkflowRepository(postgres_engine, policy_snapshot_id="policy-v1")
    )
    run = workflows.start(
        project_id=project.project_id,
        user_id="user-1",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="workflow-1",
    )

    cancelled = workflows.cancel(
        project_id=project.project_id,
        workflow_run_id=run.workflow_run_id,
        user_id="user-1",
        idempotency_key="cancel-1",
    )
    replay = workflows.cancel(
        project_id=project.project_id,
        workflow_run_id=run.workflow_run_id,
        user_id="user-1",
        idempotency_key="cancel-1",
    )

    assert cancelled == replay
    assert cancelled.status == WorkflowStatus.CANCELLED
    events = workflows.list_events(
        project_id=project.project_id,
        workflow_run_id=run.workflow_run_id,
        user_id="user-1",
    )
    assert [event.event_type for event in events] == [
        "WORKFLOW_QUEUED",
        "WORKFLOW_CANCELLED",
    ]
    with postgres_engine.connect() as connection:
        generation = connection.execute(
            text("SELECT workflow_generation FROM venture_projects WHERE project_id=:id"),
            {"id": project.project_id},
        ).scalar_one()
        stage_statuses = list(connection.execute(text("SELECT status FROM stage_runs")).scalars())
        topics = list(
            connection.execute(
                text("SELECT topic FROM workflow_outbox ORDER BY outbox_id")
            ).scalars()
        )
    assert generation == 2
    assert set(stage_statuses) == {"CANCELLED"}
    assert topics == ["WORKFLOW_STAGE_READY", "WORKFLOW_CLEANUP"]


def test_http_202_workflow_survives_api_instance_shutdown(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE result_bundles, workflow_outbox, workflow_idempotency_records, "
                "workflow_events, "
                "stage_runs, workflow_runs, idempotency_records, project_events, "
                "venture_states, venture_projects CASCADE"
            )
        )
    monkeypatch.setenv(
        "DATABASE_URL",
        postgres_engine.url.render_as_string(hide_password=False),
    )
    monkeypatch.setenv("CAFFEMATE_POLICY_SNAPSHOT_ID", "policy-v1")

    from fastapi.testclient import TestClient

    headers = {"Authorization": "Bearer valid-token"}
    with TestClient(create_app(identity_verifier=FixedIdentityVerifier())) as client:
        project = client.post(
            "/v1/projects",
            headers={**headers, "Idempotency-Key": "create-1"},
            json={},
        ).json()
        client.post(
            f"/v1/projects/{project['project_id']}/onboarding/confirm",
            headers={**headers, "Idempotency-Key": "onboarding-1"},
            json={"founder": founder().model_dump(mode="json")},
        )
        response = client.post(
            f"/v1/projects/{project['project_id']}/workflows/FIRST_PROPOSAL",
            headers={**headers, "Idempotency-Key": "workflow-1"},
            json={},
        )
        assert response.status_code == 202
        workflow_run_id = response.json()["workflow_run_id"]

    loaded = WorkflowService(
        PostgresWorkflowRepository(postgres_engine, policy_snapshot_id="policy-v1")
    ).get(
        project_id=project["project_id"],
        workflow_run_id=workflow_run_id,
        user_id="user-1",
    )
    assert loaded.status == WorkflowStatus.QUEUED
    with postgres_engine.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM workflow_outbox WHERE status='PENDING'")
        ).scalar_one() == 1

    with TestClient(create_app(identity_verifier=FixedIdentityVerifier())) as restarted_client:
        cancelled = restarted_client.post(
            f"/v1/projects/{project['project_id']}/workflows/{workflow_run_id}:cancel",
            headers={**headers, "Idempotency-Key": "cancel-1"},
            json={},
        )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"


def create_ready_stage(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> tuple[Project, str, str, WorkflowService]:
    project = onboarded_project(repository)
    workflows = WorkflowService(
        PostgresWorkflowRepository(postgres_engine, policy_snapshot_id="policy-v1")
    )
    workflows.start(
        project_id=project.project_id,
        user_id="user-1",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="workflow-1",
    )
    with postgres_engine.connect() as connection:
        stage_id, input_digest = connection.execute(
            text(
                "SELECT stage_run_id, input_digest FROM stage_runs "
                "WHERE status='READY'"
            )
        ).one()
    return project, stage_id, input_digest, workflows


def create_commit_stage(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> tuple[Project, str, str, WorkflowService]:
    project, _stage_id, _input_digest, workflows = create_ready_stage(
        repository, postgres_engine
    )
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE workflow_runs SET status='RUNNING' "
                "WHERE project_id=:project_id"
            ),
            {"project_id": project.project_id},
        )
        connection.execute(
            text(
                "UPDATE stage_runs SET status=CASE WHEN stage_code='COMMIT_RESULT' "
                "THEN 'READY' ELSE 'SUCCEEDED' END"
            )
        )
        stage_id, input_digest = connection.execute(
            text(
                "SELECT stage_run_id, input_digest FROM stage_runs "
                "WHERE stage_code='COMMIT_RESULT'"
            )
        ).one()
    return project, stage_id, input_digest, workflows


@pytest.mark.parametrize(
    ("disposition", "workflow_status", "current_stage_status", "other_stage_status"),
    [
        ("WAITING_FOR_HUMAN", "WAITING_FOR_HUMAN", "WAITING_FOR_HUMAN", "PENDING"),
        ("ABSTAIN", "PARTIAL", "SKIPPED", "CANCELLED"),
    ],
)
def test_noncontinuing_stage_does_not_publish_or_persist_a_result(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
    disposition: str,
    workflow_status: str,
    current_stage_status: str,
    other_stage_status: str,
) -> None:
    project, stage_id, input_digest, _workflows = create_ready_stage(
        repository, postgres_engine
    )
    execution = PostgresStageExecutionRepository(postgres_engine)
    lease = execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-1",
        expected_input_digest=input_digest,
    )
    assert lease is not None

    assert execution.checkpoint(
        stage_run_id=stage_id,
        lease_token=lease.lease_token,
        input_digest=lease.input_digest,
        result={
            "stage_control": {
                "disposition": disposition,
                "reason_codes": ["AREA_SELECTION_REQUIRED"],
            },
            "area_resolution": {"selected": None},
        },
    ) == CheckpointOutcome.APPLIED

    with postgres_engine.connect() as connection:
        stored_workflow_status = connection.execute(
            text(
                "SELECT status FROM workflow_runs "
                "WHERE project_id=:project_id"
            ),
            {"project_id": project.project_id},
        ).scalar_one()
        current_status = connection.execute(
            text("SELECT status FROM stage_runs WHERE stage_run_id=:stage_run_id"),
            {"stage_run_id": stage_id},
        ).scalar_one()
        other_statuses = set(
            connection.execute(
                text("SELECT status FROM stage_runs WHERE stage_run_id<>:stage_run_id"),
                {"stage_run_id": stage_id},
            ).scalars()
        )
        outbox_count = connection.execute(
            text("SELECT COUNT(*) FROM workflow_outbox")
        ).scalar_one()
        result_count = connection.execute(
            text("SELECT COUNT(*) FROM result_bundles")
        ).scalar_one()
    assert stored_workflow_status == workflow_status
    assert current_status == current_stage_status
    assert other_statuses == {other_stage_status}
    assert outbox_count == 1
    assert result_count == 0


def test_stage_context_loads_fenced_state_and_direct_dependency_results(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project, stage_id, input_digest, _workflows = create_ready_stage(
        repository, postgres_engine
    )
    execution = PostgresStageExecutionRepository(postgres_engine)
    root_lease = execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-1",
        expected_input_digest=input_digest,
    )
    assert root_lease is not None
    root_result = {"area_resolution": "UNRESOLVED"}
    assert execution.checkpoint(
        stage_run_id=stage_id,
        lease_token=root_lease.lease_token,
        input_digest=root_lease.input_digest,
        result=root_result,
    ) == CheckpointOutcome.APPLIED

    with postgres_engine.connect() as connection:
        next_stage_id, next_digest = connection.execute(
            text(
                "SELECT stage_run_id, input_digest FROM stage_runs "
                "WHERE stage_code='CLAIM_PLAN' AND status='READY'"
            )
        ).one()
    next_lease = execution.claim(
        stage_run_id=next_stage_id,
        worker_id="worker-1",
        expected_input_digest=next_digest,
    )
    assert next_lease is not None

    loaded = PostgresStageContextRepository(postgres_engine).load(next_lease)

    assert loaded.project_id == project.project_id
    assert loaded.state.project_id == project.project_id
    assert loaded.state.state_version == next_lease.head.state_version
    assert loaded.dependency_results == {"AREA_RESOLUTION": root_result}


def test_first_proposal_dag_promotes_ready_stages_and_joins_both_branches(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project = onboarded_project(repository)
    workflows = WorkflowService(
        PostgresWorkflowRepository(postgres_engine, policy_snapshot_id="policy-v1")
    )
    run = workflows.start(
        project_id=project.project_id,
        user_id="user-1",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="workflow-1",
    )
    execution = PostgresStageExecutionRepository(postgres_engine)
    expected_batches = [
        {"AREA_RESOLUTION"},
        {"CLAIM_PLAN"},
        {"EVIDENCE_PLAN"},
        {"EVIDENCE_RETRIEVAL"},
        {"EVIDENCE_ASSESS"},
        {"EVIDENCE_FREEZE"},
        {"INDEPENDENT_SEED", "FRANCHISE_ELIGIBILITY"},
        {"PROPOSE_INDEPENDENT", "PROPOSE_FRANCHISE"},
        {"CALCULATE_GATE_RANK"},
        {"CANDIDATE_AUDIT"},
        {"COMMIT_RESULT"},
    ]

    for expected in expected_batches:
        with postgres_engine.connect() as connection:
            ready = connection.execute(
                text(
                    "SELECT stage_run_id, stage_code, input_digest FROM stage_runs "
                    "WHERE workflow_run_id=:workflow_run_id AND status='READY'"
                ),
                {"workflow_run_id": run.workflow_run_id},
            ).mappings().all()
        assert {stage["stage_code"] for stage in ready} == expected
        for stage in ready:
            lease = execution.claim(
                stage_run_id=stage["stage_run_id"],
                worker_id="worker-1",
                expected_input_digest=stage["input_digest"],
            )
            assert lease is not None
            result = (
                result_payload(project_id=project.project_id)
                if stage["stage_code"] == "COMMIT_RESULT"
                else {"stage": stage["stage_code"]}
            )
            assert execution.checkpoint(
                stage_run_id=stage["stage_run_id"],
                lease_token=lease.lease_token,
                input_digest=lease.input_digest,
                result=result,
            ) == CheckpointOutcome.APPLIED

    completed = workflows.get(
        project_id=project.project_id,
        workflow_run_id=run.workflow_run_id,
        user_id="user-1",
    )
    assert completed.status == WorkflowStatus.SUCCEEDED
    with postgres_engine.connect() as connection:
        statuses = set(
            connection.execute(
                text(
                    "SELECT status FROM stage_runs WHERE workflow_run_id=:workflow_run_id"
                ),
                {"workflow_run_id": run.workflow_run_id},
            ).scalars()
        )
        ready_messages = connection.execute(
            text(
                "SELECT COUNT(*) FROM workflow_outbox "
                "WHERE topic='WORKFLOW_STAGE_READY'"
            )
        ).scalar_one()
    assert statuses == {"SUCCEEDED"}
    assert ready_messages == 13


def valid_candidate(*, project_id: str, state_version: int = 1) -> dict[str, object]:
    initial_cash = {
        "currency": "KRW",
        "low": 40_000_000,
        "base": 45_000_000,
        "high": 50_000_000,
        "provenance_refs": ["evidence-1"],
    }
    monthly_fixed_cost = {
        "currency": "KRW",
        "low": 5_000_000,
        "base": 6_000_000,
        "high": 7_000_000,
        "provenance_refs": ["evidence-1"],
    }
    return {
        "schema_version": "2.0.0",
        "candidate_id": "candidate-1",
        "project_id": project_id,
        "state_version": state_version,
        "case_type": "INDEPENDENT",
        "display_name": "소형 개인카페",
        "review_status": "REVIEW_RECOMMENDED",
        "reason_codes": ["CURRENT_CONSTRAINTS_SATISFIED"],
        "summary": "현재 자금 범위에서 다음 검토 가치가 있는 후보",
        "rank": 1,
        "rank_basis": "ECONOMIC_AND_FOUNDER_FIT",
        "is_primary_next_review": True,
        "franchise": None,
        "independent_model": {"model_id": "independent-v1", "adjusted_fields": []},
        "evidence_refs": ["evidence-1"],
        "assumption_refs": [],
        "financial_summary": {
            "initial_cash": initial_cash,
            "monthly_fixed_cost": monthly_fixed_cost,
            "break_even_monthly_sales_krw": 15_000_000,
            "required_daily_orders": 80,
            "unknown_cost_fields": [],
        },
        "missing_fields": [],
        "risks": [],
        "counterfactuals": [
            {
                "variable": "monthly_rent",
                "condition": "월세가 기준보다 15% 높아짐",
                "decision_impact": "다음 검토 우선순위를 재계산",
            }
        ],
        "next_actions": ["실제 점포 조건 확인"],
    }


def result_payload(*, project_id: str, state_version: int = 1) -> dict[str, object]:
    return {
        "result_bundle": {
            "candidates": [valid_candidate(project_id=project_id, state_version=state_version)],
            "primary_candidate_id": "candidate-1",
            "audit_status": "PASSED",
        }
    }


def test_result_bundle_checkpoint_is_atomic_and_owner_scoped(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, stage_id, input_digest, _workflows = create_commit_stage(
        repository, postgres_engine
    )
    execution = PostgresStageExecutionRepository(
        postgres_engine,
        new_result_id=lambda: "result-1",
    )
    lease = execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-1",
        expected_input_digest=input_digest,
    )
    assert lease is not None

    assert execution.checkpoint(
        stage_run_id=stage_id,
        lease_token=lease.lease_token,
        input_digest=lease.input_digest,
        result=result_payload(project_id=project.project_id),
    ) == CheckpointOutcome.APPLIED
    assert execution.checkpoint(
        stage_run_id=stage_id,
        lease_token=lease.lease_token,
        input_digest=lease.input_digest,
        result=result_payload(project_id=project.project_id),
    ) == CheckpointOutcome.DUPLICATE_DISCARDED

    results = ResultService(PostgresResultRepository(postgres_engine))
    loaded = results.get_current(project_id=project.project_id, user_id="user-1")
    assert loaded.result_bundle_id == "result-1"
    assert loaded.workflow_run_id == lease.workflow_run_id
    assert loaded.head == lease.head
    assert loaded.primary_candidate_id == "candidate-1"
    assert loaded.candidates[0]["display_name"] == "소형 개인카페"
    with pytest.raises(ResultNotFoundError):
        results.get_current(project_id=project.project_id, user_id="user-2")

    with postgres_engine.connect() as connection:
        result_count = connection.execute(text("SELECT COUNT(*) FROM result_bundles")).scalar_one()
        pointer = connection.execute(
            text(
                "SELECT current_result_bundle_id FROM venture_projects "
                "WHERE project_id=:project_id"
            ),
            {"project_id": project.project_id},
        ).scalar_one()
    assert result_count == 1
    assert pointer == "result-1"

    monkeypatch.setenv(
        "DATABASE_URL",
        postgres_engine.url.render_as_string(hide_password=False),
    )
    from fastapi.testclient import TestClient

    with TestClient(create_app(identity_verifier=FixedIdentityVerifier())) as client:
        response = client.get(
            f"/v1/projects/{project.project_id}/result",
            headers={"Authorization": "Bearer valid-token"},
        )
    assert response.status_code == 200
    assert response.json()["result_bundle_id"] == "result-1"


def test_invalid_result_contract_rolls_back_bundle_and_stage_checkpoint(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project, stage_id, input_digest, _workflows = create_commit_stage(
        repository, postgres_engine
    )
    execution = PostgresStageExecutionRepository(postgres_engine)
    lease = execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-1",
        expected_input_digest=input_digest,
    )
    assert lease is not None

    with pytest.raises(ContractValidationError):
        execution.checkpoint(
            stage_run_id=stage_id,
            lease_token=lease.lease_token,
            input_digest=lease.input_digest,
            result=result_payload(project_id="another-project"),
        )

    with postgres_engine.connect() as connection:
        stage_status, result_count, pointer = connection.execute(
            text(
                "SELECT s.status, (SELECT COUNT(*) FROM result_bundles), "
                "p.current_result_bundle_id FROM stage_runs s "
                "JOIN workflow_runs w ON w.workflow_run_id=s.workflow_run_id "
                "JOIN venture_projects p ON p.project_id=w.project_id "
                "WHERE s.stage_run_id=:stage_run_id"
            ),
            {"stage_run_id": stage_id},
        ).one()
    assert (stage_status, result_count, pointer) == ("RUNNING", 0, None)

    assert execution.checkpoint(
        stage_run_id=stage_id,
        lease_token=lease.lease_token,
        input_digest=lease.input_digest,
        result=result_payload(project_id=project.project_id),
    ) == CheckpointOutcome.APPLIED


def test_cancelled_worker_bundle_is_discarded_before_payload_validation(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project, stage_id, input_digest, workflows = create_commit_stage(
        repository, postgres_engine
    )
    execution = PostgresStageExecutionRepository(postgres_engine)
    lease = execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-1",
        expected_input_digest=input_digest,
    )
    assert lease is not None
    workflows.cancel(
        project_id=project.project_id,
        workflow_run_id=lease.workflow_run_id,
        user_id="user-1",
        idempotency_key="cancel-1",
    )

    assert execution.checkpoint(
        stage_run_id=stage_id,
        lease_token=lease.lease_token,
        input_digest=lease.input_digest,
        result={"result_bundle": {"malformed": True}},
    ) == CheckpointOutcome.CANCELLED_DISCARDED
    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM result_bundles")).scalar_one() == 0


def test_expired_stage_lease_is_reclaimed_and_old_worker_cannot_checkpoint(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    _project, stage_id, input_digest, _workflows = create_ready_stage(repository, postgres_engine)
    clock = [datetime(2026, 8, 21, tzinfo=UTC)]
    tokens = iter(["old-token", "new-token"])
    execution = PostgresStageExecutionRepository(
        postgres_engine,
        now=lambda: clock[0],
        new_token=lambda: next(tokens),
    )

    old = execution.claim(
        stage_run_id=stage_id, worker_id="worker-1", expected_input_digest=input_digest
    )
    assert old is not None
    assert execution.claim(
        stage_run_id=stage_id, worker_id="worker-2", expected_input_digest=input_digest
    ) is None
    clock[0] += timedelta(seconds=46)
    new = execution.claim(
        stage_run_id=stage_id, worker_id="worker-2", expected_input_digest=input_digest
    )
    assert new is not None
    assert new.attempt == 2
    assert execution.checkpoint(
        stage_run_id=stage_id,
        lease_token=old.lease_token,
        input_digest=old.input_digest,
        result={"worker": "old"},
    ) == CheckpointOutcome.LEASE_REJECTED
    assert execution.checkpoint(
        stage_run_id=stage_id,
        lease_token=new.lease_token,
        input_digest=new.input_digest,
        result={"worker": "new"},
    ) == CheckpointOutcome.APPLIED
    assert execution.checkpoint(
        stage_run_id=stage_id,
        lease_token=new.lease_token,
        input_digest=new.input_digest,
        result={"worker": "new"},
    ) == CheckpointOutcome.DUPLICATE_DISCARDED


def test_retryable_stage_failure_is_fenced_and_third_attempt_terminates_workflow(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    _project, stage_id, input_digest, _workflows = create_ready_stage(
        repository, postgres_engine
    )
    tokens = iter(["attempt-1", "attempt-2", "attempt-3"])
    execution = PostgresStageExecutionRepository(
        postgres_engine,
        new_token=lambda: next(tokens),
    )
    failure = StageFailure(code="STAGE_PROCESSING_ERROR", retryable=True)

    first = execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-1",
        expected_input_digest=input_digest,
    )
    assert first is not None
    assert execution.record_failure(
        stage_run_id=stage_id,
        lease_token=first.lease_token,
        input_digest=input_digest,
        failure=failure,
    ) == FailureOutcome.RETRY_SCHEDULED

    second = execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-2",
        expected_input_digest=input_digest,
    )
    assert second is not None
    assert execution.record_failure(
        stage_run_id=stage_id,
        lease_token=first.lease_token,
        input_digest=input_digest,
        failure=failure,
    ) == FailureOutcome.LEASE_REJECTED
    assert execution.record_failure(
        stage_run_id=stage_id,
        lease_token=second.lease_token,
        input_digest=input_digest,
        failure=failure,
    ) == FailureOutcome.RETRY_SCHEDULED

    third = execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-3",
        expected_input_digest=input_digest,
    )
    assert third is not None
    assert third.attempt == 3
    assert execution.record_failure(
        stage_run_id=stage_id,
        lease_token=third.lease_token,
        input_digest=input_digest,
        failure=failure,
    ) == FailureOutcome.TERMINAL_FAILED

    with postgres_engine.connect() as connection:
        stage_status, stored_failure = connection.execute(
            text("SELECT status, failure_json FROM stage_runs WHERE stage_run_id=:id"),
            {"id": stage_id},
        ).one()
        workflow_status = connection.execute(
            text("SELECT status FROM workflow_runs")
        ).scalar_one()
        events = list(
            connection.execute(
                text(
                    "SELECT event_type FROM workflow_events "
                    "WHERE event_type LIKE 'STAGE_%' ORDER BY sequence_id"
                )
            ).scalars()
        )
    assert stage_status == "FAILED"
    assert stored_failure == {"code": "STAGE_PROCESSING_ERROR", "retryable": True}
    assert workflow_status == "FAILED"
    assert events == ["STAGE_RETRY_SCHEDULED", "STAGE_RETRY_SCHEDULED", "STAGE_FAILED"]
    assert execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-4",
        expected_input_digest=input_digest,
    ) is None


def test_nonretryable_failure_terminates_on_first_attempt_without_raw_message(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    _project, stage_id, input_digest, _workflows = create_ready_stage(
        repository, postgres_engine
    )
    execution = PostgresStageExecutionRepository(postgres_engine)
    lease = execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-1",
        expected_input_digest=input_digest,
    )
    assert lease is not None

    assert execution.record_failure(
        stage_run_id=stage_id,
        lease_token=lease.lease_token,
        input_digest=input_digest,
        failure=StageFailure(code="CONTRACT_REJECTED", retryable=False),
    ) == FailureOutcome.TERMINAL_FAILED
    with postgres_engine.connect() as connection:
        stage_status, stored_failure = connection.execute(
            text("SELECT status, failure_json FROM stage_runs WHERE stage_run_id=:id"),
            {"id": stage_id},
        ).one()
    assert stage_status == "FAILED"
    assert stored_failure == {"code": "CONTRACT_REJECTED", "retryable": False}


def test_internal_stage_authorization_requires_exact_live_lease(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    _project, stage_id, input_digest, _workflows = create_ready_stage(repository, postgres_engine)
    clock = [datetime(2026, 8, 21, tzinfo=UTC)]
    execution = PostgresStageExecutionRepository(postgres_engine, now=lambda: clock[0])
    lease = execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-1",
        expected_input_digest=input_digest,
    )
    assert lease is not None

    assert execution.authorize(lease)
    assert not execution.authorize(lease.model_copy(update={"lease_token": "forged-token"}))
    clock[0] += timedelta(seconds=46)
    assert not execution.authorize(lease)


def test_cancelled_and_timed_out_results_are_never_checkpointed(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project, stage_id, input_digest, workflows = create_ready_stage(repository, postgres_engine)
    clock = [datetime(2026, 8, 21, tzinfo=UTC)]
    execution = PostgresStageExecutionRepository(postgres_engine, now=lambda: clock[0])
    lease = execution.claim(
        stage_run_id=stage_id, worker_id="worker-1", expected_input_digest=input_digest
    )
    assert lease is not None
    workflows.cancel(
        project_id=project.project_id,
        workflow_run_id=lease.workflow_run_id,
        user_id="user-1",
        idempotency_key="cancel-1",
    )
    assert execution.checkpoint(
        stage_run_id=stage_id,
        lease_token=lease.lease_token,
        input_digest=lease.input_digest,
        result={},
    ) == CheckpointOutcome.CANCELLED_DISCARDED


def test_expired_result_is_late_and_heartbeat_extends_current_lease(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    _project, stage_id, input_digest, _workflows = create_ready_stage(repository, postgres_engine)
    clock = [datetime(2026, 8, 21, tzinfo=UTC)]
    execution = PostgresStageExecutionRepository(postgres_engine, now=lambda: clock[0])
    lease = execution.claim(
        stage_run_id=stage_id, worker_id="worker-1", expected_input_digest=input_digest
    )
    assert lease is not None
    clock[0] += timedelta(seconds=15)
    assert execution.heartbeat(stage_run_id=stage_id, lease_token=lease.lease_token)
    clock[0] += timedelta(seconds=46)
    assert not execution.heartbeat(stage_run_id=stage_id, lease_token=lease.lease_token)
    assert execution.checkpoint(
        stage_run_id=stage_id,
        lease_token=lease.lease_token,
        input_digest=lease.input_digest,
        result={"must": "not persist"},
    ) == CheckpointOutcome.LATE_DISCARDED
    with postgres_engine.connect() as connection:
        status, result = connection.execute(
            text("SELECT status, result_json FROM stage_runs WHERE stage_run_id=:id"),
            {"id": stage_id},
        ).one()
    assert (status, result) == ("RUNNING", None)


@pytest.mark.parametrize(
    ("column", "replacement"),
    [
        ("workflow_generation", 99),
        ("state_version", 99),
        ("founder_snapshot_id", "changed-founder"),
        ("area_snapshot_id", "changed-area"),
        ("evidence_snapshot_id", "changed-evidence"),
        ("policy_snapshot_id", "changed-policy"),
        ("index_generation_id", "changed-index"),
        ("seed_registry_id", "changed-seed"),
    ],
)
def test_each_full_head_dimension_blocks_checkpoint(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
    column: str,
    replacement: object,
) -> None:
    _project, stage_id, input_digest, _workflows = create_ready_stage(repository, postgres_engine)
    execution = PostgresStageExecutionRepository(postgres_engine)
    lease = execution.claim(
        stage_run_id=stage_id, worker_id="worker-1", expected_input_digest=input_digest
    )
    assert lease is not None
    allowed_columns = {
        "workflow_generation", "state_version", "founder_snapshot_id", "area_snapshot_id",
        "evidence_snapshot_id", "policy_snapshot_id", "index_generation_id", "seed_registry_id",
    }
    assert column in allowed_columns
    with postgres_engine.begin() as connection:
        connection.execute(
            text(f"UPDATE project_heads SET {column}=:value"),
            {"value": replacement},
        )

    assert execution.checkpoint(
        stage_run_id=stage_id,
        lease_token=lease.lease_token,
        input_digest=lease.input_digest,
        result={"must": "not persist"},
    ) == CheckpointOutcome.STALE_DISCARDED
    with postgres_engine.connect() as connection:
        row = connection.execute(
            text("SELECT status, result_json FROM stage_runs WHERE stage_run_id=:id"),
            {"id": stage_id},
        ).one()
    assert row == ("RUNNING", None)


def test_outbox_claim_recovery_is_at_least_once_and_token_fenced(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    create_ready_stage(repository, postgres_engine)
    clock = [datetime(2026, 8, 21, tzinfo=UTC)]
    tokens = iter(["claim-old", "claim-new"])
    outbox = PostgresOutboxRepository(
        postgres_engine,
        now=lambda: clock[0],
        new_token=lambda: next(tokens),
    )
    old = outbox.claim_next(publisher_id="publisher-1")
    assert old is not None
    assert outbox.claim_next(publisher_id="publisher-2") is None
    clock[0] += timedelta(seconds=46)
    new = outbox.claim_next(publisher_id="publisher-2")
    assert new is not None
    assert new.outbox_id == old.outbox_id
    assert not outbox.mark_published(
        outbox_id=old.outbox_id,
        claim_token=old.claim_token,
        pubsub_message_id="old-message",
    )
    assert outbox.mark_published(
        outbox_id=new.outbox_id,
        claim_token=new.claim_token,
        pubsub_message_id="new-message",
    )
    with postgres_engine.connect() as connection:
        status, attempts = connection.execute(
            text("SELECT status, attempts FROM workflow_outbox")
        ).one()
    assert (status, attempts) == ("PUBLISHED", 2)


def test_outbox_topic_filter_does_not_publish_cleanup_to_stage_subscription(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project, _stage_id, _input_digest, workflows = create_ready_stage(
        repository, postgres_engine
    )
    with postgres_engine.connect() as connection:
        workflow_run_id = connection.execute(
            text("SELECT workflow_run_id FROM workflow_runs")
        ).scalar_one()
    workflows.cancel(
        project_id=project.project_id,
        workflow_run_id=workflow_run_id,
        user_id="user-1",
        idempotency_key="cancel-1",
    )
    outbox = PostgresOutboxRepository(postgres_engine, new_token=lambda: "cleanup-claim")

    cleanup = outbox.claim_next(
        publisher_id="cleanup-publisher",
        logical_topic="WORKFLOW_CLEANUP",
    )

    assert cleanup is not None
    assert cleanup.topic == "WORKFLOW_CLEANUP"
    with postgres_engine.connect() as connection:
        stage_status = connection.execute(
            text(
                "SELECT status FROM workflow_outbox "
                "WHERE topic='WORKFLOW_STAGE_READY'"
            )
        ).scalar_one()
    assert stage_status == "PENDING"
