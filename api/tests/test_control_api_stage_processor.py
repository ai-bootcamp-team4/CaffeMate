from datetime import UTC, datetime

import httpx
import pytest
from worker.control_api import ControlApiStageProcessor
from worker.errors import StageExecutionError

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


def test_terminal_stage_error_contract_is_preserved() -> None:
    processor = ControlApiStageProcessor(
        base_url="https://control-api.example",
        audience="https://control-api.example",
        token_provider=FakeTokenProvider(),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    503,
                    json={"code": "RUNTIME_FORBIDDEN", "retryable": False},
                )
            )
        ),
        now=lambda: datetime(2026, 8, 21, tzinfo=UTC),
    )

    with pytest.raises(StageExecutionError) as captured:
        processor.process(stage_lease())

    assert captured.value.code == "RUNTIME_FORBIDDEN"
    assert captured.value.retryable is False


def test_untyped_control_api_503_remains_retryable() -> None:
    processor = ControlApiStageProcessor(
        base_url="https://control-api.example",
        audience="https://control-api.example",
        token_provider=FakeTokenProvider(),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(503, text="upstream unavailable")
            )
        ),
        now=lambda: datetime(2026, 8, 21, tzinfo=UTC),
    )

    with pytest.raises(StageExecutionError) as captured:
        processor.process(stage_lease())

    assert captured.value.code == "CONTROL_API_TRANSPORT_FAILED"
    assert captured.value.retryable is True


def test_control_api_timeout_allows_sixty_second_agent_deadline() -> None:
    observed_timeout: dict[str, float] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed_timeout.update(request.extensions["timeout"])
        return httpx.Response(200, json={"result": {"status": "COMPLETE"}})

    processor = ControlApiStageProcessor(
        base_url="https://control-api.example",
        audience="https://control-api.example",
        token_provider=FakeTokenProvider(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: datetime(2026, 8, 21, tzinfo=UTC),
    )
    extended_lease = stage_lease().model_copy(
        update={"lease_expires_at": datetime(2026, 8, 21, 0, 1, 30, tzinfo=UTC)}
    )

    processor.process(extended_lease)

    assert observed_timeout["read"] == 70.0


def test_invalid_success_response_is_terminal() -> None:
    processor = ControlApiStageProcessor(
        base_url="https://control-api.example",
        audience="https://control-api.example",
        token_provider=FakeTokenProvider(),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"unexpected": True})
            )
        ),
        now=lambda: datetime(2026, 8, 21, tzinfo=UTC),
    )

    with pytest.raises(StageExecutionError) as captured:
        processor.process(stage_lease())

    assert captured.value.code == "CONTROL_API_RESPONSE_INVALID"
    assert captured.value.retryable is False
