import copy
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.agents.runtime import AgentRuntimeError, AgentRuntimeHttpClient


class FakeTokens:
    def token(self) -> str:
        return "access-token"


class FakeCleanupSink:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def enqueue_session_delete(self, **kwargs: str) -> None:
        self.calls.append(kwargs)


def evidence_fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(__file__).resolve().parents[2]
    matrix = json.loads(
        (root / "agents" / "fixtures" / "task-matrix.json").read_text(encoding="utf-8")
    )
    fixture = next(case for case in matrix["cases"] if case["id"] == "evidence_plan-complete")
    return copy.deepcopy(fixture["task"]), copy.deepcopy(fixture["result"])


def runtime_client(
    handler: httpx.MockTransport,
    cleanup: FakeCleanupSink,
) -> AgentRuntimeHttpClient:
    return AgentRuntimeHttpClient(
        gcp_project_id="gcp-project",
        resource_id="runtime-1",
        user_hmac_secret="x" * 32,
        access_tokens=FakeTokens(),
        cleanup_sink=cleanup,
        client=httpx.Client(transport=handler),
    )


def test_runtime_creates_streams_validates_and_deletes_one_managed_session() -> None:
    task, result = evidence_fixture()
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append({"url": str(request.url), "body": body, "headers": request.headers})
        if body.get("class_method") == "async_create_session":
            return httpx.Response(200, json={"output": {"id": "session-1"}})
        if body.get("class_method") == "async_delete_session":
            return httpx.Response(200, json={"output": None})
        event = {
            "author": "EVIDENCE_RESEARCHER",
            "partial": False,
            "content": {"parts": [{"text": json.dumps(result)}]},
        }
        return httpx.Response(200, text=f"data: {json.dumps(event)}\n\n")

    cleanup = FakeCleanupSink()
    loaded = runtime_client(httpx.MockTransport(handler), cleanup).invoke(task)

    assert loaded == result
    assert [request["body"].get("class_method") for request in requests] == [
        "async_create_session",
        "async_stream_query",
        "async_delete_session",
    ]
    create_user_id = requests[0]["body"]["input"]["user_id"]
    assert create_user_id.startswith("p-")
    assert task["venture_project_id"] not in create_user_id
    stream_input = requests[1]["body"]["input"]
    assert stream_input["session_id"] == "session-1"
    assert json.loads(stream_input["message"]) == task
    assert all(request["headers"]["Authorization"] == "Bearer access-token" for request in requests)
    assert cleanup.calls == []


def test_wrong_author_and_duplicate_final_events_are_protocol_errors_but_still_cleanup() -> None:
    task, result = evidence_fixture()
    deleted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("class_method") == "async_create_session":
            return httpx.Response(200, json={"output": {"id": "session-1"}})
        if body.get("class_method") == "async_delete_session":
            deleted.append(body["input"]["session_id"])
            return httpx.Response(200, json={"output": None})
        events = [
            {
                "author": "OTHER_AGENT",
                "content": {"parts": [{"text": json.dumps(result)}]},
            },
            {
                "author": "EVIDENCE_RESEARCHER",
                "content": {"parts": [{"text": json.dumps(result)}]},
            },
            {
                "author": "EVIDENCE_RESEARCHER",
                "content": {"parts": [{"text": json.dumps(result)}]},
            },
        ]
        body_text = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        return httpx.Response(200, text=body_text)

    with pytest.raises(AgentRuntimeError, match="RUNTIME_PROTOCOL_INVALID"):
        runtime_client(httpx.MockTransport(handler), FakeCleanupSink()).invoke(task)
    assert deleted == ["session-1"]


def test_delete_failure_is_durably_enqueued_without_losing_valid_result() -> None:
    task, result = evidence_fixture()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("class_method") == "async_create_session":
            return httpx.Response(200, json={"output": {"id": "session-1"}})
        if body.get("class_method") == "async_delete_session":
            return httpx.Response(503)
        event = {
            "author": "EVIDENCE_RESEARCHER",
            "content": {"parts": [{"text": json.dumps(result)}]},
        }
        return httpx.Response(200, text=f"data: {json.dumps(event)}\n\n")

    cleanup = FakeCleanupSink()
    loaded = runtime_client(httpx.MockTransport(handler), cleanup).invoke(task)

    assert loaded == result
    assert cleanup.calls == [
        {
            "runtime_resource": (
                "projects/gcp-project/locations/asia-northeast3/"
                "reasoningEngines/runtime-1"
            ),
            "user_id": cleanup.calls[0]["user_id"],
            "session_id": "session-1",
        }
    ]


def test_task_digest_mismatch_makes_zero_runtime_calls() -> None:
    task, _result = evidence_fixture()
    task["payload"]["claims"][0]["required_freshness"] = "P1D"
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    with pytest.raises(AgentRuntimeError, match="RUNTIME_TASK_DIGEST_MISMATCH"):
        runtime_client(httpx.MockTransport(handler), FakeCleanupSink()).invoke(task)
    assert calls == 0
