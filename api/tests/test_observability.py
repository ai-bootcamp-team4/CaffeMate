from __future__ import annotations

from pathlib import Path

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.observability import (
    RAG_SIGNAL_CONTRACT,
    current_trace_carrier,
    safe_agent_attributes,
)

ROOT = Path(__file__).resolve().parents[2]


def test_serverless_trace_export_is_immediate_in_python_and_agent_runtime() -> None:
    python_source = (ROOT / "api/app/observability.py").read_text(encoding="utf-8")
    agent_source = (ROOT / "agents/src/telemetry.ts").read_text(encoding="utf-8")

    assert "SimpleSpanProcessor" in python_source
    assert "BatchSpanProcessor" not in python_source
    assert "SimpleSpanProcessor" in agent_source
    assert "BatchSpanProcessor" not in agent_source


def test_current_trace_carrier_is_w3c_and_contains_no_business_identifier() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("root"):
        carrier = current_trace_carrier()

    assert carrier["traceparent"].startswith("00-")
    assert len(carrier["traceparent"].split("-")) == 4
    assert "project" not in str(carrier).lower()


def test_agent_attributes_include_release_contract_but_not_payload_or_ids() -> None:
    task = {
        "agent_name": "PROPOSAL_AGENT",
        "task_type": "PROPOSE_INDEPENDENT",
        "prompt_version": "proposal-independent-v3",
        "input_schema_id": "agent-task-v1",
        "output_schema_id": "agent-result-v1",
        "venture_project_id": "secret-project-id",
        "workflow_run_id": "secret-workflow-id",
        "payload": {"target_area_input": "exact user location"},
    }

    attributes = safe_agent_attributes(
        task,
        model_id="gemini-2.5-flash",
        source_revision="abc123",
    )

    assert attributes == {
        "caffemate.agent.role": "PROPOSAL_AGENT",
        "caffemate.agent.task_type": "PROPOSE_INDEPENDENT",
        "caffemate.prompt.version": "proposal-independent-v3",
        "caffemate.schema.input": "agent-task-v1",
        "caffemate.schema.output": "agent-result-v1",
        "gen_ai.request.model": "gemini-2.5-flash",
        "service.version": "abc123",
    }
    assert "secret" not in str(attributes)
    assert "location" not in str(attributes)


def test_rag_signal_contract_is_low_cardinality_and_does_not_fake_measurements() -> None:
    assert set(RAG_SIGNAL_CONTRACT) == {
        "caffemate.rag.retrieve.duration",
        "caffemate.rag.rerank.duration",
        "caffemate.rag.hits",
        "caffemate.rag.evidence.accepted",
        "caffemate.rag.citations",
    }
    for definition in RAG_SIGNAL_CONTRACT.values():
        assert definition["instrument"] in {"counter", "histogram"}
        assert definition["unit"] in {"ms", "1"}
        assert set(definition["attributes"]).issubset(
            {"source_family", "result_status", "index_generation"}
        )
        assert "default" not in definition
