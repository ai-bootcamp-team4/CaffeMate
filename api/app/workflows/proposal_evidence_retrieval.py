"""MCP evidence retrieval and official-RAG evidence normalization for first proposals."""

import asyncio
import hashlib
from collections.abc import Callable
from copy import deepcopy
from datetime import date, datetime
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

import rfc8785

from app.contracts.schema_registry import ContractRegistry
from app.domain.models import CafeTypePreference, VentureState
from app.mcp.client import McpCallOutcome, McpClientError
from app.workflows.franchise_grounding import verified_franchise_brand_ids
from app.workflows.models import HeadFence

FRANCHISE_RAG_QUERIES: tuple[str, ...] = (
    "컴포즈커피 개인 가맹점 창업 신청 가맹 모집 가능 여부 공식 안내",
    "컴포즈커피 공식 창업 비용 10평 15평 포함 제외 항목",
    "메가MGC커피 개인 가맹점 창업 신청 가맹 모집 가능 여부 공식 안내",
    "메가MGC커피 공식 창업 비용 10평 포함 제외 항목",
    "이디야커피 개인 가맹점 창업 신청 가맹 모집 가능 여부 공식 안내",
    "이디야커피 공식 창업 비용 가맹비 월 로열티 포함 제외 항목",
)
SEOUL_TIMEZONE = ZoneInfo("Asia/Seoul")


class ProposalMcp(Protocol):
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


class ProposalEvidenceRetriever:
    def __init__(
        self,
        *,
        mcp: ProposalMcp,
        contracts: ContractRegistry,
        now: Callable[[], datetime],
    ) -> None:
        self._mcp = mcp
        self._contracts = contracts
        self._now = now

    async def retrieve(
        self,
        *,
        state: VentureState,
        head: HeadFence,
        workflow_run_id: str,
    ) -> list[McpCallOutcome]:
        # Domestic startup references are evaluated against Seoul's calendar date,
        # not the server's UTC date.
        as_of = self._now().astimezone(SEOUL_TIMEZONE).date().isoformat()
        calls: list[tuple[str, dict[str, Any]]] = []
        if state.area.administrative_code and state.area.boundary_version:
            calls.extend(
                [
                    (
                        "get_area_profile",
                        {
                            "administrative_code": state.area.administrative_code,
                            "boundary_version": state.area.boundary_version,
                            "as_of": as_of,
                        },
                    ),
                    (
                        "get_property_reference",
                        {
                            "administrative_code": state.area.administrative_code,
                            "boundary_version": state.area.boundary_version,
                            "as_of": as_of,
                        },
                    ),
                    (
                        "search_cafe_observations",
                        {
                            "administrative_code": state.area.administrative_code,
                            "boundary_version": state.area.boundary_version,
                            "as_of": as_of,
                            "metrics": [
                                "CAFE_COUNT",
                                "OPEN_COUNT",
                                "CLOSE_COUNT",
                                "CLOSURE_RATE",
                                "ESTIMATED_SALES",
                                "FOOT_TRAFFIC",
                                "RESIDENT_POPULATION",
                                "WORKER_POPULATION",
                                "AGE_DISTRIBUTION",
                            ],
                        },
                    ),
                ]
            )
        if state.founder.cafe_type_preference != CafeTypePreference.FRANCHISE_ONLY:
            calls.append(
                (
                    "get_cost_reference",
                    {
                        "reference_types": ["MINIMUM_WAGE"],
                        "as_of": as_of,
                    },
                )
            )
        if state.founder.cafe_type_preference != CafeTypePreference.INDEPENDENT_ONLY:
            calls.append(
                (
                    "list_franchise_universe",
                    {"business_category": "CAFE", "as_of": as_of},
                )
            )
        calls.append(
            (
                "retrieve_official_documents",
                {
                    "query": (
                        f"{state.founder.target_area_input} 카페 창업 비용 인허가 "
                        "프랜차이즈 정보공개서"
                    ),
                    "source_families": ["GOVERNMENT_GUIDE"],
                    "as_of": as_of,
                    "limit": 10,
                },
            )
        )
        if state.founder.cafe_type_preference != CafeTypePreference.INDEPENDENT_ONLY:
            calls.extend(
                (
                    "retrieve_official_documents",
                    {
                        "query": query,
                        "source_families": ["COMPANY_OFFICIAL_FRANCHISE"],
                        "as_of": as_of,
                        "limit": 3,
                    },
                )
                for query in FRANCHISE_RAG_QUERIES
            )
        # One failed read must remain an observable ERROR action instead of
        # cancelling successful evidence retrieval and all downstream roles.
        outcomes = await asyncio.gather(
            *[
                self._retrieve_one(
                    call_index=index,
                    state=state,
                    head=head,
                    workflow_run_id=workflow_run_id,
                    tool_name=tool_name,
                    arguments=arguments,
                )
                for index, (tool_name, arguments) in enumerate(calls)
            ]
        )
        verified_brand_ids = verified_franchise_brand_ids(outcomes)
        if not verified_brand_ids:
            return outcomes
        disclosure_outcomes = await asyncio.gather(
            *[
                self._retrieve_one(
                    call_index=len(calls) + index,
                    state=state,
                    head=head,
                    workflow_run_id=workflow_run_id,
                    tool_name="get_franchise_disclosure",
                    arguments={"brand_id": brand_id, "as_of": as_of},
                )
                for index, brand_id in enumerate(verified_brand_ids)
            ]
        )
        return [*outcomes, *disclosure_outcomes]

    async def _retrieve_one(
        self,
        *,
        call_index: int,
        state: VentureState,
        head: HeadFence,
        workflow_run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> McpCallOutcome:
        try:
            return await self._mcp.call_tool(
                venture_project_id=state.project_id,
                workflow_run_id=workflow_run_id,
                head=head,
                tool_name=tool_name,
                arguments=arguments,
            )
        except McpClientError as error:
            request_digest = hashlib.sha256(
                f"{workflow_run_id}:{call_index}:{tool_name}".encode()
            ).hexdigest()[:20]
            request_id = f"failed-mcp-{request_digest}"
            tool_version = self._contracts.mcp_tool_version(tool_name)
            content = {
                "schema_version": "1.0.0",
                "request_id": request_id,
                "tool_name": tool_name,
                "tool_version": tool_version,
                "status": "ERROR",
                "project_id": state.project_id,
                "evidence_records": [],
                "missing_fields": [f"{tool_name}_result"],
                "conflicts": [],
                "source_trace": [],
                "error_codes": [error.mcp_code],
                "observed_at": self._now().isoformat().replace("+00:00", "Z"),
                "data": [],
            }
            self._contracts.validate_mcp_tool_result(tool_name, content)
            return McpCallOutcome(
                request_id=request_id,
                tool_name=tool_name,
                tool_version=tool_version,
                status="ERROR",
                is_complete=False,
                structured_content=content,
            )

    def with_official_rag_evidence(self, outcome: McpCallOutcome) -> McpCallOutcome:
        """Normalize official RAG hits only when source identity and anchors are complete."""

        if outcome.tool_name != "retrieve_official_documents":
            return outcome
        content = outcome.structured_content
        existing = content.get("evidence_records")
        if isinstance(existing, list) and existing:
            return outcome

        traces = [
            trace
            for trace in content.get("source_trace", [])
            if isinstance(trace, dict)
            and isinstance(trace.get("source_ref"), str)
            and isinstance(trace.get("data_date"), str)
            and isinstance(trace.get("content_digest"), str)
        ]
        observed_at = content.get("observed_at")
        project_id = content.get("project_id")
        if not isinstance(observed_at, str) or not isinstance(project_id, str):
            return outcome

        records: list[dict[str, Any]] = []
        for hit in content.get("data", []):
            if not isinstance(hit, dict):
                continue
            required = {
                key: hit.get(key)
                for key in (
                    "document_revision_id",
                    "title",
                    "anchor",
                    "excerpt",
                    "source_date",
                    "evidence_id",
                )
            }
            if not all(isinstance(value, str) and value for value in required.values()):
                continue
            anchor = cast(str, required["anchor"])
            source_date = cast(str, required["source_date"])
            matching_traces = [
                trace
                for trace in traces
                if anchor.startswith(trace["source_ref"])
                and source_date == trace["data_date"]
            ]
            if len(matching_traces) != 1:
                continue
            trace = matching_traces[0]
            excerpt = cast(str, required["excerpt"])
            source_family = hit.get("source_family")
            claim_type = hit.get("claim_type")
            brand_id = hit.get("brand_id")
            is_company_franchise = source_family == "COMPANY_OFFICIAL_FRANCHISE"
            record = {
                "schema_version": "2.0.0",
                "evidence_id": f"official-rag:{required['evidence_id']}",
                "project_id": project_id,
                "claim_type": (
                    claim_type
                    if isinstance(claim_type, str) and claim_type
                    else "OFFICIAL_STARTUP_GUIDANCE"
                ),
                "metric": brand_id if isinstance(brand_id, str) and brand_id else None,
                "value": {"kind": "STRING", "value": excerpt},
                "value_kind": "EVIDENCED_FACT",
                "unit": None,
                "geographic_scope": {
                    "scope_type": "NATIONAL",
                    "scope_id": "KR",
                    "boundary_version": None,
                },
                "source": {
                    "title": required["title"],
                    "source_ref": trace["source_ref"],
                    "authority": (
                        "COMPANY_OFFICIAL" if is_company_franchise else "PRIMARY_OFFICIAL"
                    ),
                    "source_type": "WEB",
                    **(
                        {"source_family": source_family}
                        if isinstance(source_family, str) and source_family
                        else {}
                    ),
                    "published_or_data_date": source_date,
                    "source_observed_at": observed_at,
                    "document_version": required["document_revision_id"],
                    "checksum": trace["content_digest"],
                },
                "original_anchor": {
                    "anchor_type": "SECTION",
                    "locator": anchor,
                    "excerpt_hash": content_digest(excerpt),
                },
                "freshness_status": self._freshness_status(source_date),
                "conflict_status": "NONE",
                "retrieved_at": observed_at,
                "missing_context": (
                    ["AREA_AVAILABILITY_REQUIRES_HEADQUARTERS_CONFIRMATION"]
                    if claim_type == "FRANCHISE_INDIVIDUAL_ELIGIBILITY"
                    else []
                ),
                "durable_evidence_refs": [
                    trace["source_ref"],
                    required["document_revision_id"],
                    required["evidence_id"],
                ],
            }
            self._contracts.validate_evidence_record(record)
            records.append(record)

        if not records:
            return outcome
        enriched = deepcopy(content)
        enriched["evidence_records"] = deduplicate_evidence(records)
        return outcome.model_copy(update={"structured_content": enriched})

    def _freshness_status(self, source_date: str) -> str:
        try:
            age = self._now().date() - date.fromisoformat(source_date)
        except ValueError:
            return "UNKNOWN"
        if age.days < 0:
            return "UNKNOWN"
        return "FRESH" if age.days <= 365 else "STALE"


def deduplicate_evidence(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        evidence_id = record.get("evidence_id")
        if isinstance(evidence_id, str):
            by_id[evidence_id] = record
    return [by_id[key] for key in sorted(by_id)]


def content_digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(rfc8785.dumps(value)).hexdigest()}"
