import argparse
import asyncio
import copy
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from app.agents.runtime import (
    AgentRuntimeHttpClient,
    GoogleAccessTokenProvider,
    verify_agent_runtime_iam,
)
from app.agents.task_factory import compute_agent_input_digest
from app.candidates.seed_registry import IndependentSeedRegistry
from app.database import create_database_handle
from app.documents.extraction import DocumentExtractionService
from app.documents.service import DocumentService
from app.documents.storage import GoogleCloudDocumentStorage
from app.domain.models import CafeTypePreference
from app.mcp.client import GoogleIdentityTokenProvider
from app.mcp.preflight import McpManifestPreflight
from app.mcp.scope import ScopeTokenSigner
from app.migrations import apply_migrations, verify_migrations
from app.projects.postgres_repository import PostgresProjectRepository
from app.projects.service import ProjectService
from app.results.postgres_repository import PostgresResultRepository
from app.results.service import ResultService
from app.settings import RuntimeSettings
from app.verification.documents import DocumentStorageCanary, DocumentStorageCanaryError
from app.verification.first_proposal import (
    FirstProposalCanary,
    FirstProposalCanaryError,
    PostgresFirstProposalCanaryCleaner,
)
from app.workflows.first_proposal import FirstProposalStage
from app.workflows.models import HeadFence
from app.workflows.postgres_repository import PostgresWorkflowRepository
from app.workflows.service import WorkflowService
from app.workflows.start_guard import FirstProposalStartGuard, McpManifestStartGate


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


def _agent_runtime_probe_fixture(fixture_id: str) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    matrix = json.loads(
        (root / "agents" / "fixtures" / "task-matrix.json").read_text(
            encoding="utf-8"
        )
    )
    fixture = next((item for item in matrix["cases"] if item["id"] == fixture_id), None)
    if not isinstance(fixture, dict):
        raise ValueError(f"Unknown Agent Runtime probe fixture: {fixture_id}")
    return cast(dict[str, Any], fixture)


def _agent_runtime_probe_task(
    fixture_id: str = "evidence_plan-complete",
) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    fixture = _agent_runtime_probe_fixture(fixture_id)
    task = copy.deepcopy(fixture["task"])
    release = json.loads(
        (root / "agents" / "release-manifest.json").read_text(encoding="utf-8")
    )
    deadline_seconds = release["tasks"][task["task_type"]]["deadline_seconds"]
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
            "deadline_at": (datetime.now(UTC) + timedelta(seconds=deadline_seconds))
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
            "verify-agent-runtime-iam",
            "verify-first-proposal",
            "verify-document-storage",
        ],
    )
    parser.add_argument("--timeout-seconds", type=float, default=1200.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    parser.add_argument(
        "--agent-fixture-id", default="evidence_plan-complete"
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument(
        "--cafe-type-preference",
        choices=[preference.value for preference in CafeTypePreference],
        default=CafeTypePreference.OPEN_TO_BOTH.value,
    )
    arguments = parser.parse_args()
    if arguments.repeat < 1 or arguments.repeat > 20:
        parser.error("repeat must be between 1 and 20")

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
        runtime = AgentRuntimeHttpClient(
            gcp_project_id=cast(str, settings.agent_runtime_project_id),
            resource_id=cast(str, settings.agent_runtime_resource_id),
            user_hmac_secret=cast(str, settings.agent_runtime_user_hmac_secret),
            access_tokens=GoogleAccessTokenProvider(),
            cleanup_sink=_StrictAgentCleanupSink(),
        )
        expected = _agent_runtime_probe_fixture(arguments.agent_fixture_id)["result"]
        summaries: list[dict[str, Any]] = []
        for run_number in range(1, arguments.repeat + 1):
            result = runtime.invoke(
                _agent_runtime_probe_task(arguments.agent_fixture_id)
            )
            if result["status"] != expected["status"]:
                raise RuntimeError("Agent Runtime probe status differs from fixture")
            expected_payload = expected.get("payload")
            result_payload = result.get("payload")
            expected_decision = (
                expected_payload.get("decision")
                if isinstance(expected_payload, dict)
                else None
            )
            result_decision = (
                result_payload.get("decision")
                if isinstance(result_payload, dict)
                else None
            )
            if expected_decision is not None and result_decision != expected_decision:
                raise RuntimeError("Agent Runtime probe decision differs from fixture")
            if expected_decision == "PROPOSE_DELTA":
                if not isinstance(result_payload, dict):
                    raise RuntimeError("Agent Runtime probe payload is missing")
                expected_operations = expected_payload.get("operations")
                result_operations = result_payload.get("operations")
                if not isinstance(expected_operations, list) or not isinstance(
                    result_operations, list
                ):
                    raise RuntimeError("Agent Runtime probe operations are missing")
                core_fields = (
                    "field_path",
                    "kind",
                    "expected_old_value",
                    "typed_value",
                )
                expected_core = [
                    {field: operation[field] for field in core_fields}
                    for operation in expected_operations
                    if isinstance(operation, dict)
                ]
                result_core = [
                    {field: operation[field] for field in core_fields}
                    for operation in result_operations
                    if isinstance(operation, dict)
                ]
                if len(expected_core) != len(expected_operations) or len(
                    result_core
                ) != len(result_operations):
                    raise RuntimeError("Agent Runtime probe operation shape is invalid")
                if result_core != expected_core:
                    raise RuntimeError("Agent Runtime probe operations differ from fixture")
            summaries.append(
                {
                    "run": run_number,
                    "agent_name": result["agent_name"],
                    "task_type": result["task_type"],
                    "result_status": result["status"],
                    **(
                        {"decision": result_decision}
                        if result_decision is not None
                        else {}
                    ),
                }
            )
        print(
            json.dumps(
                {
                    "status": "verified",
                    "fixture_id": arguments.agent_fixture_id,
                    "runs": summaries,
                },
                sort_keys=True,
            )
        )
    elif arguments.command == "verify-agent-runtime-iam":
        settings = RuntimeSettings.from_environment()
        if not settings.agent_runtime_project_id or not settings.agent_runtime_resource_id:
            parser.error("Agent Runtime project and resource configuration required")
        iam_report = verify_agent_runtime_iam(
            gcp_project_id=settings.agent_runtime_project_id,
            resource_id=settings.agent_runtime_resource_id,
            access_tokens=GoogleAccessTokenProvider(),
        )
        print(json.dumps({"status": "verified", **iam_report}, sort_keys=True))
    elif arguments.command == "verify-first-proposal":
        settings = RuntimeSettings.from_environment()
        if (
            not settings.has_mcp_configuration
            or not settings.policy_snapshot_id
        ):
            parser.error("database, MCP and policy snapshot configuration required")
        handle = create_database_handle(settings)
        if handle is None:
            parser.error("database configuration required")
        seed_registry = IndependentSeedRegistry.load_default()
        preflight = McpManifestPreflight(
            base_url=cast(str, settings.mcp_base_url),
            audience=cast(str, settings.mcp_audience),
            identity_provider=GoogleIdentityTokenProvider(),
            scope_signer=ScopeTokenSigner(
                secret=cast(str, settings.mcp_scope_hmac_secret),
                issuer="caffemate-control-api",
                audience="caffemate-mcp",
            ),
        )
        projects = ProjectService(PostgresProjectRepository(handle.engine))
        workflows = WorkflowService(
            PostgresWorkflowRepository(
                handle.engine,
                policy_snapshot_id=settings.policy_snapshot_id,
                seed_registry_id=seed_registry.registry_id,
            ),
            start_guard=FirstProposalStartGuard(
                list(FirstProposalStage),
                manifest_gate=McpManifestStartGate(
                    preflight,
                    policy_snapshot_id=settings.policy_snapshot_id,
                    seed_registry_id=seed_registry.registry_id,
                ),
            ),
        )
        try:
            canary_report = FirstProposalCanary(
                projects=projects,
                workflows=workflows,
                results=ResultService(PostgresResultRepository(handle.engine)),
                cleaner=PostgresFirstProposalCanaryCleaner(handle.engine),
            ).run(
                timeout_seconds=arguments.timeout_seconds,
                poll_interval_seconds=arguments.poll_interval_seconds,
                cafe_type_preference=CafeTypePreference(
                    arguments.cafe_type_preference
                ),
            )
        except FirstProposalCanaryError as error:
            print(
                json.dumps(
                    {"status": "failed", "code": error.code, **error.details},
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            raise SystemExit(1) from error
        finally:
            handle.close()
        print(json.dumps(canary_report.as_dict(), sort_keys=True))
    elif arguments.command == "verify-document-storage":
        settings = RuntimeSettings.from_environment()
        if (
            not settings.has_document_storage_configuration
            or not settings.has_agent_runtime_configuration
            or not settings.policy_snapshot_id
        ):
            parser.error(
                "database, document storage, Agent Runtime and policy configuration required"
            )
        handle = create_database_handle(settings)
        if handle is None:
            parser.error("database configuration required")
        storage = GoogleCloudDocumentStorage(
            cast(str, settings.document_bucket),
            signing_service_account_email=cast(
                str, settings.document_signing_service_account_email
            ),
        )
        runtime = AgentRuntimeHttpClient(
            gcp_project_id=cast(str, settings.agent_runtime_project_id),
            resource_id=cast(str, settings.agent_runtime_resource_id),
            user_hmac_secret=cast(str, settings.agent_runtime_user_hmac_secret),
            access_tokens=GoogleAccessTokenProvider(),
            cleanup_sink=_StrictAgentCleanupSink(),
        )
        try:
            document_report = DocumentStorageCanary(
                engine=handle.engine,
                documents=DocumentService(handle.engine, storage),
                extraction=DocumentExtractionService(handle.engine, runtime),
                storage=storage,
                policy_snapshot_id=settings.policy_snapshot_id,
                seed_registry_id=IndependentSeedRegistry.load_default().registry_id,
            ).run()
        except DocumentStorageCanaryError as error:
            print(
                json.dumps(
                    {"status": "failed", "code": error.code, **error.details},
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            raise SystemExit(1) from error
        finally:
            handle.close()
        print(json.dumps(document_report.as_dict(), sort_keys=True))
