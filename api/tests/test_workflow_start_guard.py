import pytest

from app.domain.errors import (
    FirstProposalConfigurationUnavailableError,
    FirstProposalPreflightUnavailableError,
)
from app.mcp.client import McpClientError
from app.mcp.preflight import McpPreflightReport
from app.workflows.first_proposal import FirstProposalStage
from app.workflows.models import WorkflowCode
from app.workflows.start_guard import FirstProposalStartGuard, McpManifestStartGate


class ManifestGateFixture:
    def __init__(self) -> None:
        self.project_ids: list[str] = []

    def validate(self, *, project_id: str) -> None:
        self.project_ids.append(project_id)


class PreflightFixture:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def run(self, **kwargs: object) -> McpPreflightReport:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return McpPreflightReport(
            protocol_revision="2026-07-28",
            manifest_digest="a" * 64,
            tool_count=10,
        )


def test_complete_first_proposal_composition_is_accepted() -> None:
    manifest_gate = ManifestGateFixture()
    guard = FirstProposalStartGuard(
        list(FirstProposalStage), manifest_gate=manifest_gate
    )

    guard.validate(WorkflowCode.FIRST_PROPOSAL, project_id="project-1")

    assert manifest_gate.project_ids == ["project-1"]


def test_incomplete_first_proposal_composition_reports_every_missing_stage() -> None:
    guard = FirstProposalStartGuard(
        [FirstProposalStage.AREA_RESOLUTION, FirstProposalStage.CLAIM_PLAN]
    )

    with pytest.raises(FirstProposalConfigurationUnavailableError) as caught:
        guard.validate(WorkflowCode.FIRST_PROPOSAL, project_id="project-1")

    assert caught.value.missing_stage_codes == sorted(
        stage.value
        for stage in FirstProposalStage
        if stage
        not in {FirstProposalStage.AREA_RESOLUTION, FirstProposalStage.CLAIM_PLAN}
    )


def test_complete_composition_without_manifest_gate_is_accepted() -> None:
    guard = FirstProposalStartGuard(list(FirstProposalStage))

    guard.validate(WorkflowCode.FIRST_PROPOSAL, project_id="project-1")


def test_manifest_start_gate_runs_full_preflight_with_project_scope() -> None:
    preflight = PreflightFixture()
    gate = McpManifestStartGate(  # type: ignore[arg-type]
        preflight,
        policy_snapshot_id="policy-v1",
        seed_registry_id="seed-v1",
    )

    gate.validate(project_id="project-1")

    assert len(preflight.calls) == 1
    call = preflight.calls[0]
    assert call["venture_project_id"] == "project-1"
    assert str(call["workflow_run_id"]).startswith("start-preflight-")
    assert call["timeout_seconds"] == 10.0


def test_manifest_start_gate_preserves_mcp_failure_code() -> None:
    gate = McpManifestStartGate(  # type: ignore[arg-type]
        PreflightFixture(McpClientError("MCP_MANIFEST_MISMATCH")),
        policy_snapshot_id="policy-v1",
        seed_registry_id="seed-v1",
    )

    with pytest.raises(FirstProposalPreflightUnavailableError) as caught:
        gate.validate(project_id="project-1")

    assert caught.value.reason_codes == ["MCP_MANIFEST_MISMATCH"]
