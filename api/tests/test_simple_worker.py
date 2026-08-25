"""Worker는 장시간 제안 실행의 durable delivery와 운영 복구 endpoint를 제공한다."""

from fastapi.testclient import TestClient
from worker.main import create_worker_app


def test_worker_exposes_workflow_delivery_and_operations() -> None:
    app = create_worker_app()
    client = TestClient(app)

    assert client.get("/health").json() == {"status": "ok"}
    assert "/internal/v1/agent-sessions:cleanup" in app.openapi()["paths"]
    assert "/internal/v1/dead-letters" in app.openapi()["paths"]
    assert "/internal/v1/pubsub/workflow-stages" in app.openapi()["paths"]
    assert "/internal/v1/outbox:publish" in app.openapi()["paths"]

