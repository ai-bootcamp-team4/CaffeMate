from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, create_engine, text
from testcontainers.community.postgres import PostgresContainer
from worker.outbox import PostgresOutboxRepository

from app.domain.errors import (
    ContractValidationError,
    IdempotencyKeyReusedError,
    ProjectNotFoundError,
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
from app.workflows.execution_repository import PostgresStageExecutionRepository
from app.workflows.models import CheckpointOutcome, WorkflowCode, WorkflowStatus
from app.workflows.postgres_repository import PostgresWorkflowRepository
from app.workflows.service import WorkflowService


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
                "TRUNCATE workflow_outbox, workflow_idempotency_records, workflow_events, "
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
                "TRUNCATE workflow_outbox, workflow_idempotency_records, workflow_events, "
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
    assert counts == {table: 1 for table in counts}
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
        stage_status = connection.execute(text("SELECT status FROM stage_runs")).scalar_one()
        topics = list(
            connection.execute(
                text("SELECT topic FROM workflow_outbox ORDER BY outbox_id")
            ).scalars()
        )
    assert generation == 2
    assert stage_status == "CANCELLED"
    assert topics == ["WORKFLOW_STAGE_READY", "WORKFLOW_CLEANUP"]


def test_http_202_workflow_survives_api_instance_shutdown(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE workflow_outbox, workflow_idempotency_records, workflow_events, "
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
) -> tuple[Project, str, WorkflowService]:
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
        stage_id = connection.execute(text("SELECT stage_run_id FROM stage_runs")).scalar_one()
    return project, stage_id, workflows


def test_expired_stage_lease_is_reclaimed_and_old_worker_cannot_checkpoint(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    _project, stage_id, _workflows = create_ready_stage(repository, postgres_engine)
    clock = [datetime(2026, 8, 21, tzinfo=UTC)]
    tokens = iter(["old-token", "new-token"])
    execution = PostgresStageExecutionRepository(
        postgres_engine,
        now=lambda: clock[0],
        new_token=lambda: next(tokens),
    )

    old = execution.claim(stage_run_id=stage_id, worker_id="worker-1")
    assert old is not None
    assert execution.claim(stage_run_id=stage_id, worker_id="worker-2") is None
    clock[0] += timedelta(seconds=46)
    new = execution.claim(stage_run_id=stage_id, worker_id="worker-2")
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


def test_cancelled_and_timed_out_results_are_never_checkpointed(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project, stage_id, workflows = create_ready_stage(repository, postgres_engine)
    clock = [datetime(2026, 8, 21, tzinfo=UTC)]
    execution = PostgresStageExecutionRepository(postgres_engine, now=lambda: clock[0])
    lease = execution.claim(stage_run_id=stage_id, worker_id="worker-1")
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
    _project, stage_id, _workflows = create_ready_stage(repository, postgres_engine)
    clock = [datetime(2026, 8, 21, tzinfo=UTC)]
    execution = PostgresStageExecutionRepository(postgres_engine, now=lambda: clock[0])
    lease = execution.claim(stage_run_id=stage_id, worker_id="worker-1")
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
    _project, stage_id, _workflows = create_ready_stage(repository, postgres_engine)
    execution = PostgresStageExecutionRepository(postgres_engine)
    lease = execution.claim(stage_run_id=stage_id, worker_id="worker-1")
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
