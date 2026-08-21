import asyncio
import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import Field
from sqlalchemy import Engine, text

from app.domain.errors import (
    CandidateSelectionPreconditionError,
    PersistenceUnavailableError,
    ProjectNotFoundError,
)
from app.domain.models import StrictModel, VentureState
from app.mcp.client import McpCallOutcome, McpClientError
from app.workflows.models import HeadFence


class ProcedureType(StrEnum):
    BUSINESS_REGISTRATION = "BUSINESS_REGISTRATION"
    FOOD_SERVICE_REPORT = "FOOD_SERVICE_REPORT"
    FACILITY_REQUIREMENTS = "FACILITY_REQUIREMENTS"
    HYGIENE_EDUCATION = "HYGIENE_EDUCATION"
    SIGNAGE = "SIGNAGE"
    FIRE_SAFETY = "FIRE_SAFETY"


class PreparationGuideStatus(StrEnum):
    COMPLETE = "COMPLETE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"


class ProcedureRetrievalStatus(StrEnum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    STALE = "STALE"
    NOT_FOUND = "NOT_FOUND"
    ERROR = "ERROR"


class OfficialSourceTrace(StrictModel):
    source_id: str = Field(min_length=1, max_length=128)
    source_ref: str = Field(min_length=1, max_length=2000)
    data_date: date | None
    retrieved_at: datetime
    content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ProcedureStep(StrictModel):
    procedure_type: ProcedureType
    step_order: int = Field(ge=1)
    title: str = Field(min_length=1)
    required: bool
    authority: str = Field(min_length=1)
    source_date: date
    evidence_id: str = Field(min_length=1)


class ProcedureCoverage(StrictModel):
    procedure_type: ProcedureType
    status: ProcedureRetrievalStatus
    request_id: str | None = None
    steps: list[ProcedureStep]
    missing_fields: list[str]
    conflicts: list[str]
    error_codes: list[str]
    source_trace: list[OfficialSourceTrace]
    evidence_records: list[dict[str, Any]]


class PreparationGuide(StrictModel):
    project_id: str
    selection_id: str
    candidate_id: str
    candidate_type: str
    jurisdiction_code: str
    jurisdiction_display_name: str | None
    as_of: date
    status: PreparationGuideStatus
    procedures: list[ProcedureCoverage]
    source_trace: list[OfficialSourceTrace]
    evidence_records: list[dict[str, Any]]
    human_actions_only: bool = True
    external_submission_performed: bool = False
    generated_at: datetime


class ProcedureMcpClient(Protocol):
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


class PreparationGuideService:
    def __init__(
        self,
        engine: Engine,
        mcp_client: ProcedureMcpClient,
        *,
        now: Callable[[], datetime] | None = None,
        max_concurrency: int = 4,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("Preparation guide concurrency must be positive")
        self._engine = engine
        self._mcp_client = mcp_client
        self._now = now or (lambda: datetime.now(UTC))
        self._max_concurrency = max_concurrency

    async def get(
        self,
        *,
        project_id: str,
        selection_id: str,
        user_id: str,
    ) -> PreparationGuide:
        selected = self._load_selection(
            project_id=project_id,
            selection_id=selection_id,
            user_id=user_id,
        )
        state = VentureState.model_validate(selected["state_json"])
        if state.active_case_id != selected["candidate_id"]:
            raise CandidateSelectionPreconditionError(
                "Preparation guide requires the current selected candidate"
            )
        jurisdiction_code = state.area.administrative_code
        if state.area.resolution_status != "RESOLVED" or not jurisdiction_code:
            raise CandidateSelectionPreconditionError(
                "Preparation guide requires a resolved administrative area"
            )
        head = HeadFence(
            workflow_generation=selected["workflow_generation"],
            state_version=selected["state_version"],
            founder_snapshot_id=selected["founder_snapshot_id"],
            area_snapshot_id=selected["area_snapshot_id"],
            evidence_snapshot_id=selected["evidence_snapshot_id"],
            policy_snapshot_id=selected["policy_snapshot_id"],
            index_generation_id=selected["index_generation_id"],
            seed_registry_id=selected["seed_registry_id"],
        )
        generated_at = self._now()
        as_of = generated_at.date()
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def retrieve(procedure_type: ProcedureType) -> ProcedureCoverage:
            try:
                async with semaphore:
                    outcome = await self._mcp_client.call_tool(
                        venture_project_id=project_id,
                        workflow_run_id=f"preparation-{selection_id}",
                        head=head,
                        tool_name="get_official_procedure",
                        arguments={
                            "jurisdiction_code": jurisdiction_code,
                            "procedure_type": procedure_type.value,
                            "as_of": as_of.isoformat(),
                        },
                        timeout_seconds=30.0,
                    )
            except McpClientError as error:
                return ProcedureCoverage(
                    procedure_type=procedure_type,
                    status=ProcedureRetrievalStatus.ERROR,
                    steps=[],
                    missing_fields=[],
                    conflicts=[],
                    error_codes=[error.mcp_code],
                    source_trace=[],
                    evidence_records=[],
                )
            content = outcome.structured_content
            evidence_records = list(content.get("evidence_records", []))
            evidence_ids = {
                value.get("evidence_id")
                for value in evidence_records
                if isinstance(value, dict)
            }
            unsupported_step_count = 0
            steps: list[ProcedureStep] = []
            for value in content.get("data", []):
                if not isinstance(value, dict):
                    continue
                if value.get("evidence_id") not in evidence_ids:
                    unsupported_step_count += 1
                    continue
                steps.append(ProcedureStep(procedure_type=procedure_type, **value))
            steps.sort(key=lambda value: (value.step_order, value.title))
            error_codes = list(content.get("error_codes", []))
            status = ProcedureRetrievalStatus(outcome.status)
            if unsupported_step_count:
                error_codes.append("PROCEDURE_EVIDENCE_MISSING")
                status = ProcedureRetrievalStatus.PARTIAL
            return ProcedureCoverage(
                procedure_type=procedure_type,
                status=status,
                request_id=outcome.request_id,
                steps=steps,
                missing_fields=list(content.get("missing_fields", [])),
                conflicts=list(content.get("conflicts", [])),
                error_codes=sorted(set(error_codes)),
                source_trace=[
                    OfficialSourceTrace.model_validate(value)
                    for value in content.get("source_trace", [])
                ],
                evidence_records=evidence_records,
            )

        outcomes = await asyncio.gather(*(retrieve(value) for value in ProcedureType))
        source_trace = self._collect_source_trace(outcomes)
        candidate = selected["candidate_json"]
        return PreparationGuide(
            project_id=project_id,
            selection_id=selection_id,
            candidate_id=selected["candidate_id"],
            candidate_type=str(candidate.get("case_type", "UNKNOWN")),
            jurisdiction_code=jurisdiction_code,
            jurisdiction_display_name=state.area.display_name,
            as_of=as_of,
            status=self._guide_status(outcomes),
            procedures=outcomes,
            source_trace=source_trace,
            evidence_records=self._collect_evidence_records(outcomes),
            generated_at=generated_at,
        )

    def _load_selection(
        self,
        *,
        project_id: str,
        selection_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT cs.selection_id, cs.candidate_id, cs.candidate_json,
                           s.state_json, h.workflow_generation, h.state_version,
                           h.founder_snapshot_id, h.area_snapshot_id,
                           h.evidence_snapshot_id, h.policy_snapshot_id,
                           h.index_generation_id, h.seed_registry_id
                    FROM candidate_selections cs
                    JOIN venture_projects p ON p.project_id=cs.project_id
                    JOIN venture_states s
                      ON s.project_id=p.project_id
                     AND s.state_version=p.current_state_version
                    JOIN project_heads h ON h.project_id=p.project_id
                    WHERE cs.project_id=:project_id
                      AND cs.selection_id=:selection_id
                      AND cs.owner_user_id=:user_id
                      AND p.owner_user_id=:user_id
                    """
                ),
                {
                    "project_id": project_id,
                    "selection_id": selection_id,
                    "user_id": user_id,
                },
            ).mappings().one_or_none()
        if row is None:
            raise ProjectNotFoundError("Candidate selection was not found")
        value = dict(row)
        for field in ("candidate_json", "state_json"):
            if isinstance(value[field], str):
                value[field] = json.loads(value[field])
        return value

    @staticmethod
    def _guide_status(outcomes: list[ProcedureCoverage]) -> PreparationGuideStatus:
        if all(value.status == "OK" and value.steps for value in outcomes):
            return PreparationGuideStatus.COMPLETE
        if all(value.status in {"ERROR", "NOT_FOUND"} and not value.steps for value in outcomes):
            return PreparationGuideStatus.UNAVAILABLE
        return PreparationGuideStatus.REVIEW_REQUIRED

    @staticmethod
    def _collect_source_trace(
        outcomes: list[ProcedureCoverage],
    ) -> list[OfficialSourceTrace]:
        unique: dict[tuple[str, str, str], OfficialSourceTrace] = {}
        for outcome in outcomes:
            for source in outcome.source_trace:
                key = (
                    source.source_id,
                    source.source_ref,
                    source.content_digest,
                )
                unique[key] = source
        return [unique[key] for key in sorted(unique)]

    @staticmethod
    def _collect_evidence_records(
        outcomes: list[ProcedureCoverage],
    ) -> list[dict[str, Any]]:
        unique: dict[str, dict[str, Any]] = {}
        for outcome in outcomes:
            for evidence in outcome.evidence_records:
                evidence_id = evidence.get("evidence_id")
                if isinstance(evidence_id, str) and evidence_id:
                    unique[evidence_id] = evidence
        return [unique[evidence_id] for evidence_id in sorted(unique)]


class UnavailablePreparationGuideService:
    async def get(self, **_: Any) -> PreparationGuide:
        raise PersistenceUnavailableError("Preparation guide dependencies are unavailable")
