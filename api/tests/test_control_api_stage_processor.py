from datetime import UTC, datetime

import httpx
import pytest
from worker.control_api import ControlApiStageProcessor

from tests.test_worker_runtime import stage_lease


class FakeTokenProvider:
    def __init__(self) -> None:
        self.audiences: list[str] = []

    def token_for(self, audience: str) -> str:
        self.audiences.append(audience)
        return "worker-token"


def test_worker_calls_only_internal_stage_path_with_service_identity() -> None:
    token_provider = FakeTokenProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/v1/workflows/workflow-1/stages/stage-1:execute"
        assert request.headers["Authorization"] == "Bearer worker-token"
        return httpx.Response(200, json={"result": {"status": "COMPLETE"}})

    processor = ControlApiStageProcessor(
        base_url="https://control-api.example/",
        audience="https://control-api.example",
        token_provider=token_provider,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: datetime(2026, 8, 21, tzinfo=UTC),
    )

    assert processor.process(stage_lease()) == {"status": "COMPLETE"}
    assert token_provider.audiences == ["https://control-api.example"]


def test_worker_refuses_to_call_after_lease_budget_is_exhausted() -> None:
    token_provider = FakeTokenProvider()
    processor = ControlApiStageProcessor(
        base_url="https://control-api.example",
        audience="https://control-api.example",
        token_provider=token_provider,
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200))),
        now=lambda: datetime(2026, 8, 21, 0, 0, 44, tzinfo=UTC),
    )

    with pytest.raises(TimeoutError, match="insufficient"):
        processor.process(stage_lease())
    assert token_provider.audiences == []
