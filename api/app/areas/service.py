from typing import Protocol
from uuid import uuid4

from app.areas.models import AreaSearchCandidate, AreaSearchResult
from app.areas.token import AreaSelectionTokenError, AreaSelectionTokenSigner
from app.domain.errors import ContractValidationError, ExternalExecutionUnavailableError
from app.domain.models import (
    AreaIdentity,
    AreaMappingStatus,
    AreaScopeType,
    CandidateSetCompleteness,
)
from app.mcp.client import McpCallOutcome
from app.workflows.models import HeadFence


class AreaLookupMcpClient(Protocol):
    async def call_tool(
        self,
        *,
        venture_project_id: str,
        workflow_run_id: str,
        head: HeadFence,
        tool_name: str,
        arguments: dict[str, object],
        timeout_seconds: float = 30.0,
    ) -> McpCallOutcome: ...


class AreaLookupService:
    def __init__(
        self,
        mcp_client: AreaLookupMcpClient,
        *,
        token_signer: AreaSelectionTokenSigner,
        policy_snapshot_id: str,
    ) -> None:
        self._mcp_client = mcp_client
        self._token_signer = token_signer
        self._policy_snapshot_id = policy_snapshot_id

    async def search(
        self,
        *,
        project_id: str,
        query: str,
        limit: int,
    ) -> AreaSearchResult:
        normalized_query = " ".join(query.split())
        lookup_head = HeadFence(
            workflow_generation=1,
            state_version=1,
            founder_snapshot_id=None,
            area_snapshot_id=None,
            evidence_snapshot_id=None,
            policy_snapshot_id=self._policy_snapshot_id,
            index_generation_id=None,
            seed_registry_id=None,
        )
        outcome = await self._mcp_client.call_tool(
            venture_project_id=project_id,
            workflow_run_id=f"area-lookup:{uuid4()}",
            head=lookup_head,
            tool_name="resolve_area",
            arguments={"query": normalized_query, "country_code": "KR", "limit": limit},
        )
        content = outcome.structured_content
        raw_candidates = content.get("data")
        if not isinstance(raw_candidates, list):
            raise ContractValidationError("Area lookup result has no candidate list")
        candidates: list[AreaSearchCandidate] = []
        for raw in raw_candidates:
            if not isinstance(raw, dict):
                raise ContractValidationError("Area lookup candidate is invalid")
            legal_code = raw.get("administrative_code")
            display_name = raw.get("display_name")
            source_revision = raw.get("boundary_version")
            if (
                not isinstance(legal_code, str)
                or not isinstance(display_name, str)
                or not isinstance(source_revision, str)
            ):
                raise ContractValidationError("Area lookup candidate identity is incomplete")
            area = AreaIdentity(
                area_id=f"legal-dong:{legal_code}",
                scope_type=AreaScopeType.LEGAL_DONG,
                display_name=display_name,
                legal_dong_code=legal_code,
                administrative_dong_codes=[],
                mapping_status=AreaMappingStatus.UNVERIFIED,
                source_revision=source_revision,
                boundary_version=None,
            )
            candidates.append(
                AreaSearchCandidate(
                    **area.model_dump(mode="python"),
                    selection_token=self._token_signer.issue(
                        venture_project_id=project_id,
                        query=normalized_query,
                        area=area,
                    ),
                )
            )
        missing_fields = content.get("missing_fields", [])
        source_trace = content.get("source_trace", [])
        return AreaSearchResult(
            query=normalized_query,
            status=outcome.status,
            completeness=CandidateSetCompleteness.UNVERIFIED,
            candidates=candidates,
            missing_fields=list(missing_fields) if isinstance(missing_fields, list) else [],
            source_trace=list(source_trace) if isinstance(source_trace, list) else [],
        )

    def resolve_selection(
        self,
        *,
        project_id: str,
        query: str,
        selection_token: str | None,
    ) -> AreaIdentity:
        if not selection_token:
            raise ContractValidationError("A verified area selection is required")
        try:
            claims = self._token_signer.verify(selection_token)
        except AreaSelectionTokenError as error:
            raise ContractValidationError("Area selection is invalid or expired") from error
        normalized_query = " ".join(query.split())
        selected_display_name = " ".join(claims.area.display_name.split())
        if claims.venture_project_id != project_id or normalized_query not in {
            claims.query,
            selected_display_name,
        }:
            raise ContractValidationError("Area selection does not match this project and query")
        return claims.area


class UnavailableAreaLookupService:
    async def search(self, **_: object) -> AreaSearchResult:
        raise ExternalExecutionUnavailableError("AREA_LOOKUP_UNAVAILABLE")

    def resolve_selection(
        self,
        *,
        project_id: str,
        query: str,
        selection_token: str | None,
    ) -> AreaIdentity | None:
        if selection_token:
            raise ExternalExecutionUnavailableError("AREA_LOOKUP_UNAVAILABLE")
        return None
