import copy
import json
from datetime import UTC, datetime
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


def stream_response(*events: dict[str, Any]) -> httpx.Response:
    body = "".join(json.dumps({"output": event}) + "\n" for event in events)
    return httpx.Response(200, text=body, headers={"Content-Type": "application/json"})


def runtime_client(
    handler: httpx.MockTransport,
    cleanup: FakeCleanupSink,
    **kwargs: Any,
) -> AgentRuntimeHttpClient:
    return AgentRuntimeHttpClient(
        gcp_project_id="gcp-project",
        resource_id="runtime-1",
        user_hmac_secret="x" * 32,
        access_tokens=FakeTokens(),
        cleanup_sink=cleanup,
        client=httpx.Client(transport=handler),
        now=lambda: datetime(2026, 8, 21, 8, 59, tzinfo=UTC),
        **kwargs,
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
        return stream_response(event)

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


def test_runtime_reserves_cleanup_budget_and_does_not_cap_long_streams_at_30_seconds() -> None:
    task, result = evidence_fixture()
    observed: list[tuple[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body.get("class_method")
        timeout = request.extensions["timeout"]
        observed.append((str(method), float(timeout["read"])))
        if method == "async_create_session":
            return httpx.Response(200, json={"output": {"id": "session-1"}})
        if method == "async_delete_session":
            return httpx.Response(200, json={"output": None})
        event = {
            "author": "EVIDENCE_RESEARCHER",
            "content": {"parts": [{"text": json.dumps(result)}]},
        }
        return stream_response(event)

    runtime_client(httpx.MockTransport(handler), FakeCleanupSink()).invoke(task)

    assert observed == [
        ("async_create_session", 10.0),
        ("async_stream_query", 58.0),
        ("async_delete_session", 10.0),
    ]


@pytest.mark.parametrize(
    "stream_body",
    [
        'data: {"output":{"author":"EVIDENCE_RESEARCHER"}}\n\n',
        '{"author":"EVIDENCE_RESEARCHER"}\n',
        '{"output":{"author":"EVIDENCE_RESEARCHER"},"extra":true}\n',
        '{"output":"not-an-event"}\n',
    ],
)
def test_stream_wire_format_rejects_non_authoritative_envelopes(stream_body: str) -> None:
    task, _result = evidence_fixture()
    deleted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body.get("class_method")
        if method == "async_create_session":
            return httpx.Response(200, json={"output": {"id": "session-1"}})
        if method == "async_delete_session":
            deleted.append(body["input"]["session_id"])
            return httpx.Response(200, json={"output": None})
        return httpx.Response(
            200,
            text=stream_body,
            headers={"Content-Type": "application/json"},
        )

    with pytest.raises(AgentRuntimeError, match="RUNTIME_STREAM_PROTOCOL_INVALID"):
        runtime_client(httpx.MockTransport(handler), FakeCleanupSink()).invoke(task)
    assert deleted == ["session-1"]


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
        return stream_response(*events)

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
        return stream_response(event)

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


def test_retryable_transport_uses_new_invocation_and_session_then_succeeds() -> None:
    task, result = evidence_fixture()
    created = 0
    streamed_tasks: list[dict[str, Any]] = []
    sleeps: list[float] = []
    invocation_ids = iter(["inv-retry-2", "unused"])

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal created
        body = json.loads(request.content)
        method = body.get("class_method")
        if method == "async_create_session":
            created += 1
            if created == 1:
                return httpx.Response(503)
            return httpx.Response(200, json={"output": {"id": f"session-{created}"}})
        if method == "async_delete_session":
            return httpx.Response(200, json={"output": None})
        sent_task = json.loads(body["input"]["message"])
        streamed_tasks.append(sent_task)
        response_result = copy.deepcopy(result)
        response_result["invocation_id"] = sent_task["invocation_id"]
        event = {
            "author": task["agent_name"],
            "content": {"parts": [{"text": json.dumps(response_result)}]},
        }
        return stream_response(event)

    loaded = runtime_client(
        httpx.MockTransport(handler),
        FakeCleanupSink(),
        sleep=sleeps.append,
        new_invocation_id=lambda: next(invocation_ids),
    ).invoke(task)

    assert loaded["invocation_id"] == "inv-retry-2"
    assert created == 2
    assert streamed_tasks[0]["transport_attempt"] == 2
    assert streamed_tasks[0]["task_id"] == task["task_id"]
    assert streamed_tasks[0]["input_digest"] == task["input_digest"]
    assert len(sleeps) == 1
    assert 0.25 <= sleeps[0] <= 0.35


def test_stream_retry_cleans_each_known_session() -> None:
    task, result = evidence_fixture()
    sessions = 0
    streams = 0
    deleted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sessions, streams
        body = json.loads(request.content)
        method = body.get("class_method")
        if method == "async_create_session":
            sessions += 1
            return httpx.Response(200, json={"output": {"id": f"session-{sessions}"}})
        if method == "async_delete_session":
            deleted.append(body["input"]["session_id"])
            return httpx.Response(200, json={"output": None})
        streams += 1
        if streams == 1:
            return httpx.Response(503)
        sent_task = json.loads(body["input"]["message"])
        response_result = copy.deepcopy(result)
        response_result["invocation_id"] = sent_task["invocation_id"]
        event = {
            "author": task["agent_name"],
            "content": {"parts": [{"text": json.dumps(response_result)}]},
        }
        return stream_response(event)

    runtime_client(
        httpx.MockTransport(handler),
        FakeCleanupSink(),
        sleep=lambda _seconds: None,
        new_invocation_id=lambda: "inv-stream-retry",
    ).invoke(task)
    assert deleted == ["session-1", "session-2"]


def test_schema_invalid_result_is_repaired_once_in_a_new_session() -> None:
    task, result = evidence_fixture()
    sessions = 0
    sent_tasks: list[dict[str, Any]] = []
    invocation_ids = iter(["inv-repair-1"])

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sessions
        body = json.loads(request.content)
        method = body.get("class_method")
        if method == "async_create_session":
            sessions += 1
            return httpx.Response(200, json={"output": {"id": f"session-{sessions}"}})
        if method == "async_delete_session":
            return httpx.Response(200, json={"output": None})
        sent_task = json.loads(body["input"]["message"])
        sent_tasks.append(sent_task)
        if sent_task["repair_attempt"] == 0:
            response_text = "{not-json"
        else:
            response_result = copy.deepcopy(result)
            response_result["invocation_id"] = sent_task["invocation_id"]
            response_text = json.dumps(response_result)
        event = {
            "author": task["agent_name"],
            "content": {"parts": [{"text": response_text}]},
        }
        return stream_response(event)

    loaded = runtime_client(
        httpx.MockTransport(handler),
        FakeCleanupSink(),
        new_invocation_id=lambda: next(invocation_ids),
    ).invoke(task)

    assert loaded["invocation_id"] == "inv-repair-1"
    assert sessions == 2
    assert [sent["repair_attempt"] for sent in sent_tasks] == [0, 1]
    repair = sent_tasks[1]
    assert repair["repair_of_invocation_id"] == task["invocation_id"]
    assert repair["input_digest"] == task["input_digest"]
    assert repair["repair_context"]["previous_response_text"] == "{not-json"
    assert repair["repair_context"]["previous_response_digest"].startswith("sha256:")
    assert repair["repair_context"]["validator_errors"] == [
        {
            "code": "JSON_PARSE_FAILED",
            "json_pointer": "",
            "message": "Response is not one valid JSON object",
        }
    ]


def test_second_schema_failure_stops_without_third_generation() -> None:
    task, _result = evidence_fixture()
    sessions = 0
    invocation_ids = iter(["inv-repair-1"])

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sessions
        body = json.loads(request.content)
        method = body.get("class_method")
        if method == "async_create_session":
            sessions += 1
            return httpx.Response(200, json={"output": {"id": f"session-{sessions}"}})
        if method == "async_delete_session":
            return httpx.Response(200, json={"output": None})
        event = {
            "author": task["agent_name"],
            "content": {"parts": [{"text": "{}"}]},
        }
        return stream_response(event)

    with pytest.raises(AgentRuntimeError, match="RUNTIME_RESULT_SCHEMA_INVALID"):
        runtime_client(
            httpx.MockTransport(handler),
            FakeCleanupSink(),
            new_invocation_id=lambda: next(invocation_ids),
        ).invoke(task)
    assert sessions == 2


@pytest.mark.parametrize(
    ("status_code", "runtime_code"),
    [
        (400, "RUNTIME_REQUEST_INVALID"),
        (401, "RUNTIME_UNAUTHENTICATED"),
        (403, "RUNTIME_FORBIDDEN"),
    ],
)
def test_terminal_http_failures_are_not_retried(status_code: int, runtime_code: str) -> None:
    task, _result = evidence_fixture()
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code)

    with pytest.raises(AgentRuntimeError, match=runtime_code):
        runtime_client(httpx.MockTransport(handler), FakeCleanupSink()).invoke(task)
    assert calls == 1


def test_safety_block_event_is_terminal_and_session_is_cleaned() -> None:
    task, _result = evidence_fixture()
    sessions = 0
    deletes = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sessions, deletes
        body = json.loads(request.content)
        method = body.get("class_method")
        if method == "async_create_session":
            sessions += 1
            return httpx.Response(200, json={"output": {"id": "session-1"}})
        if method == "async_delete_session":
            deletes += 1
            return httpx.Response(200, json={"output": None})
        event = {"errorCode": "SAFETY_BLOCKED"}
        return stream_response(event)

    with pytest.raises(AgentRuntimeError, match="SAFETY_BLOCKED"):
        runtime_client(httpx.MockTransport(handler), FakeCleanupSink()).invoke(task)
    assert sessions == 1
    assert deletes == 1


def test_expired_deadline_makes_zero_runtime_calls() -> None:
    task, _result = evidence_fixture()
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    with pytest.raises(AgentRuntimeError, match="RUNTIME_TIMED_OUT"):
        AgentRuntimeHttpClient(
            gcp_project_id="gcp-project",
            resource_id="runtime-1",
            user_hmac_secret="x" * 32,
            access_tokens=FakeTokens(),
            cleanup_sink=FakeCleanupSink(),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            now=lambda: datetime(2026, 8, 21, 9, 0, 1, tzinfo=UTC),
        ).invoke(task)
    assert calls == 0


def test_retry_after_is_used_only_within_two_seconds() -> None:
    task, result = evidence_fixture()
    create_calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_calls
        body = json.loads(request.content)
        method = body.get("class_method")
        if method == "async_create_session":
            create_calls += 1
            if create_calls == 1:
                return httpx.Response(429, headers={"Retry-After": "1.5"})
            return httpx.Response(200, json={"output": {"id": "session-2"}})
        if method == "async_delete_session":
            return httpx.Response(200, json={"output": None})
        sent_task = json.loads(body["input"]["message"])
        response_result = copy.deepcopy(result)
        response_result["invocation_id"] = sent_task["invocation_id"]
        event = {
            "author": task["agent_name"],
            "content": {"parts": [{"text": json.dumps(response_result)}]},
        }
        return stream_response(event)

    runtime_client(
        httpx.MockTransport(handler),
        FakeCleanupSink(),
        sleep=sleeps.append,
        new_invocation_id=lambda: "inv-retry-after",
    ).invoke(task)
    assert sleeps == [1.5]


def test_transport_retry_then_repair_references_invalid_retry_invocation() -> None:
    task, result = evidence_fixture()
    create_calls = 0
    sent_tasks: list[dict[str, Any]] = []
    invocation_ids = iter(["inv-transport-2", "inv-repair-after-retry"])

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_calls
        body = json.loads(request.content)
        method = body.get("class_method")
        if method == "async_create_session":
            create_calls += 1
            if create_calls == 1:
                return httpx.Response(503)
            return httpx.Response(200, json={"output": {"id": f"session-{create_calls}"}})
        if method == "async_delete_session":
            return httpx.Response(200, json={"output": None})
        sent_task = json.loads(body["input"]["message"])
        sent_tasks.append(sent_task)
        if sent_task["repair_attempt"] == 0:
            response_text = "invalid"
        else:
            response_result = copy.deepcopy(result)
            response_result["invocation_id"] = sent_task["invocation_id"]
            response_text = json.dumps(response_result)
        event = {
            "author": task["agent_name"],
            "content": {"parts": [{"text": response_text}]},
        }
        return stream_response(event)

    runtime_client(
        httpx.MockTransport(handler),
        FakeCleanupSink(),
        sleep=lambda _seconds: None,
        new_invocation_id=lambda: next(invocation_ids),
    ).invoke(task)
    assert sent_tasks[1]["repair_of_invocation_id"] == "inv-transport-2"


def test_retry_is_stopped_when_less_than_two_seconds_remain() -> None:
    task, _result = evidence_fixture()
    task["deadline_at"] = "2026-08-21T09:00:00Z"
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    client = AgentRuntimeHttpClient(
        gcp_project_id="gcp-project",
        resource_id="runtime-1",
        user_hmac_secret="x" * 32,
        access_tokens=FakeTokens(),
        cleanup_sink=FakeCleanupSink(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: datetime(2026, 8, 21, 8, 59, 59, tzinfo=UTC),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(AgentRuntimeError, match="RUNTIME_QUERY_TRANSPORT_FAILED"):
        client.invoke(task)
    assert calls == 1
