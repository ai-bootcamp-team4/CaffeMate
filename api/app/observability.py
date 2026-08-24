"""CaffeMate AgentOps tracing without business payloads or user identifiers."""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final

from opentelemetry import propagate, trace
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode

TRACER_NAME: Final = "caffemate.control-api"

# RAG implementation may emit these instruments only after a real operation completes.
# There are intentionally no defaults: an absent measurement must remain absent.
RAG_SIGNAL_CONTRACT: Final[dict[str, dict[str, object]]] = {
    "caffemate.rag.retrieve.duration": {
        "instrument": "histogram",
        "unit": "ms",
        "attributes": ("source_family", "result_status", "index_generation"),
    },
    "caffemate.rag.rerank.duration": {
        "instrument": "histogram",
        "unit": "ms",
        "attributes": ("source_family", "result_status", "index_generation"),
    },
    "caffemate.rag.hits": {
        "instrument": "histogram",
        "unit": "1",
        "attributes": ("source_family", "result_status", "index_generation"),
    },
    "caffemate.rag.evidence.accepted": {
        "instrument": "counter",
        "unit": "1",
        "attributes": ("source_family", "result_status", "index_generation"),
    },
    "caffemate.rag.citations": {
        "instrument": "counter",
        "unit": "1",
        "attributes": ("source_family", "result_status", "index_generation"),
    },
}

_configured = False


def configure_cloud_trace(
    *,
    service_name: str,
    service_version: str | None = None,
    project_id: str | None = None,
) -> None:
    """Configure one process-wide provider when explicitly enabled."""

    global _configured
    if _configured or os.getenv("CAFFEMATE_OTEL_ENABLED", "").lower() not in {"1", "true"}:
        return
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version or "unknown",
            "deployment.environment.name": os.getenv("CAFFEMATE_ENVIRONMENT", "unknown"),
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(CloudTraceSpanExporter(project_id=project_id))  # type: ignore[no-untyped-call]
    )
    trace.set_tracer_provider(provider)
    _configured = True


def tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME)


def current_trace_carrier() -> dict[str, str]:
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return {key: value for key, value in carrier.items() if key in {"traceparent", "tracestate"}}


def safe_agent_attributes(
    task: Mapping[str, Any],
    *,
    model_id: str | None = None,
    source_revision: str | None = None,
) -> dict[str, str]:
    mapping = {
        "caffemate.agent.role": task.get("agent_name"),
        "caffemate.agent.task_type": task.get("task_type"),
        "caffemate.prompt.version": task.get("prompt_version"),
        "caffemate.schema.input": task.get("input_schema_id"),
        "caffemate.schema.output": task.get("output_schema_id"),
        "gen_ai.request.model": model_id,
        "service.version": source_revision,
    }
    return {
        key: value[:256]
        for key, value in mapping.items()
        if isinstance(value, str) and value
    }


def record_safe_metric(event: str, **attributes: str | int | float | bool) -> None:
    """Emit an allow-listed structured event for low-cardinality log metrics."""

    allowed = {
        "workflow_code",
        "result_status",
        "agent_role",
        "task_type",
        "model_id",
        "prompt_version",
        "elapsed_ms",
        "token_count",
        "http_status",
    }
    payload = {
        key: value
        for key, value in attributes.items()
        if key in allowed and isinstance(value, (str, int, float, bool))
    }
    print(
        json.dumps({"event": event, **payload}, ensure_ascii=False, sort_keys=True),
        flush=True,
    )


class SafeTracingMiddleware:
    """Trace HTTP requests while excluding path values, query strings, headers and bodies."""

    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        carrier = {
            key.decode("latin-1"): value.decode("latin-1")
            for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and isinstance(value, bytes)
        }
        context = propagate.extract(carrier)
        method = str(scope.get("method", "UNKNOWN"))[:16]
        status_code = 500

        async def traced_send(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 500))
            await send(message)

        with tracer().start_as_current_span(
            "caffemate.http.request",
            context=context,
            kind=SpanKind.SERVER,
            attributes={"http.request.method": method},
        ) as span:
            try:
                await self._app(scope, receive, traced_send)
            except Exception as error:
                span.record_exception(error)
                span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                route = scope.get("route")
                route_path = getattr(route, "path", None)
                if isinstance(route_path, str):
                    span.set_attribute("http.route", route_path)
                span.set_attribute("http.response.status_code", status_code)
                if status_code >= 500:
                    span.set_status(Status(StatusCode.ERROR))
