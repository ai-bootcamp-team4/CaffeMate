import argparse
import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from app.agents.runtime import AgentRuntimeHttpClient, GoogleAccessTokenProvider
from app.agents.task_factory import compute_agent_input_digest
from app.candidates.seed_registry import IndependentSeedRegistry
from app.database import create_database_handle
from app.mcp.client import GoogleIdentityTokenProvider
from app.mcp.preflight import McpManifestPreflight
from app.mcp.scope import ScopeTokenSigner
from app.migrations import apply_migrations, verify_migrations
from app.settings import RuntimeSettings
from app.workflows.models import HeadFence


class _StrictAgentCleanupSink:
    def enqueue_session_delete(
        self,
        *,
        runtime_resource: str,
        user_id: str,
        session_id: str,
    ) -> None:
        del runtime_resource, user_id, session_id
        raise RuntimeError("Agent Runtime verification requires synchronous session deletion")


def _agent_runtime_probe_task() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    matrix = json.loads(
        (root / "agents" / "fixtures" / "task-matrix.json").read_text(
            encoding="utf-8"
        )
    )
    fixture = next(
        item for item in matrix["cases"] if item["id"] == "evidence_plan-complete"
    )
    task = copy.deepcopy(fixture["task"])
    probe_id = uuid4().hex
    task.update(
        {
            "task_id": f"runtime-preflight-{probe_id}",
            "invocation_id": str(uuid4()),
            "workflow_run_id": f"runtime-preflight-{probe_id}",
            "stage_run_id": f"runtime-preflight-{probe_id}",
            "venture_project_id": f"runtime-preflight-{probe_id}",
            "transport_attempt": 1,
            "repair_attempt": 0,
            "deadline_at": (datetime.now(UTC) + timedelta(minutes=3))
            .isoformat()
            .replace("+00:00", "Z"),
        }
    )
    task["input_digest"] = compute_agent_input_digest(task)
    return cast(dict[str, Any], task)


def main() -> None:
    parser = argparse.ArgumentParser(prog="caffemate-api")
    parser.add_argument(
        "command",
        choices=[
            "migrate",
            "verify-migrations",
            "verify-mcp-preflight",
            "verify-agent-runtime",
        ],
    )
    arguments = parser.parse_args()

    if arguments.command == "migrate":
        handle = create_database_handle(RuntimeSettings.from_environment())
        if handle is None:
            parser.error(
                "DATABASE_URL or complete Cloud SQL INSTANCE_CONNECTION_NAME/DB_* settings required"
            )
        try:
            apply_migrations(handle.engine)
        finally:
            handle.close()
    elif arguments.command == "verify-migrations":
        handle = create_database_handle(RuntimeSettings.from_environment())
        if handle is None:
            parser.error(
                "DATABASE_URL or complete Cloud SQL INSTANCE_CONNECTION_NAME/DB_* settings required"
            )
        try:
            verification = verify_migrations(handle.engine)
            print(
                json.dumps(
                    {
                        "status": "verified",
                        "migration_count": verification.count,
                        "migration_set_digest": verification.set_digest,
                    },
                    sort_keys=True,
                )
            )
        finally:
            handle.close()
    elif arguments.command == "verify-mcp-preflight":
        settings = RuntimeSettings.from_environment()
        if not settings.has_mcp_configuration or not settings.policy_snapshot_id:
            parser.error("MCP and policy snapshot configuration required")
        seed_registry = IndependentSeedRegistry.load_default()
        report = asyncio.run(
            McpManifestPreflight(
                base_url=cast(str, settings.mcp_base_url),
                audience=cast(str, settings.mcp_audience),
                identity_provider=GoogleIdentityTokenProvider(),
                scope_signer=ScopeTokenSigner(
                    secret=cast(str, settings.mcp_scope_hmac_secret),
                    issuer="caffemate-control-api",
                    audience="caffemate-mcp",
                ),
            ).run(
                venture_project_id="control-api-deploy-preflight",
                workflow_run_id="control-api-deploy-preflight",
                head=HeadFence(
                    workflow_generation=1,
                    state_version=1,
                    policy_snapshot_id=settings.policy_snapshot_id,
                    seed_registry_id=seed_registry.registry_id,
                ),
                timeout_seconds=10.0,
            )
        )
        print(json.dumps({"status": "verified", **report.model_dump()}, sort_keys=True))
    elif arguments.command == "verify-agent-runtime":
        settings = RuntimeSettings.from_environment()
        if not settings.has_agent_runtime_configuration:
            parser.error("complete Agent Runtime configuration required")
        result = AgentRuntimeHttpClient(
            gcp_project_id=cast(str, settings.agent_runtime_project_id),
            resource_id=cast(str, settings.agent_runtime_resource_id),
            user_hmac_secret=cast(str, settings.agent_runtime_user_hmac_secret),
            access_tokens=GoogleAccessTokenProvider(),
            cleanup_sink=_StrictAgentCleanupSink(),
        ).invoke(_agent_runtime_probe_task())
        print(
            json.dumps(
                {
                    "status": "verified",
                    "agent_name": result["agent_name"],
                    "task_type": result["task_type"],
                    "result_status": result["status"],
                },
                sort_keys=True,
            )
        )
