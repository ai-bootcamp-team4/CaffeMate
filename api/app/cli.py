import argparse
import asyncio
import json
from typing import cast

from app.candidates.seed_registry import IndependentSeedRegistry
from app.database import create_database_handle
from app.mcp.client import GoogleIdentityTokenProvider
from app.mcp.preflight import McpManifestPreflight
from app.mcp.scope import ScopeTokenSigner
from app.migrations import apply_migrations, verify_migrations
from app.settings import RuntimeSettings
from app.workflows.models import HeadFence


def main() -> None:
    parser = argparse.ArgumentParser(prog="caffemate-api")
    parser.add_argument(
        "command",
        choices=["migrate", "verify-migrations", "verify-mcp-preflight"],
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
