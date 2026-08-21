import httpx
import pytest
from worker.agent_cleanup import AgentRuntimeSessionDeleter

from app.agents.runtime import AgentRuntimeError


class FixedTokenProvider:
    def token(self) -> str:
        return "access-token"


def payload() -> dict[str, object]:
    return {
        "runtime_resource": (
            "projects/project-1/locations/asia-northeast3/reasoningEngines/runtime-1"
        ),
        "user_id": "p-pseudonymous",
        "session_id": "session-1",
    }


def test_runtime_session_deleter_uses_fixed_resource_and_typed_delete_call() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"output": None})

    deleter = AgentRuntimeSessionDeleter(
        gcp_project_id="project-1",
        resource_id="runtime-1",
        access_tokens=FixedTokenProvider(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    deleter.delete(payload())

    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer access-token"
    assert requests[0].read().decode() == (
        '{"class_method":"async_delete_session","input":'
        '{"user_id":"p-pseudonymous","session_id":"session-1"}}'
    )


def test_runtime_session_deleter_rejects_cross_resource_and_wraps_transport() -> None:
    deleter = AgentRuntimeSessionDeleter(
        gcp_project_id="project-1",
        resource_id="runtime-1",
        access_tokens=FixedTokenProvider(),
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(503))
        ),
    )
    crossed = payload()
    crossed["runtime_resource"] = (
        "projects/other/locations/asia-northeast3/reasoningEngines/runtime-2"
    )

    with pytest.raises(ValueError, match="crossed"):
        deleter.delete(crossed)
    with pytest.raises(AgentRuntimeError) as error:
        deleter.delete(payload())
    assert error.value.runtime_code == "RUNTIME_SESSION_CLEANUP_FAILED"


def test_runtime_session_deleter_treats_missing_session_as_idempotent_success() -> None:
    deleter = AgentRuntimeSessionDeleter(
        gcp_project_id="project-1",
        resource_id="runtime-1",
        access_tokens=FixedTokenProvider(),
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(404))
        ),
    )

    deleter.delete(payload())
