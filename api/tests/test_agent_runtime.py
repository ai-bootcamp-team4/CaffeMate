import asyncio
import copy
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.agents.runtime import (
    AgentRuntimeError,
    AgentRuntimeHttpClient,
    verify_agent_runtime_iam,
)
from app.agents.task_factory import compute_agent_input_digest
from app.cli import _agent_runtime_probe_task
from app.contracts.schema_registry import ContractRegistry


class FakeTokens:
    def token(self) -> str:
        return "access-token"


class FakeCleanupSink:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def enqueue_session_delete(self, **kwargs: str) -> None:
        self.calls.append(kwargs)


def test_runtime_iam_verifier_requires_query_and_rejects_mutation_permissions() -> None:
    requested: list[str] = []

    def allowed_handler(request: httpx.Request) -> httpx.Response:
        requested.extend(json.loads(request.content)["permissions"])
        return httpx.Response(
            200,
            json={"permissions": ["aiplatform.reasoningEngines.query"]},
        )

    report = verify_agent_runtime_iam(
        gcp_project_id="gcp-project",
        resource_id="runtime-1",
        access_tokens=FakeTokens(),
        transport=httpx.MockTransport(allowed_handler),
    )

    assert report["granted_permissions"] == ["aiplatform.reasoningEngines.query"]
    assert set(requested) == {
        "aiplatform.reasoningEngines.query",
        "aiplatform.reasoningEngines.update",
        "aiplatform.reasoningEngines.delete",
    }

    for granted, code in (
        ([], "RUNTIME_QUERY_PERMISSION_MISSING"),
        (
            [
                "aiplatform.reasoningEngines.query",
                "aiplatform.reasoningEngines.update",
            ],
            "RUNTIME_MUTATION_PERMISSION_PRESENT",
        ),
    ):
        with pytest.raises(AgentRuntimeError, match=code):
            verify_agent_runtime_iam(
                gcp_project_id="gcp-project",
                resource_id="runtime-1",
                access_tokens=FakeTokens(),
                transport=httpx.MockTransport(
                    lambda _request, granted=granted: httpx.Response(
                        200, json={"permissions": granted}
                    )
                ),
            )


def test_operational_probe_is_a_fresh_valid_evidence_plan_task() -> None:
    task = _agent_runtime_probe_task()

    ContractRegistry().validate_agent_task(task)
    assert task["task_type"] == "EVIDENCE_PLAN"
    assert task["agent_name"] == "EVIDENCE_RESEARCHER"
    assert task["venture_project_id"].startswith("runtime-preflight-")
    assert task["input_digest"] == compute_agent_input_digest(task)
    assert datetime.fromisoformat(task["deadline_at"].replace("Z", "+00:00")) > datetime.now(UTC)


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
    **kwargs: Any,
) -> AgentRuntimeHttpClient:
    return AgentRuntimeHttpClient(
        gcp_project_id="gcp-project",
        resource_id="runtime-1",
        user_hmac_secret="x" * 32,
        access_tokens=FakeTokens(),
        cleanup_sink=cleanup,
        transport=handler,
        now=lambda: datetime(2026, 8, 21, 8, 59, tzinfo=UTC),
        **kwargs,
    )


def expected_session_id(invocation_id: str) -> str:
    return f"caffemate-{hashlib.sha256(invocation_id.encode()).hexdigest()[:48]}"


def test_runtime_creates_streams_validates_and_deletes_one_managed_session() -> None:
    task, result = evidence_fixture()
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append({"url": str(request.url), "body": body, "headers": request.headers})
        if body.get("class_method") == "async_create_session":
            return httpx.Response(200, json={"output": {"id": body["input"]["session_id"]}})
        if body.get("class_method") == "async_delete_session":
            return httpx.Response(200, json={"output": None})
        event = {
            "author": "EVIDENCE_RESEARCHER",
            "partial": False,
            "content": {"parts": [{"text": json.dumps(result)}]},
        }
        return httpx.Response(200, text=f'{json.dumps({"output": event})}\n')

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
    assert requests[0]["body"]["input"]["session_id"] == expected_session_id(
        task["invocation_id"]
    )
    assert stream_input["session_id"] == expected_session_id(task["invocation_id"])
    assert json.loads(stream_input["message"]) == task
    assert all(request["headers"]["Authorization"] == "Bearer access-token" for request in requests)
    assert cleanup.calls == []


def test_sixty_second_task_preserves_stream_budget_and_reserves_cleanup() -> None:
    task, result = evidence_fixture()
    task["deadline_at"] = "2026-08-21T09:00:00Z"
    observed_timeouts: dict[str, float] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body.get("class_method")
        timeout = request.extensions["timeout"]
        observed_timeouts[str(method)] = float(timeout["read"])
        if method == "async_create_session":
            return httpx.Response(200, json={"output": {"id": body["input"]["session_id"]}})
        if method == "async_delete_session":
            return httpx.Response(200, json={"output": None})
        event = {
            "author": "EVIDENCE_RESEARCHER",
            "partial": False,
            "content": {"parts": [{"text": json.dumps(result)}]},
        }
        return httpx.Response(200, text=f'{json.dumps({"output": event})}\n')

    runtime_client(httpx.MockTransport(handler), FakeCleanupSink()).invoke(task)

    assert observed_timeouts == {
        "async_create_session": 10.0,
        "async_stream_query": 58.0,
        "async_delete_session": 10.0,
    }


def test_stream_discards_final_when_wall_clock_reaches_cleanup_reserve() -> None:
    task, result = evidence_fixture()
    task["deadline_at"] = "2026-08-21T09:00:00Z"
    cleanup = FakeCleanupSink()
    delete_timeouts: list[float] = []
    observed_times = iter(
        [
            datetime(2026, 8, 21, 8, 59, 0, tzinfo=UTC),
            datetime(2026, 8, 21, 8, 59, 0, tzinfo=UTC),
            datetime(2026, 8, 21, 8, 59, 0, tzinfo=UTC),
            datetime(2026, 8, 21, 8, 59, 59, tzinfo=UTC),
            datetime(2026, 8, 21, 8, 59, 59, tzinfo=UTC),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body.get("class_method")
        if method == "async_create_session":
            return httpx.Response(200, json={"output": {"id": body["input"]["session_id"]}})
        if method == "async_delete_session":
            delete_timeouts.append(float(request.extensions["timeout"]["read"]))
            return httpx.Response(200, json={"output": None})
        event = {
            "author": "EVIDENCE_RESEARCHER",
            "content": {"parts": [{"text": json.dumps(result)}]},
        }
        return httpx.Response(200, text=f'{json.dumps({"output": event})}\n')

    client = AgentRuntimeHttpClient(
        gcp_project_id="gcp-project",
        resource_id="runtime-1",
        user_hmac_secret="x" * 32,
        access_tokens=FakeTokens(),
        cleanup_sink=cleanup,
        transport=httpx.MockTransport(handler),
        now=lambda: next(observed_times),
    )
    with pytest.raises(AgentRuntimeError, match="RUNTIME_TIMED_OUT"):
        client.invoke(task)

    assert delete_timeouts == [1.0]
    assert cleanup.calls == []


def test_expired_cleanup_budget_enqueues_session_delete() -> None:
    task, result = evidence_fixture()
    task["deadline_at"] = "2026-08-21T09:00:00Z"
    cleanup = FakeCleanupSink()
    delete_calls = 0
    observed_times = iter(
        [
            datetime(2026, 8, 21, 8, 59, 0, tzinfo=UTC),
            datetime(2026, 8, 21, 8, 59, 0, tzinfo=UTC),
            datetime(2026, 8, 21, 8, 59, 0, tzinfo=UTC),
            datetime(2026, 8, 21, 8, 59, 59, tzinfo=UTC),
            datetime(2026, 8, 21, 9, 0, 0, tzinfo=UTC),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal delete_calls
        body = json.loads(request.content)
        method = body.get("class_method")
        if method == "async_create_session":
            return httpx.Response(200, json={"output": {"id": body["input"]["session_id"]}})
        if method == "async_delete_session":
            delete_calls += 1
            return httpx.Response(200, json={"output": None})
        event = {
            "author": "EVIDENCE_RESEARCHER",
            "content": {"parts": [{"text": json.dumps(result)}]},
        }
        return httpx.Response(200, text=f'{json.dumps({"output": event})}\n')

    client = AgentRuntimeHttpClient(
        gcp_project_id="gcp-project",
        resource_id="runtime-1",
        user_hmac_secret="x" * 32,
        access_tokens=FakeTokens(),
        cleanup_sink=cleanup,
        transport=httpx.MockTransport(handler),
        now=lambda: next(observed_times),
    )
    with pytest.raises(AgentRuntimeError, match="RUNTIME_TIMED_OUT"):
        client.invoke(task)

    assert delete_calls == 0
    assert len(cleanup.calls) == 1
    assert cleanup.calls[0]["runtime_resource"].endswith("reasoningEngines/runtime-1")
    assert cleanup.calls[0]["user_id"].startswith("p-")
    assert cleanup.calls[0]["session_id"] == expected_session_id(task["invocation_id"])


def test_stream_transport_is_cancelled_at_absolute_wall_clock_deadline() -> None:
    task, result = evidence_fixture()
    task["deadline_at"] = "2026-08-21T09:00:00Z"
    cleanup = FakeCleanupSink()

    stream_cancelled = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal stream_cancelled
        body = json.loads(request.content)
        method = body.get("class_method")
        if method == "async_create_session":
            return httpx.Response(200, json={"output": {"id": body["input"]["session_id"]}})
        if method == "async_delete_session":
            pytest.fail("hard stream timeout must use durable cleanup instead")
        try:
            await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            stream_cancelled = True
            raise
        event = {
            "author": "EVIDENCE_RESEARCHER",
            "content": {"parts": [{"text": json.dumps(result)}]},
        }
        return httpx.Response(200, content=json.dumps({"output": event}))

    client = AgentRuntimeHttpClient(
        gcp_project_id="gcp-project",
        resource_id="runtime-1",
        user_hmac_secret="x" * 32,
        access_tokens=FakeTokens(),
        cleanup_sink=cleanup,
        transport=httpx.MockTransport(handler),
        now=lambda: datetime(2026, 8, 21, 8, 59, 57, 700000, tzinfo=UTC),
    )
    started = time.monotonic()
    with pytest.raises(AgentRuntimeError, match="RUNTIME_TIMED_OUT"):
        client.invoke(task)

    assert time.monotonic() - started < 0.8
    assert len(cleanup.calls) == 1
    assert stream_cancelled is True
    assert cleanup.calls[0]["session_id"] == expected_session_id(task["invocation_id"])


def test_create_transport_is_cancelled_and_known_session_is_enqueued() -> None:
    task, _result = evidence_fixture()
    task["deadline_at"] = "2026-08-21T09:00:00Z"
    cleanup = FakeCleanupSink()
    create_cancelled = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal create_cancelled
        body = json.loads(request.content)
        if body.get("class_method") != "async_create_session":
            pytest.fail("timed-out create must not continue to stream or inline delete")
        try:
            await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            create_cancelled = True
            raise
        return httpx.Response(200, json={"output": {"id": body["input"]["session_id"]}})

    client = AgentRuntimeHttpClient(
        gcp_project_id="gcp-project",
        resource_id="runtime-1",
        user_hmac_secret="x" * 32,
        access_tokens=FakeTokens(),
        cleanup_sink=cleanup,
        transport=httpx.MockTransport(handler),
        now=lambda: datetime(2026, 8, 21, 8, 59, 57, 700000, tzinfo=UTC),
    )

    with pytest.raises(AgentRuntimeError, match="RUNTIME_TIMED_OUT"):
        client.invoke(task)

    assert create_cancelled is True
    assert cleanup.calls[0]["session_id"] == expected_session_id(task["invocation_id"])


def test_wrong_author_and_duplicate_final_events_are_protocol_errors_but_still_cleanup() -> None:
    task, result = evidence_fixture()
    deleted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("class_method") == "async_create_session":
            return httpx.Response(200, json={"output": {"id": body["input"]["session_id"]}})
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
    assert deleted == [expected_session_id(task["invocation_id"])]


@pytest.mark.parametrize(
    "trailing_record",
    [
        {"error": "stream failed after final"},
        ["not", "an", "object"],
        "not-an-object",
        {"output": "not-an-event"},
        {"unexpected": True},
    ],
)
def test_invalid_record_after_valid_final_fails_closed(trailing_record: object) -> None:
    task, result = evidence_fixture()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body.get("class_method")
        if method == "async_create_session":
            return httpx.Response(200, json={"output": {"id": body["input"]["session_id"]}})
        if method == "async_delete_session":
            return httpx.Response(200, json={"output": None})
        event = {
            "author": "EVIDENCE_RESEARCHER",
            "content": {"parts": [{"text": json.dumps(result)}]},
        }
        return httpx.Response(
            200,
            text=(
                f'{json.dumps({"output": event})}\n'
                f"{json.dumps(trailing_record)}\n"
            ),
        )

    with pytest.raises(AgentRuntimeError, match="RUNTIME_STREAM_PROTOCOL_INVALID"):
        runtime_client(httpx.MockTransport(handler), FakeCleanupSink()).invoke(task)


def test_non_text_final_before_valid_final_fails_closed() -> None:
    task, result = evidence_fixture()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body.get("class_method")
        if method == "async_create_session":
            return httpx.Response(200, json={"output": {"id": body["input"]["session_id"]}})
        if method == "async_delete_session":
            return httpx.Response(200, json={"output": None})
        events = [
            {
                "author": "EVIDENCE_RESEARCHER",
                "content": {"parts": [{"functionCall": {"name": "forbidden"}}]},
            },
            {
                "author": "EVIDENCE_RESEARCHER",
                "content": {"parts": [{"text": json.dumps(result)}]},
            },
        ]
        return httpx.Response(
            200,
            text="".join(f'{json.dumps({"output": event})}\n' for event in events),
        )

    with pytest.raises(AgentRuntimeError, match="RUNTIME_PROTOCOL_INVALID"):
        runtime_client(httpx.MockTransport(handler), FakeCleanupSink()).invoke(task)


def test_wrong_author_partial_event_fails_closed() -> None:
    task, result = evidence_fixture()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body.get("class_method")
        if method == "async_create_session":
            return httpx.Response(200, json={"output": {"id": body["input"]["session_id"]}})
        if method == "async_delete_session":
            return httpx.Response(200, json={"output": None})
        events = [
            {
                "author": "WRONG_AGENT",
                "partial": True,
                "content": {"parts": [{"text": "still running"}]},
            },
            {
                "author": "EVIDENCE_RESEARCHER",
                "content": {"parts": [{"text": json.dumps(result)}]},
            },
        ]
        return httpx.Response(
            200,
            text="".join(f'{json.dumps({"output": event})}\n' for event in events),
        )

    with pytest.raises(AgentRuntimeError, match="RUNTIME_PROTOCOL_INVALID"):
        runtime_client(httpx.MockTransport(handler), FakeCleanupSink()).invoke(task)


def test_delete_failure_is_durably_enqueued_without_losing_valid_result() -> None:
    task, result = evidence_fixture()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("class_method") == "async_create_session":
            return httpx.Response(200, json={"output": {"id": body["input"]["session_id"]}})
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
            "session_id": expected_session_id(task["invocation_id"]),
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
            return httpx.Response(200, json={"output": {"id": body["input"]["session_id"]}})
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
        return httpx.Response(200, text=f"data: {json.dumps(event)}\n\n")

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
            return httpx.Response(200, json={"output": {"id": body["input"]["session_id"]}})
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
        return httpx.Response(200, text=f"data: {json.dumps(event)}\n\n")

    runtime_client(
        httpx.MockTransport(handler),
        FakeCleanupSink(),
        sleep=lambda _seconds: None,
        new_invocation_id=lambda: "inv-stream-retry",
    ).invoke(task)
    assert deleted == [
        expected_session_id(task["invocation_id"]),
        expected_session_id("inv-stream-retry"),
    ]


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
            return httpx.Response(200, json={"output": {"id": body["input"]["session_id"]}})
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
        return httpx.Response(200, text=f"data: {json.dumps(event)}\n\n")

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
            return httpx.Response(200, json={"output": {"id": body["input"]["session_id"]}})
        if method == "async_delete_session":
            return httpx.Response(200, json={"output": None})
        event = {
            "author": task["agent_name"],
            "content": {"parts": [{"text": "{}"}]},
        }
        return httpx.Response(200, text=f"data: {json.dumps(event)}\n\n")

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
            return httpx.Response(200, json={"output": {"id": body["input"]["session_id"]}})
        if method == "async_delete_session":
            deletes += 1
            return httpx.Response(200, json={"output": None})
        event = {"errorCode": "SAFETY_BLOCKED"}
        return httpx.Response(200, text=f"data: {json.dumps(event)}\n\n")

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
            transport=httpx.MockTransport(handler),
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
            return httpx.Response(200, json={"output": {"id": body["input"]["session_id"]}})
        if method == "async_delete_session":
            return httpx.Response(200, json={"output": None})
        sent_task = json.loads(body["input"]["message"])
        response_result = copy.deepcopy(result)
        response_result["invocation_id"] = sent_task["invocation_id"]
        event = {
            "author": task["agent_name"],
            "content": {"parts": [{"text": json.dumps(response_result)}]},
        }
        return httpx.Response(200, text=f"data: {json.dumps(event)}\n\n")

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
            return httpx.Response(
                200,
                json={"output": {"id": body["input"]["session_id"]}},
            )
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
        return httpx.Response(200, text=f"data: {json.dumps(event)}\n\n")

    runtime_client(
        httpx.MockTransport(handler),
        FakeCleanupSink(),
        sleep=lambda _seconds: None,
        new_invocation_id=lambda: next(invocation_ids),
    ).invoke(task)
    assert sent_tasks[1]["repair_of_invocation_id"] == "inv-transport-2"


def test_session_is_not_started_when_cleanup_reserve_is_unavailable() -> None:
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
        transport=httpx.MockTransport(handler),
        now=lambda: datetime(2026, 8, 21, 8, 59, 59, tzinfo=UTC),
        sleep=lambda _seconds: None,
    )
    with pytest.raises(AgentRuntimeError, match="RUNTIME_TIMED_OUT"):
        client.invoke(task)
    assert calls == 0
