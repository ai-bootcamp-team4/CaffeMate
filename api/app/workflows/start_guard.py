import asyncio
from collections.abc import Collection
from typing import Protocol
from uuid import uuid4

from app.domain.errors import (
    FirstProposalConfigurationUnavailableError,
    FirstProposalPreflightUnavailableError,
)
from app.mcp.client import McpClientError
from app.mcp.preflight import McpManifestPreflight
from app.workflows.first_proposal import FirstProposalStage
from app.workflows.models import HeadFence, WorkflowCode


class WorkflowStartGuard(Protocol):
    def validate(self, workflow_code: WorkflowCode, *, project_id: str) -> None: ...


class WorkflowStartManifestGate(Protocol):
    def validate(self, *, project_id: str) -> None: ...


class McpManifestStartGate:
    def __init__(
        self,
        preflight: McpManifestPreflight,
        *,
        policy_snapshot_id: str,
        seed_registry_id: str,
    ) -> None:
        self._preflight = preflight
        self._head = HeadFence(
            workflow_generation=1,
            state_version=1,
            policy_snapshot_id=policy_snapshot_id,
            seed_registry_id=seed_registry_id,
        )

    def validate(self, *, project_id: str) -> None:
        try:
            asyncio.run(
                self._preflight.run(
                    venture_project_id=project_id,
                    workflow_run_id=f"start-preflight-{uuid4()}",
                    head=self._head,
                    timeout_seconds=10.0,
                )
            )
        except McpClientError as error:
            raise FirstProposalPreflightUnavailableError([error.mcp_code]) from error
        except Exception as error:
            raise FirstProposalPreflightUnavailableError(
                ["MCP_PREFLIGHT_INTERNAL_ERROR"]
            ) from error


class FirstProposalStartGuard:
    def __init__(
        self,
        available_stages: Collection[FirstProposalStage],
        *,
        manifest_gate: WorkflowStartManifestGate | None = None,
    ) -> None:
        available = set(available_stages)
        self._missing_stages = sorted(
            stage.value for stage in FirstProposalStage if stage not in available
        )
        self._manifest_gate = manifest_gate

    @property
    def missing_stages(self) -> list[str]:
        return list(self._missing_stages)

    def validate(self, workflow_code: WorkflowCode, *, project_id: str) -> None:
        if workflow_code != WorkflowCode.FIRST_PROPOSAL:
            return
        if self._missing_stages:
            raise FirstProposalConfigurationUnavailableError(self._missing_stages)
        if self._manifest_gate is None:
            raise FirstProposalPreflightUnavailableError(
                ["MCP_MANIFEST_PREFLIGHT_UNCONFIGURED"]
            )
        self._manifest_gate.validate(project_id=project_id)
