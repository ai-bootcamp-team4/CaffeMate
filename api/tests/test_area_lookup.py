import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.areas.service import AreaLookupService
from app.areas.token import AreaSelectionTokenSigner
from app.domain.errors import ContractValidationError
from app.mcp.client import McpCallOutcome


class FakeMcpClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def call_tool(self, **kwargs: Any) -> McpCallOutcome:
        self.calls.append(kwargs)
        project_id = kwargs["venture_project_id"]
        return McpCallOutcome(
            request_id="request-1",
            tool_name="resolve_area",
            tool_version="1.0.0",
            status="OK",
            is_complete=True,
            structured_content={
                "data": [
                    {
                        "administrative_code": "1144012300",
                        "display_name": "서울특별시 마포구 망원동",
                        "boundary_version": "JUSO_LIVE_UNVERSIONED",
                        "match_kind": "AMBIGUOUS",
                    }
                ],
                "missing_fields": [],
                "source_trace": [],
                "project_id": project_id,
            },
        )


def test_search_returns_signed_legal_dong_identity_without_claiming_admin_mapping() -> None:
    now = datetime(2026, 8, 22, tzinfo=UTC)
    client = FakeMcpClient()
    service = AreaLookupService(
        client,
        token_signer=AreaSelectionTokenSigner(secret="s" * 32, clock=lambda: now),
        policy_snapshot_id="policy-v1",
    )

    result = asyncio.run(
        service.search(project_id="project-1", query=" 서울 마포구 망원동 ", limit=10)
    )

    assert result.query == "서울 마포구 망원동"
    assert result.completeness == "UNVERIFIED"
    assert result.candidates[0].model_dump(exclude={"selection_token"}) == {
        "area_id": "legal-dong:1144012300",
        "scope_type": "LEGAL_DONG",
        "display_name": "서울특별시 마포구 망원동",
        "legal_dong_code": "1144012300",
        "administrative_dong_codes": [],
        "mapping_status": "UNVERIFIED",
        "source_revision": "JUSO_LIVE_UNVERSIONED",
        "boundary_version": None,
    }
    assert client.calls[0]["workflow_run_id"].startswith("area-lookup:")
    selected = service.resolve_selection(
        project_id="project-1",
        query="서울 마포구 망원동",
        selection_token=result.candidates[0].selection_token,
    )
    assert selected.area_id == "legal-dong:1144012300"


def test_selection_token_is_project_query_and_expiry_fenced() -> None:
    now = datetime(2026, 8, 22, tzinfo=UTC)
    clock_value = [now]
    service = AreaLookupService(
        FakeMcpClient(),
        token_signer=AreaSelectionTokenSigner(secret="s" * 32, clock=lambda: clock_value[0]),
        policy_snapshot_id="policy-v1",
    )
    result = asyncio.run(service.search(project_id="project-1", query="망원동", limit=10))
    token = result.candidates[0].selection_token

    with pytest.raises(ContractValidationError):
        service.resolve_selection(project_id="project-2", query="망원동", selection_token=token)
    with pytest.raises(ContractValidationError):
        service.resolve_selection(project_id="project-1", query="원천동", selection_token=token)

    clock_value[0] = now + timedelta(minutes=16)
    with pytest.raises(ContractValidationError):
        service.resolve_selection(project_id="project-1", query="망원동", selection_token=token)
