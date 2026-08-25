from threading import Event, Thread
from typing import cast

from sqlalchemy import Engine, text

from app.agents.runtime import (
    AgentRuntimeHttpClient,
    GoogleAccessTokenProvider,
    PostgresAgentCleanupSink,
)
from app.candidates.seed_registry import IndependentSeedRegistry
from app.mcp.client import GoogleIdentityTokenProvider, McpHttpClient
from app.mcp.scope import ScopeTokenSigner
from app.security.content_protection import ContentProtection
from app.settings import RuntimeSettings
from app.workflows.execution import (
    FirstProposalPipeline,
    PostgresFirstProposalExecutor,
)
from app.workflows.lease import PostgresWorkflowLeaseRepository
from app.workflows.linear_agent_pipeline import LinearMultiAgentProposalPipeline
from app.workflows.models import WorkflowCode, WorkflowProgress, WorkflowRun, WorkflowStatus
from app.workflows.postgres_repository import PostgresWorkflowRepository
from app.workflows.service import WorkflowService
from app.workflows.simple_proposal import SimpleProposalBuilder


class InlineQueuedWorkflowOperations:
    """Run queued verification work with the same lease and checkpoints as the Worker."""

    def __init__(
        self,
        workflows: WorkflowService,
        engine: Engine,
        pipeline: FirstProposalPipeline,
        *,
        worker_id: str = "caffemate-verification",
        heartbeat_interval_seconds: float = 15.0,
    ) -> None:
        self._workflows = workflows
        self._engine = engine
        self._leases = PostgresWorkflowLeaseRepository(engine)
        self._executor = PostgresFirstProposalExecutor(engine, pipeline, self._leases)
        self._worker_id = worker_id
        self._heartbeat_interval_seconds = heartbeat_interval_seconds

    def start(
        self,
        *,
        project_id: str,
        user_id: str,
        workflow_code: WorkflowCode,
        idempotency_key: str,
    ) -> WorkflowRun:
        run = self._workflows.start(
            project_id=project_id,
            user_id=user_id,
            workflow_code=workflow_code,
            idempotency_key=idempotency_key,
        )
        if run.status == WorkflowStatus.QUEUED:
            self._execute(run.workflow_run_id)
        return run

    def get_progress(
        self,
        *,
        project_id: str,
        workflow_run_id: str,
        user_id: str,
    ) -> WorkflowProgress:
        return self._workflows.get_progress(
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            user_id=user_id,
        )

    def _execute(self, workflow_run_id: str) -> None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT stage_run_id, input_digest FROM stage_runs "
                    "WHERE workflow_run_id=:workflow_run_id AND stage_code='RUN_PROPOSAL'"
                ),
                {"workflow_run_id": workflow_run_id},
            ).mappings().one()
        lease = self._leases.claim(
            stage_run_id=str(row["stage_run_id"]),
            worker_id=self._worker_id,
            expected_input_digest=str(row["input_digest"]),
        )
        if lease is None:
            raise RuntimeError("Verification workflow lease is unavailable")

        stop = Event()
        def heartbeat() -> None:
            while not stop.wait(self._heartbeat_interval_seconds):
                if not self._leases.heartbeat(
                    stage_run_id=lease.stage_run_id,
                    lease_token=lease.lease_token,
                ):
                    return

        heartbeat_thread = Thread(
            target=heartbeat,
            name=f"caffemate-verification-heartbeat-{lease.stage_run_id}",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            self._executor.execute(lease)
        finally:
            stop.set()
            heartbeat_thread.join(timeout=self._heartbeat_interval_seconds)


def build_verification_workflows(
    *,
    settings: RuntimeSettings,
    engine: Engine,
    seed_registry: IndependentSeedRegistry,
    content_protection: ContentProtection | None,
) -> InlineQueuedWorkflowOperations:
    if (
        not settings.policy_snapshot_id
        or not settings.has_agent_runtime_configuration
        or not settings.has_mcp_configuration
    ):
        raise ValueError("FIRST_PROPOSAL requires database, Agent Runtime, MCP and policy")
    runtime = AgentRuntimeHttpClient(
        gcp_project_id=cast(str, settings.agent_runtime_project_id),
        resource_id=cast(str, settings.agent_runtime_resource_id),
        user_hmac_secret=cast(str, settings.agent_runtime_user_hmac_secret),
        access_tokens=GoogleAccessTokenProvider(),
        cleanup_sink=PostgresAgentCleanupSink(engine),
        content_protection=content_protection,
    )
    mcp = McpHttpClient(
        base_url=cast(str, settings.mcp_base_url),
        audience=cast(str, settings.mcp_audience),
        identity_provider=GoogleIdentityTokenProvider(),
        scope_signer=ScopeTokenSigner(
            secret=cast(str, settings.mcp_scope_hmac_secret),
            issuer="caffemate-control-api",
            audience="caffemate-mcp",
        ),
    )
    pipeline = LinearMultiAgentProposalPipeline(
        runtime=runtime,
        mcp=mcp,
        seed_registry=seed_registry,
        builder=SimpleProposalBuilder(seed_registry),
    )
    workflows = WorkflowService(
        PostgresWorkflowRepository(
            engine,
            policy_snapshot_id=settings.policy_snapshot_id,
            seed_registry_id=seed_registry.registry_id,
            pipeline=pipeline,
            seed_registry=seed_registry,
        )
    )
    return InlineQueuedWorkflowOperations(workflows, engine, pipeline)