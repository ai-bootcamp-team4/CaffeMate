from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from app.agents.runtime import AgentRuntimeHttpClient


class _Tokens:
    def token(self) -> str:
        return "access-token"


class _Cleanup:
    def enqueue_session_delete(self, **_kwargs: Any) -> None:
        return None


def _fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(__file__).resolve().parents[2]
    matrix = json.loads(
        (root / "agents" / "fixtures" / "task-matrix.json").read_text(encoding="utf-8")
    )
    case = next(value for value in matrix["cases"] if value["id"] == "evidence_plan-complete")
    return case["task"], case["result"]


def test_runtime_propagates_same_w3c_context_in_header_and_agent_task() -> None:
    task, result = _fixture()
    observed: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        observed["header"] = request.headers["traceparent"]
        observed["task"] = json.loads(body["input"]["message"])
        event = {
            "author": task["agent_name"],
            "partial": False,
            "content": {"parts": [{"text": json.dumps(result)}]},
        }
        return httpx.Response(200, text=f'{json.dumps({"output": event})}\n')

    client = AgentRuntimeHttpClient(
        gcp_project_id="gcp-project",
        resource_id="runtime-1",
        user_hmac_secret="x" * 32,
        access_tokens=_Tokens(),
        cleanup_sink=_Cleanup(),
        transport=httpx.MockTransport(handler),
        now=lambda: datetime(2026, 8, 21, 8, 59, tzinfo=UTC),
    )
    provider = TracerProvider()
    tracer = provider.get_tracer("test")
    token = trace.set_tracer_provider(provider)
    del token

    with tracer.start_as_current_span("request"):
        client.invoke(task)

    assert observed["header"] == observed["task"]["trace_context"]["traceparent"]
    assert observed["header"].startswith("00-")
    assert task.get("trace_context") is None
