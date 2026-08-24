import asyncio
from enum import StrEnum
from typing import Any, Protocol

from pydantic import Field, ValidationError

from app.domain.errors import ContractValidationError
from app.domain.models import StrictModel
from app.mcp.client import McpCallOutcome, McpClientError
from app.workflows.failure_policy import StageExecutionFailurePolicy
from app.workflows.models import HeadFence, StageControl, StageDisposition
from app.workflows.stage_context import StageContext


class AreaMatchKind(StrEnum):
    EXACT = "EXACT"
    ALIAS = "ALIAS"
    CONTAINS = "CONTAINS"
    AMBIGUOUS = "AMBIGUOUS"


class AreaCandidate(StrictModel):
    administrative_code: str = Field(pattern=r"^[0-9]{8,10}$")
    display_name: str = Field(min_length=1)
    boundary_version: str = Field(min_length=1)
    match_kind: AreaMatchKind


class AreaResolutionOutput(StrictModel):
    query: str
    resolution_status: str
    selected: AreaCandidate | None
    candidates: list[AreaCandidate]
    mcp_status: str
    evidence_records: list[dict[str, Any]]
    missing_fields: list[str]
    conflicts: list[str]
    source_trace: list[dict[str, Any]]
    observed_at: str


class AreaMcpClient(Protocol):
    async def call_tool(
        self,
        *,
        venture_project_id: str,
        workflow_run_id: str,
        head: HeadFence,
        tool_name: str,
        arguments: dict[str, Any],
        traceparent: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> McpCallOutcome: ...


class AreaResolutionStageHandler:
    def __init__(self, mcp_client: AreaMcpClient) -> None:
        self._mcp_client = mcp_client

    def execute(self, context: StageContext) -> dict[str, object]:
        resolved = context.state.area
        if (
            resolved.resolution_status.value == "RESOLVED"
            and resolved.administrative_code
            and resolved.display_name
            and resolved.source_revision
        ):
            confirmed_candidate = AreaCandidate(
                administrative_code=resolved.administrative_code,
                display_name=resolved.display_name,
                boundary_version=resolved.boundary_version or resolved.source_revision,
                match_kind=AreaMatchKind.EXACT,
            )
            return self._result(
                control=StageControl(),
                output=AreaResolutionOutput(
                    query=context.state.founder.target_area_input.strip(),
                    resolution_status="RESOLVED",
                    selected=confirmed_candidate,
                    candidates=[confirmed_candidate],
                    mcp_status="STATE_CONFIRMED",
                    evidence_records=[],
                    missing_fields=list(resolved.unavailable_fields),
                    conflicts=[],
                    source_trace=[],
                    observed_at=context.state.updated_at.isoformat(),
                ),
            )
        query = context.state.founder.target_area_input.strip()
        try:
            outcome = asyncio.run(
                self._mcp_client.call_tool(
                    venture_project_id=context.project_id,
                    workflow_run_id=context.lease.workflow_run_id,
                    head=context.lease.head,
                    tool_name="resolve_area",
                    arguments={"query": query, "country_code": "KR", "limit": 10},
                )
            )
        except McpClientError as error:
            if not StageExecutionFailurePolicy.can_degrade(error):
                raise
            return self._result(
                control=StageControl(
                    disposition=StageDisposition.WAITING_FOR_HUMAN,
                    reason_codes=["AREA_SOURCE_UNAVAILABLE"],
                ),
                output=AreaResolutionOutput(
                    query=query,
                    resolution_status="UNAVAILABLE",
                    selected=None,
                    candidates=[],
                    mcp_status="UNAVAILABLE",
                    evidence_records=[],
                    missing_fields=["administrative_dong_mapping"],
                    conflicts=[],
                    source_trace=[],
                    observed_at=context.state.updated_at.isoformat(),
                ),
            )
        content = outcome.structured_content
        try:
            candidates = [AreaCandidate.model_validate(value) for value in content["data"]]
            common = {
                "query": query,
                "candidates": candidates,
                "mcp_status": outcome.status,
                "evidence_records": content["evidence_records"],
                "missing_fields": content["missing_fields"],
                "conflicts": content["conflicts"],
                "source_trace": content["source_trace"],
                "observed_at": content["observed_at"],
            }
        except (KeyError, TypeError, ValidationError) as error:
            raise ContractValidationError("resolve_area result is structurally invalid") from error

        if outcome.status != "OK":
            return self._result(
                control=StageControl(
                    disposition=StageDisposition.ABSTAIN,
                    reason_codes=[f"AREA_SOURCE_{outcome.status}"],
                ),
                output=AreaResolutionOutput(
                    **common,
                    resolution_status="UNAVAILABLE",
                    selected=None,
                ),
            )

        exact = [
            candidate for candidate in candidates if candidate.match_kind == AreaMatchKind.EXACT
        ]
        selected = exact[0] if len(exact) == 1 else None
        if selected is None and len(candidates) == 1:
            candidate = candidates[0]
            if candidate.match_kind != AreaMatchKind.AMBIGUOUS:
                selected = candidate
        if selected is not None:
            return self._result(
                control=StageControl(),
                output=AreaResolutionOutput(
                    **common,
                    resolution_status="RESOLVED",
                    selected=selected,
                ),
            )
        if candidates:
            return self._result(
                control=StageControl(
                    disposition=StageDisposition.WAITING_FOR_HUMAN,
                    reason_codes=["AREA_SELECTION_REQUIRED"],
                ),
                output=AreaResolutionOutput(
                    **common,
                    resolution_status="AMBIGUOUS",
                    selected=None,
                ),
            )
        return self._result(
            control=StageControl(
                disposition=StageDisposition.ABSTAIN,
                reason_codes=["AREA_NOT_FOUND"],
            ),
            output=AreaResolutionOutput(
                **common,
                resolution_status="UNAVAILABLE",
                selected=None,
            ),
        )

    @staticmethod
    def _result(
        *,
        control: StageControl,
        output: AreaResolutionOutput,
    ) -> dict[str, object]:
        return {
            "stage_control": control.model_dump(mode="json"),
            "area_resolution": output.model_dump(mode="json"),
        }
