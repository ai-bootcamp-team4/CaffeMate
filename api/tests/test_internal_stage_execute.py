from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.agents.runtime import AgentRuntimeError
from app.auth import IdentityVerifier
from app.main import create_app
from app.workflows.models import HeadFence, StageLease
from app.workflows.stage_service import StageExecutionService


class FakeIdentity(IdentityVerifier):
    def verify(self, bearer_token: str) -> str:
        if bearer_token != "worker-token":
            raise AssertionError("unexpected token")
        return "worker@example.iam.gserviceaccount.com"


class FakeAuthorizer:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls = 0

    def authorize(self, lease: StageLease) -> bool:
        del lease
        self.calls += 1
        return self.allowed


class FakeExecutor:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    def execute(self, lease: StageLease) -> dict[str, object]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return {"stage_code": lease.stage_code, "attempt": lease.attempt}


def lease() -> StageLease:
    now = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)
    return StageLease(
        workflow_run_id="workflow-1",
        stage_run_id="stage-1",
        stage_code="FIRST_PROPOSAL",
        input_digest="a" * 64,
        lease_token="lease-secret",
        lease_expires_at=now + timedelta(seconds=45),
        attempt=1,
        head=HeadFence(
            workflow_generation=1,
            state_version=1,
            founder_snapshot_id="founder-1",
            area_snapshot_id="area-1",
            evidence_snapshot_id=None,
            policy_snapshot_id="policy-1",
            index_generation_id=None,
            seed_registry_id=None,
        ),
    )


def client(authorizer: FakeAuthorizer, executor: FakeExecutor) -> TestClient:
    return TestClient(
        create_app(
            stage_execution_service=StageExecutionService(authorizer, executor),
            internal_identity_verifier=FakeIdentity(),
        )
    )


def test_internal_stage_execute_requires_worker_identity_and_current_lease() -> None:
    authorizer = FakeAuthorizer()
    executor = FakeExecutor()
    with client(authorizer, executor) as test_client:
        unauthenticated = test_client.post(
            "/internal/v1/workflows/workflow-1/stages/stage-1:execute",
            json={"lease": lease().model_dump(mode="json")},
        )
        accepted = test_client.post(
            "/internal/v1/workflows/workflow-1/stages/stage-1:execute",
            headers={"Authorization": "Bearer worker-token"},
            json={"lease": lease().model_dump(mode="json")},
        )

    assert unauthenticated.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json() == {"result": {"stage_code": "FIRST_PROPOSAL", "attempt": 1}}
    assert authorizer.calls == 1
    assert executor.calls == 1


def test_path_mismatch_and_rejected_lease_never_execute() -> None:
    authorizer = FakeAuthorizer(allowed=False)
    executor = FakeExecutor()
    with client(authorizer, executor) as test_client:
        wrong_path = test_client.post(
            "/internal/v1/workflows/other-workflow/stages/stage-1:execute",
            headers={"Authorization": "Bearer worker-token"},
            json={"lease": lease().model_dump(mode="json")},
        )
        rejected = test_client.post(
            "/internal/v1/workflows/workflow-1/stages/stage-1:execute",
            headers={"Authorization": "Bearer worker-token"},
            json={"lease": lease().model_dump(mode="json")},
        )

    assert wrong_path.status_code == rejected.status_code == 409
    assert wrong_path.json() == rejected.json() == {"code": "STAGE_LEASE_REJECTED"}
    assert authorizer.calls == 1
    assert executor.calls == 0


def test_agent_runtime_subclass_is_reported_as_retryable_unavailable() -> None:
    authorizer = FakeAuthorizer()
    executor = FakeExecutor(error=AgentRuntimeError("RUNTIME_EXECUTION_FAILED"))

    with client(authorizer, executor) as test_client:
        response = test_client.post(
            "/internal/v1/workflows/workflow-1/stages/stage-1:execute",
            headers={"Authorization": "Bearer worker-token"},
            json={"lease": lease().model_dump(mode="json")},
        )

    assert response.status_code == 503
    assert response.json() == {"code": "EXTERNAL_EXECUTION_UNAVAILABLE"}
