from __future__ import annotations

import json
from pathlib import Path


def test_dashboard_is_agentops_readback_contract_without_fake_rag_series() -> None:
    root = Path(__file__).resolve().parents[2]
    dashboard = json.loads(
        (root / "deploy/monitoring/caffemate-agentops-dashboard.json").read_text(
            encoding="utf-8"
        )
    )
    serialized = json.dumps(dashboard, ensure_ascii=False)

    assert dashboard["displayName"] == "CaffeMate AgentOps"
    assert "API → MCP/RAG → Agent Runtime → Gemini" in serialized
    assert "Worker cleanup is an independent trace" in serialized
    assert "never records raw user text" in serialized
    assert "caffemate_agent_invocations" in serialized
    assert "caffemate_model_latency_ms" in serialized
    assert "logging.googleapis.com/user/caffemate.rag" not in serialized


def test_readback_script_returns_dashboard_and_trace_console_paths() -> None:
    root = Path(__file__).resolve().parents[2]
    script = (root / "scripts/verify-agentops-observability.sh").read_text(encoding="utf-8")

    assert "gcloud monitoring dashboards describe" in script
    assert "console.cloud.google.com/monitoring/dashboards" in script
    assert "console.cloud.google.com/traces/explorer" in script


def test_production_deployments_enable_trace_with_the_source_revision() -> None:
    root = Path(__file__).resolve().parents[2]
    webhook = (root / "cloudbuild.main-webhook.yaml").read_text(encoding="utf-8")
    runtime_deploy = (root / "scripts/deploy-agent-runtime.sh").read_text(
        encoding="utf-8"
    )
    backend_deploy = (root / "scripts/deploy-api-worker-runtime.sh").read_text(
        encoding="utf-8"
    )

    for deployment in (webhook, runtime_deploy, backend_deploy):
        assert "CAFFEMATE_OTEL_ENABLED" in deployment
        assert "CAFFEMATE_SOURCE_REVISION" in deployment

    assert "AGENT_MODEL_ID" in runtime_deploy
