"""사용자 제안은 API에서 동기 실행하므로 Worker가 제안 stage를 다시 발행하지 않게 고정한다."""

from fastapi.testclient import TestClient
from worker.main import create_worker_app


def test_worker_exposes_operations_but_no_workflow_stage_dispatcher() -> None:
    app = create_worker_app()
    client = TestClient(app)

    assert client.get("/health").json() == {"status": "ok"}
    assert "/internal/v1/agent-sessions:cleanup" in app.openapi()["paths"]
    assert "/internal/v1/dead-letters" in app.openapi()["paths"]
    assert "/internal/v1/outbox:publish" not in app.openapi()["paths"]

