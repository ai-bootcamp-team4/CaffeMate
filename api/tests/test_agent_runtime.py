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
from app.security.content_protection import ContentBoundary, ContentInspection


class FakeTokens:
    def token(self) -> str:
        return "access-token"


class FakeCleanupSink:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def enqueue_session_delete(self, **kwargs: str) -> None:
        self.calls.append(kwargs)


class RecordingContentProtection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ContentBoundary]] = []

    def inspect(self, content: str, boundary: ContentBoundary) -> ContentInspection:
        self.calls.append((content, boundary))
        return ContentInspection(
            boundary=boundary,
            invocation_result="SUCCESS",
            match_state="NO_MATCH_FOUND",
            finding_count=0,
            info_types=(),
            findings_truncated=False,
        )


class DeferredErrorStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'{"error":"RUNTIME_AGENT_OUTPUT_INVALID"}'


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


def test_operational_probe_can_build_a_live_shaped_intent_task() -> None:
    task = _agent_runtime_probe_task("intent_delta-complete")

    ContractRegistry().validate_agent_task(task)
    assert task["task_type"] == "INTENT_DELTA"
    assert task["agent_name"] == "INTENT_INTERPRETER"
    assert task["prompt_version"] == "intent-interpreter.v2"
    assert task["payload"]["allowed_field_paths"] == [
        "/founder/borrowing_intent"
    ]
    assert task["input_digest"] == compute_agent_input_digest(task)
    remaining = datetime.fromisoformat(
        task["deadline_at"].replace("Z", "+00:00")
    ) - datetime.now(UTC)
    assert 0 < remaining.total_seconds() <= 30


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


def test_runtime_uses_one_ephemeral_stream_and_validates_the_final_result() -> None:
    task, result = evidence_fixture()
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append({"url": str(request.url), "body": body, "headers": request.headers})
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
        "async_ephemeral_stream_query",
    ]
    stream_input = requests[0]["body"]["input"]
    assert stream_input["user_id"].startswith("p-")
    assert task["venture_project_id"] not in stream_input["user_id"]
    assert stream_input["session_id"] == expected_session_id(task["invocation_id"])
    assert json.loads(stream_input["message"]) == task
    assert requests[0]["headers"]["Authorization"] == "Bearer access-token"
    assert cleanup.calls == []


def test_runtime_inspects_typed_agent_payload_before_and_after_gemini() -> None:
    task, result = evidence_fixture()
    protection = RecordingContentProtection()

    def handler(_request: httpx.Request) -> httpx.Response:
        event = {
            "author": "EVIDENCE_RESEARCHER",
            "partial": False,
            "content": {"parts": [{"text": json.dumps(result)}]},
        }
        return httpx.Response(200, text=f'{json.dumps({"output": event})}\n')

    loaded = runtime_client(
        httpx.MockTransport(handler),
        FakeCleanupSink(),
        content_protection=protection,
    ).invoke(task)

    assert loaded == result
    assert [boundary for _content, boundary in protection.calls] == [
        ContentBoundary.AGENT_INPUT,
        ContentBoundary.AGENT_OUTPUT,
    ]
    assert json.loads(protection.calls[0][0]) == task["payload"]
    assert json.loads(protection.calls[1][0]) == result["payload"]


def test_sixty_second_task_preserves_stream_budget_and_reserves_cleanup() -> None:
    task, result = evidence_fixture()
    task["deadline_at"] = "2026-08-21T09:00:00Z"
    observed_timeouts: dict[str, float] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body.get("class_method")
        timeout = request.extensions["timeout"]
        observed_timeouts[str(method)] = float(timeout["read"])
        event = {
            "author": "EVIDENCE_RESEARCHER",
            "partial": False,
            "content": {"parts": [{"text": json.dumps(result)}]},
        }
        return httpx.Response(200, text=f'{json.dumps({"output": event})}\n')

    runtime_client(httpx.MockTransport(handler), FakeCleanupSink()).invoke(task)

    assert observed_timeouts == {
        "async_ephemeral_stream_query": 58.0,
    }


def test_stream_discards_final_when_wall_clock_reaches_cleanup_reserve() -> None:
    task, result = evidence_fixture()
    task["deadline_at"] = "2026-08-21T09:00:00Z"
    cleanup = FakeCleanupSink()
    observed_times = iter(
        [
            datetime(2026, 8, 21, 8, 59, 0, tzinfo=UTC),
            datetime(2026, 8, 21, 8, 59, 0, tzinfo=UTC),
            datetime(2026, 8, 21, 8, 59, 59, tzinfo=UTC),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body.get("class_method") == "async_ephemeral_stream_query"
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

    assert len(cleanup.calls) == 1
    assert cleanup.calls[0]["session_id"] == expected_session_id(task["invocation_id"])


def test_exhausted_transport_retries_enqueue_each_uncertain_ephemeral_session() -> None:
    task, _result = evidence_fixture()
    cleanup = FakeCleanupSink()
    invocation_ids = iter(["inv-retry-2", "inv-retry-3"])

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body.get("class_method") == "async_ephemeral_stream_query"
        return httpx.Response(503)

    with pytest.raises(AgentRuntimeError, match="RUNTIME_STREAM_TRANSPORT_FAILED"):
        runtime_client(
            httpx.MockTransport(handler),
            cleanup,
            sleep=lambda _seconds: None,
            new_invocation_id=lambda: next(invocation_ids),
        ).invoke(task)

    assert [call["session_id"] for call in cleanup.calls] == [
        expected_session_id(task["invocation_id"]),
        expected_session_id("inv-retry-2"),
        expected_session_id("inv-retry-3"),
    ]


def test_stream_transport_is_cancelled_at_absolute_wall_clock_deadline() -> None:
    task, result = evidence_fixture()
    task["deadline_at"] = "2026-08-21T09:00:00Z"
    cleanup = FakeCleanupSink()

    stream_cancelled = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal stream_cancelled
        body = json.loads(request.content)
        assert body.get("class_method") == "async_ephemeral_stream_query"
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


def test_wrong_author_and_duplicate_final_events_fail_after_runtime_cleanup() -> None:
    task, result = evidence_fixture()
    cleanup = FakeCleanupSink()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body.get("class_method") == "async_ephemeral_stream_query"
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
        runtime_client(httpx.MockTransport(handler), cleanup).invoke(task)
    assert cleanup.calls == []


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


def test_runtime_cleanup_failure_invalidates_result_and_enqueues_durable_cleanup() -> None:
    task, result = evidence_fixture()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body.get("class_method") == "async_ephemeral_stream_query"
        event = {
            "author": "EVIDENCE_RESEARCHER",
            "content": {"parts": [{"text": json.dumps(result)}]},
        }
        cleanup_error = {"error_code": "RUNTIME_SESSION_CLEANUP_FAILED"}
        return httpx.Response(
            200,
            text=(
                f"data: {json.dumps(event)}\n\n"
                f"data: {json.dumps(cleanup_error)}\n\n"
            ),
        )

    cleanup = FakeCleanupSink()
    with pytest.raises(AgentRuntimeError, match="RUNTIME_SESSION_CLEANUP_FAILED"):
        runtime_client(httpx.MockTransport(handler), cleanup).invoke(task)

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
    stream_calls = 0
    streamed_tasks: list[dict[str, Any]] = []
    sleeps: list[float] = []
    invocation_ids = iter(["inv-retry-2", "unused"])
    cleanup = FakeCleanupSink()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal stream_calls
        body = json.loads(request.content)
        assert body.get("class_method") == "async_ephemeral_stream_query"
        stream_calls += 1
        if stream_calls == 1:
            return httpx.Response(503)
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
        cleanup,
        sleep=sleeps.append,
        new_invocation_id=lambda: next(invocation_ids),
    ).invoke(task)

    assert loaded["invocation_id"] == "inv-retry-2"
    assert stream_calls == 2
    assert streamed_tasks[0]["transport_attempt"] == 2
    assert streamed_tasks[0]["task_id"] == task["task_id"]
    assert streamed_tasks[0]["input_digest"] == task["input_digest"]
    assert len(sleeps) == 1
    assert 0.25 <= sleeps[0] <= 0.35
    assert [call["session_id"] for call in cleanup.calls] == [
        expected_session_id(task["invocation_id"])
    ]


def test_terminal_agent_output_failure_is_not_retried() -> None:
    task, _result = evidence_fixture()
    stream_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal stream_calls
        body = json.loads(request.content)
        assert body.get("class_method") == "async_ephemeral_stream_query"
        stream_calls += 1
        return httpx.Response(
            422, json={"error": "VERTEX_MODEL_RESPONSE_INCOMPLETE"}
        )

    with pytest.raises(AgentRuntimeError, match="RUNTIME_AGENT_OUTPUT_INVALID"):
        runtime_client(httpx.MockTransport(handler), FakeCleanupSink()).invoke(task)

    assert stream_calls == 1


def test_stream_retry_enqueues_only_the_uncertain_failed_session() -> None:
    task, result = evidence_fixture()
    streams = 0
    cleanup = FakeCleanupSink()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal streams
        body = json.loads(request.content)
        assert body.get("class_method") == "async_ephemeral_stream_query"
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
        cleanup,
        sleep=lambda _seconds: None,
        new_invocation_id=lambda: "inv-stream-retry",
    ).invoke(task)
    assert [call["session_id"] for call in cleanup.calls] == [
        expected_session_id(task["invocation_id"]),
    ]


def test_schema_invalid_result_is_repaired_once_in_a_new_session() -> None:
    task, result = evidence_fixture()
    streams = 0
    sent_tasks: list[dict[str, Any]] = []
    invocation_ids = iter(["inv-repair-1"])

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal streams
        body = json.loads(request.content)
        assert body.get("class_method") == "async_ephemeral_stream_query"
        streams += 1
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
    assert streams == 2
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
    streams = 0
    invocation_ids = iter(["inv-repair-1"])

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal streams
        body = json.loads(request.content)
        assert body.get("class_method") == "async_ephemeral_stream_query"
        streams += 1
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
    assert streams == 2


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


@pytest.mark.parametrize(
    "provider_code",
    [
        "MODEL_JSON_INVALID",
        "RESULT_SCHEMA_INVALID",
        "RESULT_SEMANTIC_INVALID",
        "RUNTIME_AGENT_OUTPUT_INVALID",
    ],
)
def test_agent_output_rejection_is_not_misreported_as_request_invalid(
    provider_code: str,
) -> None:
    task, _result = evidence_fixture()
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": provider_code})

    with pytest.raises(AgentRuntimeError, match="RUNTIME_AGENT_OUTPUT_INVALID"):
        runtime_client(httpx.MockTransport(handler), FakeCleanupSink()).invoke(task)
    assert calls == 1


def test_stream_http_failure_reads_deferred_body_before_classification() -> None:
    task, _result = evidence_fixture()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, stream=DeferredErrorStream())

    with pytest.raises(AgentRuntimeError, match="RUNTIME_AGENT_OUTPUT_INVALID"):
        runtime_client(httpx.MockTransport(handler), FakeCleanupSink()).invoke(task)


def test_safety_block_event_is_terminal_after_runtime_cleanup() -> None:
    task, _result = evidence_fixture()
    stream_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal stream_calls
        body = json.loads(request.content)
        assert body.get("class_method") == "async_ephemeral_stream_query"
        stream_calls += 1
        event = {"errorCode": "SAFETY_BLOCKED"}
        return httpx.Response(200, text=f"data: {json.dumps(event)}\n\n")

    with pytest.raises(AgentRuntimeError, match="SAFETY_BLOCKED"):
        runtime_client(httpx.MockTransport(handler), FakeCleanupSink()).invoke(task)
    assert stream_calls == 1


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
    stream_calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal stream_calls
        body = json.loads(request.content)
        assert body.get("class_method") == "async_ephemeral_stream_query"
        stream_calls += 1
        if stream_calls == 1:
            return httpx.Response(429, headers={"Retry-After": "1.5"})
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
    stream_calls = 0
    sent_tasks: list[dict[str, Any]] = []
    invocation_ids = iter(["inv-transport-2", "inv-repair-after-retry"])

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal stream_calls
        body = json.loads(request.content)
        assert body.get("class_method") == "async_ephemeral_stream_query"
        stream_calls += 1
        if stream_calls == 1:
            return httpx.Response(503)
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
