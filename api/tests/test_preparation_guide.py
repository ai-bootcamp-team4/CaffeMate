import asyncio
from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.auth import IdentityVerifier
from app.domain.errors import CandidateSelectionPreconditionError
from app.main import create_app
from app.mcp.client import McpCallOutcome, McpClientError
from app.selections.preparation import (
    PreparationGuideService,
    PreparationGuideStatus,
    ProcedureType,
)


def selection_row() -> dict[str, Any]:
    return {
        "selection_id": "selection-1",
        "candidate_id": "candidate-1",
        "candidate_json": {"case_type": "INDEPENDENT"},
        "state_json": {
            "schema_version": "1.0.0",
            "project_id": "project-1",
            "user_id": "user-1",
            "state_version": 2,
            "status": "WAITING_FOR_HUMAN",
            "active_case_id": "candidate-1",
            "founder": {
                "target_area_input": "수원 아주대 부근",
                "own_funds_krw": 50_000_000,
                "borrowing_intent": "UNDECIDED",
                "cafe_type_preference": "OPEN_TO_BOTH",
                "operation_mode": "DIRECT_FULL_TIME",
                "preferences": [],
                "avoidances": [],
            },
            "area": {
                "resolution_status": "RESOLVED",
                "administrative_code": "4111759000",
                "display_name": "수원시 영통구 원천동",
                "boundary_version": "2026-08",
                "coverage_profile": "R2_REGIONAL_CONNECTOR",
                "evidence_ids": ["area-evidence-1"],
                "unavailable_fields": [],
            },
            "venture_cases": [
                {
                    "case_id": "candidate-1",
                    "case_type": "INDEPENDENT",
                    "maturity": "CANDIDATE",
                    "status": "SELECTED",
                    "display_name": "소형 개인카페",
                    "franchise_eligibility": "NOT_APPLICABLE",
                    "confirmed_claim_ids": [],
                    "assumption_ids": [],
                    "missing_fields": [],
                }
            ],
            "assumption_ids": [],
            "conflict_ids": [],
            "updated_at": "2026-08-21T10:00:00Z",
        },
        "workflow_generation": 1,
        "state_version": 2,
        "founder_snapshot_id": "founder-2",
        "area_snapshot_id": "area-2",
        "evidence_snapshot_id": "evidence-1",
        "policy_snapshot_id": "policy-1",
        "index_generation_id": "index-1",
        "seed_registry_id": "seed-1",
    }


class FakeMappings:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def one_or_none(self) -> dict[str, Any] | None:
        return self._row


class FakeResult:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def mappings(self) -> FakeMappings:
        return FakeMappings(self._row)


class FakeConnection:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, *_: object, **__: object) -> FakeResult:
        return FakeResult(self._row)


class FakeEngine:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def connect(self) -> FakeConnection:
        return FakeConnection(self._row)


class ProcedureMcpFixture:
    def __init__(
        self,
        failures: set[ProcedureType] | None = None,
        omit_evidence: set[ProcedureType] | None = None,
    ) -> None:
        self.failures = failures or set()
        self.omit_evidence = omit_evidence or set()
        self.calls: list[dict[str, Any]] = []

    async def call_tool(self, **kwargs: Any) -> McpCallOutcome:
        self.calls.append(kwargs)
        procedure_type = ProcedureType(kwargs["arguments"]["procedure_type"])
        if procedure_type in self.failures:
            raise McpClientError("MCP_SERVER_UNAVAILABLE")
        request_id = f"request-{procedure_type.value.lower()}"
        evidence_id = f"evidence-{procedure_type.value.lower()}"
        return McpCallOutcome(
            request_id=request_id,
            tool_name="get_official_procedure",
            tool_version="1.0.0",
            status="OK",
            is_complete=True,
            traceparent="00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
            structured_content={
                "status": "OK",
                "data": [
                    {
                        "step_order": 1,
                        "title": f"{procedure_type.value} 공식 확인",
                        "required": True,
                        "authority": "관할 행정기관",
                        "source_date": "2026-08-21",
                        "evidence_id": evidence_id,
                    }
                ],
                "missing_fields": [],
                "conflicts": [],
                "error_codes": [],
                "evidence_records": []
                if procedure_type in self.omit_evidence
                else [
                    {
                        "schema_version": "2.0.0",
                        "evidence_id": evidence_id,
                        "project_id": "project-1",
                        "claim_type": f"OFFICIAL_PROCEDURE_{procedure_type.value}",
                        "value": {
                            "kind": "STRING",
                            "value": f"{procedure_type.value} 공식 확인",
                        },
                        "value_kind": "EVIDENCED_FACT",
                        "unit": None,
                        "geographic_scope": {
                            "country_code": "KR",
                            "administrative_code": "4111759000",
                            "display_name": "수원시 영통구 원천동",
                        },
                        "source": {
                            "title": "관할 행정기관 공식 안내",
                            "source_ref": "https://official.example/procedure",
                            "authority": "PRIMARY_OFFICIAL",
                            "source_type": "WEB",
                            "published_or_data_date": "2026-08-21",
                            "source_observed_at": "2026-08-21T10:00:00Z",
                            "document_version": "2026-08-21",
                            "checksum": "sha256:" + "a" * 64,
                        },
                        "original_anchor": {
                            "anchor_type": "SECTION",
                            "locator": procedure_type.value,
                            "excerpt_hash": None,
                        },
                        "freshness_status": "FRESH",
                        "conflict_status": "NONE",
                        "retrieved_at": "2026-08-21T10:00:00Z",
                        "missing_context": [],
                        "durable_evidence_refs": [
                            f"official-procedure-{procedure_type.value.lower()}"
                        ],
                    }
                ],
                "source_trace": [
                    {
                        "source_id": f"source-{procedure_type.value.lower()}",
                        "source_ref": "https://official.example/procedure",
                        "data_date": "2026-08-21",
                        "retrieved_at": "2026-08-21T10:00:00Z",
                        "content_digest": "sha256:" + "a" * 64,
                    }
                ],
            },
        )


def service(row: dict[str, Any], mcp: ProcedureMcpFixture) -> PreparationGuideService:
    return PreparationGuideService(
        FakeEngine(row),  # type: ignore[arg-type]
        mcp,
        now=lambda: datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
        max_concurrency=2,
    )


def test_builds_all_official_procedure_groups_without_external_submission() -> None:
    mcp = ProcedureMcpFixture()
    guide = asyncio.run(
        service(selection_row(), mcp).get(
            project_id="project-1",
            selection_id="selection-1",
            user_id="user-1",
        )
    )

    assert guide.status == PreparationGuideStatus.COMPLETE
    assert guide.as_of == date(2026, 8, 21)
    assert guide.jurisdiction_code == "4111759000"
    assert {value.procedure_type for value in guide.procedures} == set(ProcedureType)
    assert len(mcp.calls) == 6
    assert all(call["tool_name"] == "get_official_procedure" for call in mcp.calls)
    assert all(call["arguments"]["as_of"] == "2026-08-21" for call in mcp.calls)
    assert guide.human_actions_only is True
    assert guide.external_submission_performed is False
    assert len(guide.source_trace) == 6
    assert len(guide.evidence_records) == 6


def test_preserves_failed_procedure_as_review_required_instead_of_empty_success() -> None:
    mcp = ProcedureMcpFixture({ProcedureType.FIRE_SAFETY})
    guide = asyncio.run(
        service(selection_row(), mcp).get(
            project_id="project-1",
            selection_id="selection-1",
            user_id="user-1",
        )
    )

    assert guide.status == PreparationGuideStatus.REVIEW_REQUIRED
    fire = next(
        value
        for value in guide.procedures
        if value.procedure_type == ProcedureType.FIRE_SAFETY
    )
    assert fire.status == "ERROR"
    assert fire.steps == []
    assert fire.error_codes == ["MCP_SERVER_UNAVAILABLE"]


def test_drops_procedure_step_without_linked_evidence_record() -> None:
    mcp = ProcedureMcpFixture(omit_evidence={ProcedureType.HYGIENE_EDUCATION})
    guide = asyncio.run(
        service(selection_row(), mcp).get(
            project_id="project-1",
            selection_id="selection-1",
            user_id="user-1",
        )
    )
    hygiene = next(
        value
        for value in guide.procedures
        if value.procedure_type == ProcedureType.HYGIENE_EDUCATION
    )
    assert hygiene.status == "PARTIAL"
    assert hygiene.steps == []
    assert hygiene.error_codes == ["PROCEDURE_EVIDENCE_MISSING"]
    assert guide.status == PreparationGuideStatus.REVIEW_REQUIRED


@pytest.mark.parametrize("mutation", ["candidate", "area"])
def test_requires_current_selection_and_resolved_area(mutation: str) -> None:
    row = deepcopy(selection_row())
    if mutation == "candidate":
        row["state_json"]["active_case_id"] = "another-candidate"
    else:
        row["state_json"]["area"]["resolution_status"] = "UNRESOLVED"
        row["state_json"]["area"]["administrative_code"] = None

    with pytest.raises(CandidateSelectionPreconditionError):
        asyncio.run(
            service(row, ProcedureMcpFixture()).get(
                project_id="project-1",
                selection_id="selection-1",
                user_id="user-1",
            )
        )


class FixedIdentity(IdentityVerifier):
    def verify(self, bearer_token: str) -> str:
        assert bearer_token == "valid-token"
        return "user-1"


class PreparationApiFixture:
    def __init__(self, guide: Any) -> None:
        self.guide = guide
        self.calls: list[dict[str, str]] = []

    async def get(self, **kwargs: str) -> Any:
        self.calls.append(kwargs)
        return self.guide


def test_public_api_is_owner_authenticated_and_returns_typed_guide() -> None:
    guide = asyncio.run(
        service(selection_row(), ProcedureMcpFixture()).get(
            project_id="project-1",
            selection_id="selection-1",
            user_id="user-1",
        )
    )
    fixture = PreparationApiFixture(guide)
    with TestClient(
        create_app(
            identity_verifier=FixedIdentity(),
            preparation_guide_service=fixture,  # type: ignore[arg-type]
        )
    ) as client:
        response = client.get(
            "/v1/projects/project-1/candidate-selections/selection-1/preparation-guide",
            headers={"Authorization": "Bearer valid-token"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETE"
    assert response.json()["external_submission_performed"] is False
    assert fixture.calls == [
        {
            "project_id": "project-1",
            "selection_id": "selection-1",
            "user_id": "user-1",
        }
    ]
