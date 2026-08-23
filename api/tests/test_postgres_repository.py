import hashlib
import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import rfc8785
from sqlalchemy import Engine, create_engine, text
from testcontainers.community.postgres import PostgresContainer
from worker.agent_cleanup import AgentSessionCleanupConsumer, CleanupOutcome
from worker.dead_letter import (
    DeadLetterOperationError,
    DeadLetterOperations,
    ReprocessDeadLetterRequest,
)
from worker.outbox import OutboxPublisher, PostgresOutboxRepository
from worker.pubsub import PubSubDelivery
from worker.runtime import DeliveryOutcome, DurableWorker

from app.agents.runtime import PostgresAgentCleanupSink
from app.candidates.seed_registry import IndependentSeedRegistry
from app.documents.extraction import DocumentExtractionService
from app.documents.service import DocumentService
from app.documents.storage import StoredObject
from app.domain.errors import (
    ContractValidationError,
    FeedbackPreconditionError,
    FeedbackPreviewNotFoundError,
    IdempotencyKeyReusedError,
    ProjectNotFoundError,
    ResultNotFoundError,
    StateVersionConflictError,
)
from app.domain.models import (
    BorrowingIntent,
    CafeTypePreference,
    FounderState,
    OperationMode,
    Project,
)
from app.evidence.models import EvidenceRefreshRequest
from app.evidence.refresh import EvidenceRefreshService
from app.feedback.models import FeedbackPreviewStatus
from app.feedback.postgres_repository import PostgresFeedbackRepository
from app.feedback.service import FeedbackService
from app.main import create_app
from app.mcp.client import McpCallOutcome, McpClientError
from app.mcp.preflight import McpPreflightReport
from app.migrations import apply_migrations
from app.projects.postgres_repository import PostgresProjectRepository
from app.projects.service import ProjectService
from app.results.postgres_repository import PostgresResultRepository
from app.results.service import ResultService
from app.verification.documents import DocumentStorageCanary
from app.verification.first_proposal import PostgresFirstProposalCanaryCleaner
from app.workflows.area_resolution import AreaResolutionStageHandler
from app.workflows.calculate_gate_rank import CalculateGateRankStageHandler
from app.workflows.candidate_audit import CandidateAuditStageHandler
from app.workflows.candidate_inputs import (
    FranchiseEligibilityStageHandler,
    IndependentSeedStageHandler,
)
from app.workflows.claim_plan import ClaimPlanStageHandler
from app.workflows.commit_result import CommitResultStageHandler
from app.workflows.evidence_assess import EvidenceAssessStageHandler
from app.workflows.evidence_freeze import EvidenceFreezeStageHandler
from app.workflows.evidence_plan import EvidencePlanStageHandler
from app.workflows.evidence_retrieval import EvidenceRetrievalStageHandler
from app.workflows.execution_repository import LEASE_SECONDS, PostgresStageExecutionRepository
from app.workflows.first_proposal import FirstProposalStage
from app.workflows.models import (
    CheckpointOutcome,
    FailureOutcome,
    StageFailure,
    StageLease,
    WorkflowCode,
    WorkflowRun,
    WorkflowStatus,
)
from app.workflows.postgres_repository import PostgresWorkflowRepository
from app.workflows.proposal import ProposalStageHandler
from app.workflows.service import WorkflowService
from app.workflows.stage_context import PostgresStageContextRepository
from app.workflows.stage_router import (
    FirstProposalStageHandler,
    FirstProposalStageRouter,
)
from tests.test_agent_boundary import evidence_record
from tests.test_candidate_audit_stage import audit_result
from tests.test_proposal_stages import proposal_result


class FixedIdentityVerifier:
    def verify(self, bearer_token: str) -> str:
        assert bearer_token == "valid-token"
        return "user-1"


class ConfiguredExternalDependencies:
    def __init__(self) -> None:
        self.preflight_project_ids: list[str] = []

    def invoke(self, task: dict[str, object]) -> dict[str, object]:
        del task
        raise AssertionError("Agent Runtime must not run while starting a workflow")

    async def call_tool(self, **kwargs: object) -> object:
        del kwargs
        raise AssertionError("MCP must not run while starting a workflow")

    async def run(self, **kwargs: object) -> McpPreflightReport:
        self.preflight_project_ids.append(str(kwargs["venture_project_id"]))
        return McpPreflightReport(
            protocol_revision="2026-07-28",
            manifest_digest="a" * 64,
            tool_count=10,
        )


class FailedPreflightDependencies(ConfiguredExternalDependencies):
    async def run(self, **kwargs: object) -> McpPreflightReport:
        del kwargs
        raise McpClientError("MCP_MANIFEST_MISMATCH")


class RecordingOutboxDispatcher:
    def __init__(self) -> None:
        self.calls = 0

    def publish_one(self) -> bool:
        self.calls += 1
        return False


class DocumentStorageFixture:
    def __init__(self) -> None:
        self.objects: dict[str, StoredObject] = {}
        self.contents: dict[str, bytes] = {}

    def sign_upload(self, **kwargs: Any) -> str:
        return f"https://upload.invalid/{kwargs['object_path']}"

    def inspect(self, *, object_path: str) -> StoredObject | None:
        return self.objects.get(object_path)

    def sign_download(self, **kwargs: Any) -> str:
        return f"https://download.invalid/{kwargs['object_path']}"

    def delete(self, *, object_path: str) -> None:
        self.objects.pop(object_path, None)
        self.contents.pop(object_path, None)


class DocumentCanaryTransportFixture:
    def __init__(self, storage: DocumentStorageFixture) -> None:
        self._storage = storage

    def put(self, url: str, *, content: bytes, headers: Mapping[str, str]) -> int:
        object_path = url.removeprefix("https://upload.invalid/")
        self._storage.contents[object_path] = content
        self._storage.objects[object_path] = StoredObject(
            content_type=headers["Content-Type"],
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        return 200

    def get(self, url: str) -> tuple[int, bytes]:
        object_path = url.removeprefix("https://download.invalid/")
        content = self._storage.contents.get(object_path)
        return (200, content) if content is not None else (404, b"")


class DocumentExtractionAgentFixture:
    def __init__(self) -> None:
        self.tasks: list[dict[str, Any]] = []

    def invoke(self, task: dict[str, Any]) -> dict[str, Any]:
        self.tasks.append(task)
        claim_id = task["payload"]["claim_id_pool"][0]
        anchor = deepcopy(task["payload"]["parser_blocks"][0]["anchor"])
        return {
            "schema_version": "1.0.0",
            "task_id": task["task_id"],
            "invocation_id": task["invocation_id"],
            "agent_name": task["agent_name"],
            "task_type": task["task_type"],
            "workflow_run_id": task["workflow_run_id"],
            "stage_run_id": task["stage_run_id"],
            "venture_project_id": task["venture_project_id"],
            "head_fence_seen": deepcopy(task["head_fence"]),
            "input_digest": task["input_digest"],
            "output_schema_id": task["output_schema_id"],
            "status": "COMPLETE",
            "payload": {
                "proposed_claims": [
                    {
                        "claim_id": claim_id,
                        "predicate": "LEASE_DEPOSIT",
                        "raw_value_text": "보증금 5,000만원",
                        "typed_value": {"kind": "INTEGER", "value": 50_000_000},
                        "unit": "KRW",
                        "currency": "KRW",
                        "vat_status": "NOT_APPLICABLE",
                        "inclusion_scope": "보증금",
                        "effective_from": None,
                        "effective_to": None,
                        "valid_until": None,
                        "document_revision_id": task["payload"]["document_revision"][
                            "document_revision_id"
                        ],
                        "anchor": anchor,
                        "extraction_status": "PROPOSED",
                        "risk_flags": [],
                    }
                ],
                "unresolved_fields": [],
                "document_risk_flags": [],
            },
            "evidence_refs": [],
            "missing_claim_ids": [],
            "reason_codes": [],
            "warnings": [],
        }


class FirstProposalMcpFixture:
    def __init__(self) -> None:
        self.tool_names: list[str] = []

    async def call_tool(self, **kwargs: Any) -> McpCallOutcome:
        tool_name = kwargs["tool_name"]
        self.tool_names.append(tool_name)
        if tool_name != "resolve_area":
            request_id = f"request-{tool_name}-{len(self.tool_names)}"
            return McpCallOutcome(
                request_id=request_id,
                tool_name=tool_name,
                tool_version="1.0.0",
                status="OK",
                is_complete=True,
                structured_content={
                    "schema_version": "1.0.0",
                    "request_id": request_id,
                    "tool_name": tool_name,
                    "tool_version": "1.0.0",
                    "status": "OK",
                    "project_id": kwargs["venture_project_id"],
                    "data": [],
                    "evidence_records": [],
                    "missing_fields": [],
                    "conflicts": [],
                    "source_trace": [],
                    "error_codes": [],
                    "observed_at": "2026-08-21T10:00:00Z",
                },
            )
        return McpCallOutcome(
            request_id="request-area-1",
            tool_name="resolve_area",
            tool_version="1.0.0",
            status="OK",
            is_complete=True,
            structured_content={
                "data": [
                    {
                        "administrative_code": "4111756000",
                        "display_name": "경기도 수원시 영통구 원천동",
                        "boundary_version": "2026-01",
                        "match_kind": "EXACT",
                    }
                ],
                "evidence_records": [],
                "missing_fields": [],
                "conflicts": [],
                "source_trace": [],
                "observed_at": "2026-08-21T10:00:00Z",
            },
        )


class FirstProposalAgentFixture:
    def __init__(self) -> None:
        self.task_types: list[str] = []

    def invoke(self, task: dict[str, Any]) -> dict[str, Any]:
        task_type = task["task_type"]
        self.task_types.append(task_type)
        if task_type == "EVIDENCE_ASSESS":
            missing = [claim["claim_id"] for claim in task["payload"]["claims"]]
            return self._result(
                task,
                payload={
                    "assessments": [],
                    "missing_claims": missing,
                    "conflict_proposals": [],
                },
                missing_claim_ids=missing,
            )
        if task_type == "PROPOSE_INDEPENDENT":
            return proposal_result(task)
        if task_type == "CANDIDATE_AUDIT":
            return audit_result(task)
        raise AssertionError(f"Unexpected Agent task in minimal integration: {task_type}")

    @staticmethod
    def _result(
        task: dict[str, Any],
        *,
        payload: dict[str, Any],
        missing_claim_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "task_id": task["task_id"],
            "invocation_id": task["invocation_id"],
            "agent_name": task["agent_name"],
            "task_type": task["task_type"],
            "workflow_run_id": task["workflow_run_id"],
            "stage_run_id": task["stage_run_id"],
            "venture_project_id": task["venture_project_id"],
            "head_fence_seen": deepcopy(task["head_fence"]),
            "input_digest": task["input_digest"],
            "output_schema_id": task["output_schema_id"],
            "status": "COMPLETE",
            "payload": payload,
            "evidence_refs": [],
            "missing_claim_ids": missing_claim_ids or [],
            "reason_codes": [],
            "warnings": [],
        }


class FeedbackAgentFixture:
    def __init__(self, target_funds: int = 40_000_000) -> None:
        self.tasks: list[dict[str, Any]] = []
        self.target_funds = target_funds

    def invoke(self, task: dict[str, Any]) -> dict[str, Any]:
        self.tasks.append(task)
        operation_id = task["payload"]["operation_id_pool"][0]
        current_funds = task["payload"]["current_state_projection"]["founder"][
            "own_funds_krw"
        ]
        return {
            "schema_version": "1.0.0",
            "task_id": task["task_id"],
            "invocation_id": task["invocation_id"],
            "agent_name": task["agent_name"],
            "task_type": task["task_type"],
            "workflow_run_id": task["workflow_run_id"],
            "stage_run_id": task["stage_run_id"],
            "venture_project_id": task["venture_project_id"],
            "head_fence_seen": deepcopy(task["head_fence"]),
            "input_digest": task["input_digest"],
            "output_schema_id": task["output_schema_id"],
            "status": "COMPLETE",
            "payload": {
                "decision": "PROPOSE_DELTA",
                "operations": [
                    {
                        "op_id": operation_id,
                        "kind": "SET",
                        "field_path": "/founder/own_funds_krw",
                        "expected_old_value": {
                            "kind": "INTEGER",
                            "value": current_funds,
                        },
                        "typed_value": {"kind": "INTEGER", "value": self.target_funds},
                        "unit": "KRW",
                        "semantic_kind": "HARD_CONSTRAINT",
                        "source_span": {"start": 0, "end": 17},
                        "ambiguity_codes": [],
                    }
                ],
                "clarifying_questions": [],
                "affected_workflow_codes": ["FIRST_PROPOSAL"],
                "risk_flags": [],
            },
            "evidence_refs": [],
            "missing_claim_ids": [],
            "reason_codes": [],
            "warnings": [],
        }


class RouterStageProcessor:
    def __init__(self, router: FirstProposalStageRouter) -> None:
        self._router = router
        self.errors: list[Exception] = []

    def process(self, lease: StageLease) -> dict[str, object]:
        try:
            return self._router.execute(lease)
        except Exception as error:
            self.errors.append(error)
            raise


class ImmediateWorkerPublisher:
    def __init__(self, worker: DurableWorker) -> None:
        self._worker = worker
        self.outcomes: list[DeliveryOutcome] = []

    def publish(
        self,
        *,
        topic: str,
        payload: Mapping[str, object],
        attributes: Mapping[str, str],
    ) -> str:
        message_id = f"integration-message-{len(self.outcomes) + 1}"
        outcome = self._worker.handle(
            PubSubDelivery(
                message_id=message_id,
                logical_topic=topic,
                payload=payload,
                attributes={**attributes, "logical_topic": topic},
            )
        )
        self.outcomes.append(outcome)
        return message_id


@pytest.fixture(scope="module")
def postgres_engine() -> Engine:
    with PostgresContainer(
        image="postgres:17-alpine",
        username="caffemate",
        password="integration-test-only",
        dbname="caffemate",
        driver="pg8000",
    ) as postgres:
        engine = create_engine(postgres.get_connection_url(), pool_pre_ping=True)
        apply_migrations(engine)
        yield engine
        engine.dispose()


@pytest.fixture
def repository(postgres_engine: Engine) -> PostgresProjectRepository:
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE result_bundles, workflow_outbox, workflow_idempotency_records, "
                "workflow_events, "
                "stage_runs, workflow_runs, idempotency_records, project_events, "
                "venture_states, venture_projects CASCADE"
            )
        )
    return PostgresProjectRepository(postgres_engine)


def founder(*, own_funds_krw: int = 50_000_000) -> FounderState:
    return FounderState(
        target_area_input="수원 아주대 부근",
        own_funds_krw=own_funds_krw,
        borrowing_intent=BorrowingIntent.UNDECIDED,
        cafe_type_preference=CafeTypePreference.OPEN_TO_BOTH,
        operation_mode=OperationMode.DIRECT_FULL_TIME,
    )


def onboarded_project(repository: PostgresProjectRepository, *, user_id: str = "user-1"):
    service = ProjectService(repository)
    project = service.create_project(user_id=user_id, idempotency_key=f"create-{user_id}")
    return service.confirm_onboarding(
        project_id=project.project_id,
        user_id=user_id,
        idempotency_key=f"onboarding-{user_id}",
        founder=founder(),
    )


def test_migrations_are_idempotent(postgres_engine: Engine) -> None:
    apply_migrations(postgres_engine)
    with postgres_engine.connect() as connection:
        versions = connection.execute(
            text("SELECT version FROM schema_migrations ORDER BY version")
        ).scalars()
        assert list(versions) == [
            "0001_project_state.sql",
            "0002_workflow_outbox.sql",
            "0003_worker_lease_fence.sql",
            "0004_result_bundle.sql",
            "0005_stage_failure.sql",
            "0006_first_proposal_dag.sql",
            "0007_evidence_snapshot.sql",
            "0008_feedback_preview.sql",
            "0009_feedback_resolution.sql",
            "0010_candidate_selection.sql",
            "0011_document_upload.sql",
            "0012_document_extraction.sql",
            "0013_document_claim_apply.sql",
            "0014_result_decision_delta.sql",
            "0015_outbox_dead_letter.sql",
            "0016_evidence_refresh.sql",
            "0017_outbox_reprocess_audit.sql",
        ]


def test_agent_session_cleanup_outbox_is_durable_and_idempotent(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    del repository
    sink = PostgresAgentCleanupSink(postgres_engine)
    arguments = {
        "runtime_resource": (
            "projects/gcp-project/locations/asia-northeast3/reasoningEngines/runtime-1"
        ),
        "user_id": "p-pseudonymous",
        "session_id": "session-1",
    }

    sink.enqueue_session_delete(**arguments)
    sink.enqueue_session_delete(**arguments)

    with postgres_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT topic, aggregate_id, payload_json "
                "FROM workflow_outbox WHERE topic = 'AGENT_SESSION_CLEANUP'"
            )
        ).mappings()
        assert list(rows) == [
            {
                "topic": "AGENT_SESSION_CLEANUP",
                "aggregate_id": "session-1",
                "payload_json": arguments,
            }
        ]


def test_agent_session_cleanup_consumer_completes_retries_and_dead_letters(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    del repository
    sink = PostgresAgentCleanupSink(postgres_engine)
    arguments = {
        "runtime_resource": (
            "projects/gcp-project/locations/asia-northeast3/reasoningEngines/runtime-1"
        ),
        "user_id": "p-pseudonymous",
        "session_id": "session-cleanup-consumer",
    }
    sink.enqueue_session_delete(**arguments)

    class SuccessfulDeleter:
        def delete(self, payload: Mapping[str, object]) -> None:
            assert payload == arguments

    consumer = AgentSessionCleanupConsumer(
        PostgresOutboxRepository(postgres_engine, new_token=lambda: "cleanup-token"),
        SuccessfulDeleter(),
        consumer_id="cleanup-consumer",
    )
    assert consumer.cleanup_one() == CleanupOutcome.DELETED
    assert consumer.cleanup_one() == CleanupOutcome.EMPTY

    sink.enqueue_session_delete(**{**arguments, "session_id": "wrong-scope"})

    class ScopeRejectingDeleter:
        def delete(self, payload: Mapping[str, object]) -> None:
            del payload
            raise ValueError("scope")

    rejecting = AgentSessionCleanupConsumer(
        PostgresOutboxRepository(postgres_engine, new_token=lambda: "reject-token"),
        ScopeRejectingDeleter(),
        consumer_id="cleanup-consumer",
    )
    assert rejecting.cleanup_one() == CleanupOutcome.DEAD_LETTERED
    with postgres_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT aggregate_id, status, failure_code FROM workflow_outbox "
                "WHERE topic='AGENT_SESSION_CLEANUP' ORDER BY outbox_id"
            )
        ).mappings().all()
    assert [dict(row) for row in rows] == [
        {
            "aggregate_id": "session-cleanup-consumer",
            "status": "PUBLISHED",
            "failure_code": None,
        },
        {
            "aggregate_id": "wrong-scope",
            "status": "DEAD_LETTER",
            "failure_code": "AGENT_CLEANUP_SCOPE_INVALID",
        },
    ]


def test_dead_letter_reprocess_is_allowlisted_idempotent_and_audited(
    postgres_engine: Engine,
) -> None:
    payload = {
        "runtime_resource": (
            "projects/test/locations/asia-northeast3/reasoningEngines/runtime-1"
        ),
        "user_id": "p-pseudonymous",
        "session_id": "session-retry-exhausted",
    }
    payload_bytes = rfc8785.dumps(payload)
    with postgres_engine.begin() as connection:
        outbox_id = connection.execute(
            text(
                """
                INSERT INTO workflow_outbox(
                    topic, aggregate_id, payload_json, payload_digest, status,
                    attempts, available_at, failure_code, failed_at, created_at
                ) VALUES (
                    'AGENT_SESSION_CLEANUP', 'session-retry-exhausted',
                    CAST(:payload AS JSONB), :digest, 'DEAD_LETTER', 5, NOW(),
                    'AGENT_CLEANUP_RETRY_EXHAUSTED', NOW(), NOW()
                ) RETURNING outbox_id
                """
            ),
            {
                "payload": payload_bytes.decode(),
                "digest": hashlib.sha256(payload_bytes).hexdigest(),
            },
        ).scalar_one()
    operations = DeadLetterOperations(
        postgres_engine,
        now=lambda: datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
        new_id=lambda: "reprocess-event-1",
    )
    page = operations.list(limit=10)
    record = next(item for item in page.items if item.outbox_id == outbox_id)
    assert record.reprocessable is True
    assert not hasattr(record, "payload")
    request = ReprocessDeadLetterRequest(
        request_id="request-1",
        expected_failure_code="AGENT_CLEANUP_RETRY_EXHAUSTED",
        remediation_code="RUNTIME_RECOVERED",
        change_reference="INC-42",
    )
    applied = operations.reprocess(outbox_id=outbox_id, request=request)
    replay = operations.reprocess(outbox_id=outbox_id, request=request)
    assert replay == applied
    with pytest.raises(DeadLetterOperationError, match="DEAD_LETTER_REQUEST_ID_REUSED"):
        operations.reprocess(
            outbox_id=outbox_id + 1,
            request=request,
        )
    with postgres_engine.connect() as connection:
        outbox = connection.execute(
            text(
                "SELECT status, attempts, failure_code, failed_at "
                "FROM workflow_outbox WHERE outbox_id=:outbox_id"
            ),
            {"outbox_id": outbox_id},
        ).one()
        audit = connection.execute(
            text(
                "SELECT previous_failure_code, previous_attempts, remediation_code, "
                "change_reference FROM outbox_reprocess_events WHERE outbox_id=:outbox_id"
            ),
            {"outbox_id": outbox_id},
        ).one()
    assert outbox == ("PENDING", 0, None, None)
    assert audit == (
        "AGENT_CLEANUP_RETRY_EXHAUSTED",
        5,
        "RUNTIME_RECOVERED",
        "INC-42",
    )


def test_evidence_refresh_invalidates_result_and_starts_scoped_selective_rerun(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project, stage_id, input_digest, _workflows = create_commit_stage(
        repository, postgres_engine
    )
    execution = PostgresStageExecutionRepository(
        postgres_engine,
        new_result_id=lambda: "result-before-refresh",
    )
    lease = execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-refresh",
        expected_input_digest=input_digest,
    )
    assert lease is not None
    assert execution.checkpoint(
        stage_run_id=stage_id,
        lease_token=lease.lease_token,
        input_digest=lease.input_digest,
        result=result_payload(project_id=project.project_id),
    ) == CheckpointOutcome.APPLIED
    record = evidence_record("evidence-refresh-1")
    record["project_id"] = project.project_id
    record["source"]["source_ref"] = "official://franchise/disclosure/brand-a"
    record["source"]["document_version"] = "v1"
    record["source"]["source_observed_at"] = "2026-08-22T00:00:00Z"
    record["retrieved_at"] = "2026-08-22T00:00:00Z"
    record_digest = hashlib.sha256(rfc8785.dumps(record)).hexdigest()
    snapshot_id = "evidence-refresh-snapshot-v1"
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO evidence_records(
                    project_id, evidence_id, record_json, record_digest, created_at
                ) VALUES (
                    :project_id, :evidence_id, CAST(:record AS JSONB),
                    :record_digest, :created_at
                )
                """
            ),
            {
                "project_id": project.project_id,
                "evidence_id": record["evidence_id"],
                "record": json.dumps(record),
                "record_digest": record_digest,
                "created_at": datetime(2026, 8, 22, 0, 0, tzinfo=UTC),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO evidence_snapshots(
                    evidence_snapshot_id, project_id, workflow_run_id,
                    source_stage_run_id, snapshot_json, snapshot_digest, created_at
                ) VALUES (
                    :snapshot_id, :project_id, :workflow_run_id, :stage_id,
                    '{}'::JSONB, :digest, :created_at
                )
                """
            ),
            {
                "snapshot_id": snapshot_id,
                "project_id": project.project_id,
                "workflow_run_id": lease.workflow_run_id,
                "stage_id": stage_id,
                "digest": "sha256:" + "a" * 64,
                "created_at": datetime(2026, 8, 22, 0, 0, tzinfo=UTC),
            },
        )
        connection.execute(
            text(
                "INSERT INTO evidence_snapshot_records("
                "evidence_snapshot_id, project_id, evidence_id) "
                "VALUES (:snapshot_id, :project_id, :evidence_id)"
            ),
            {
                "snapshot_id": snapshot_id,
                "project_id": project.project_id,
                "evidence_id": record["evidence_id"],
            },
        )
        connection.execute(
            text(
                "UPDATE project_heads SET evidence_snapshot_id=:snapshot_id "
                "WHERE project_id=:project_id"
            ),
            {"snapshot_id": snapshot_id, "project_id": project.project_id},
        )
        connection.execute(
            text(
                "UPDATE workflow_runs SET evidence_snapshot_id=:snapshot_id "
                "WHERE workflow_run_id=:workflow_run_id"
            ),
            {"snapshot_id": snapshot_id, "workflow_run_id": lease.workflow_run_id},
        )
        connection.execute(
            text(
                "UPDATE result_bundles SET evidence_snapshot_id=:snapshot_id "
                "WHERE result_bundle_id=:result_bundle_id"
            ),
            {
                "snapshot_id": snapshot_id,
                "result_bundle_id": "result-before-refresh",
            },
        )
    now = datetime(2026, 8, 22, 1, 0, tzinfo=UTC)
    refresh = EvidenceRefreshService(
        postgres_engine,
        now=lambda: now,
        new_id=iter(("refresh-1", "workflow-refresh", *(f"id-{i}" for i in range(30)))).__next__,
    )
    request = EvidenceRefreshRequest.model_validate(
        {
            "project_id": project.project_id,
            "observations": [
                {
                    "source_ref": "official://franchise/disclosure/brand-a",
                    "source_revision": "v2",
                    "source_observed_at": now.isoformat(),
                    "availability": "AVAILABLE",
                }
            ],
        }
    )
    result = refresh.refresh(request)
    replay = refresh.refresh(request)
    unchanged = refresh.refresh(
        request.model_copy(
            update={
                "observations": [
                    request.observations[0].model_copy(
                        update={
                            "source_observed_at": now + timedelta(minutes=1),
                        }
                    )
                ]
            }
        )
    )

    assert result == replay
    assert unchanged.status == "NO_CHANGE"
    assert unchanged.recompute_workflow_run_id is None
    assert result.status == "RECOMPUTE_QUEUED"
    assert result.changed_source_refs == ["official://franchise/disclosure/brand-a"]
    assert result.affected_evidence_ids == ["evidence-refresh-1"]
    assert result.invalidated_result_bundle_id == "result-before-refresh"
    assert result.recompute_workflow_run_id == "workflow-refresh"
    loaded = ResultService(PostgresResultRepository(postgres_engine)).get_current(
        project_id=project.project_id, user_id="user-1"
    )
    assert loaded.freshness.value == "STALE"
    assert loaded.invalidation_reason_codes == ["SOURCE_REVISION_CHANGED"]
    with postgres_engine.connect() as connection:
        lifecycle = connection.execute(
            text(
                "SELECT status, reason_code FROM evidence_lifecycle "
                "WHERE project_id=:project_id AND evidence_id=:evidence_id"
            ),
            {"project_id": project.project_id, "evidence_id": "evidence-refresh-1"},
        ).mappings().one()
    assert dict(lifecycle) == {
        "status": "STALE",
        "reason_code": "SOURCE_REVISION_CHANGED",
    }


def test_project_and_state_survive_repository_recreation(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    service = ProjectService(repository)
    project = service.create_project(user_id="user-1", idempotency_key="create-1")
    service.confirm_onboarding(
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="onboarding-1",
        founder=founder(),
    )

    recreated = ProjectService(PostgresProjectRepository(postgres_engine))
    loaded = recreated.get_project(project_id=project.project_id, user_id="user-1")

    assert loaded.state is not None
    assert loaded.state.state_version == 1
    assert loaded.state.founder.own_funds_krw == 50_000_000


def test_fastapi_runtime_uses_configured_postgres_repository(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE result_bundles, workflow_outbox, workflow_idempotency_records, "
                "workflow_events, "
                "stage_runs, workflow_runs, idempotency_records, project_events, "
                "venture_states, venture_projects CASCADE"
            )
        )
    monkeypatch.setenv(
        "DATABASE_URL",
        postgres_engine.url.render_as_string(hide_password=False),
    )

    from fastapi.testclient import TestClient

    with TestClient(create_app(identity_verifier=FixedIdentityVerifier())) as client:
        created = client.post(
            "/v1/projects",
            headers={"Authorization": "Bearer valid-token", "Idempotency-Key": "create-1"},
            json={},
        )
        listed = client.get(
            "/v1/projects",
            headers={"Authorization": "Bearer valid-token"},
        )

    assert created.status_code == 201
    assert listed.status_code == 200
    assert listed.json() == [created.json()]


def test_project_queries_are_owner_scoped(repository: PostgresProjectRepository) -> None:
    service = ProjectService(repository)
    own = service.create_project(user_id="user-1", idempotency_key="create-1")
    other = service.create_project(user_id="user-2", idempotency_key="create-2")

    assert [project.project_id for project in service.list_projects(user_id="user-1")] == [
        own.project_id
    ]
    with pytest.raises(ProjectNotFoundError):
        service.get_project(project_id=other.project_id, user_id="user-1")


def test_same_request_is_replayed_and_changed_body_is_rejected(
    repository: PostgresProjectRepository,
) -> None:
    service = ProjectService(repository)
    project = service.create_project(user_id="user-1", idempotency_key="create-1")
    first = service.confirm_onboarding(
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="onboarding-1",
        founder=founder(),
    )
    replay = service.confirm_onboarding(
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="onboarding-1",
        founder=founder(),
    )

    assert replay == first
    with pytest.raises(IdempotencyKeyReusedError):
        service.confirm_onboarding(
            project_id=project.project_id,
            user_id="user-1",
            idempotency_key="onboarding-1",
            founder=founder(own_funds_krw=40_000_000),
        )


def test_concurrent_duplicate_commits_one_event_and_one_state(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    service = ProjectService(repository)
    project = service.create_project(user_id="user-1", idempotency_key="create-1")

    def confirm(_index: int) -> int:
        result = service.confirm_onboarding(
            project_id=project.project_id,
            user_id="user-1",
            idempotency_key="same-request",
            founder=founder(),
        )
        assert result.state is not None
        return result.state.state_version

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(confirm, range(2))) == [1, 1]

    with postgres_engine.connect() as connection:
        event_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM project_events "
                "WHERE project_id = :project_id AND event_type = 'ONBOARDING_CONFIRMED'"
            ),
            {"project_id": project.project_id},
        ).scalar_one()
        state_count = connection.execute(
            text("SELECT COUNT(*) FROM venture_states WHERE project_id = :project_id"),
            {"project_id": project.project_id},
        ).scalar_one()
    assert event_count == state_count == 1


def test_distinct_second_onboarding_is_a_state_version_conflict(
    repository: PostgresProjectRepository,
) -> None:
    service = ProjectService(repository)
    project = service.create_project(user_id="user-1", idempotency_key="create-1")
    service.confirm_onboarding(
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="onboarding-1",
        founder=founder(),
    )

    with pytest.raises(StateVersionConflictError):
        service.confirm_onboarding(
            project_id=project.project_id,
            user_id="user-1",
            idempotency_key="onboarding-2",
            founder=founder(),
        )


def test_validation_failure_rolls_back_idempotency_event_and_state(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    service = ProjectService(repository)
    project = service.create_project(user_id="user-1", idempotency_key="create-1")

    class RejectAllContracts:
        def validate_venture_state(self, _value: dict[str, object]) -> None:
            raise ContractValidationError("rejected for rollback test")

    rejecting_service = ProjectService(
        PostgresProjectRepository(postgres_engine, contracts=RejectAllContracts())
    )
    with pytest.raises(ContractValidationError):
        rejecting_service.confirm_onboarding(
            project_id=project.project_id,
            user_id="user-1",
            idempotency_key="onboarding-1",
            founder=founder(),
        )

    recovered = service.confirm_onboarding(
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="onboarding-1",
        founder=founder(),
    )
    assert recovered.state is not None
    assert recovered.state.state_version == 1


def test_workflow_requires_confirmed_onboarding_and_rolls_back_command(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    projects = ProjectService(repository)
    project = projects.create_project(user_id="user-1", idempotency_key="create-1")
    workflows = WorkflowService(
        PostgresWorkflowRepository(
            postgres_engine, policy_snapshot_id="policy-v1", seed_registry_id="seed-v1"
        )
    )

    from app.domain.errors import WorkflowPreconditionError

    with pytest.raises(WorkflowPreconditionError):
        workflows.start(
            project_id=project.project_id,
            user_id="user-1",
            workflow_code=WorkflowCode.FIRST_PROPOSAL,
            idempotency_key="workflow-1",
        )

    projects.confirm_onboarding(
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="onboarding-1",
        founder=founder(),
    )
    run = workflows.start(
        project_id=project.project_id,
        user_id="user-1",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="workflow-1",
    )
    assert run.status == WorkflowStatus.QUEUED


def test_workflow_start_atomically_writes_run_stage_event_and_outbox(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project = onboarded_project(repository)
    workflows = WorkflowService(
        PostgresWorkflowRepository(
            postgres_engine, policy_snapshot_id="policy-v1", seed_registry_id="seed-v1"
        )
    )

    run = workflows.start(
        project_id=project.project_id,
        user_id="user-1",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="workflow-1",
    )

    assert run.head.workflow_generation == 1
    assert run.head.state_version == 1
    assert run.head.policy_snapshot_id == "policy-v1"
    assert run.head.seed_registry_id == "seed-v1"
    assert run.head.founder_snapshot_id == f"{project.project_id}:state:1:founder"
    assert run.head.area_snapshot_id == f"{project.project_id}:state:1:area"
    with postgres_engine.connect() as connection:
        counts = {
            table: connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            for table in (
                "workflow_runs",
                "stage_runs",
                "workflow_events",
                "workflow_idempotency_records",
                "workflow_outbox",
            )
        }
        outbox = (
            connection.execute(text("SELECT topic, status, payload_json FROM workflow_outbox"))
            .mappings()
            .one()
        )
        digest_lengths = list(
            connection.execute(
                text(
                    "SELECT octet_length(request_digest) FROM idempotency_records "
                    "UNION ALL "
                    "SELECT octet_length(request_digest) FROM workflow_idempotency_records"
                )
            ).scalars()
        )
    assert counts == {
        "workflow_runs": 1,
        "stage_runs": 13,
        "workflow_events": 1,
        "workflow_idempotency_records": 1,
        "workflow_outbox": 1,
    }
    assert outbox["topic"] == "WORKFLOW_STAGE_READY"
    assert outbox["status"] == "PENDING"
    assert outbox["payload_json"]["workflow_run_id"] == run.workflow_run_id
    assert digest_lengths == [32, 32, 32]

    progress = workflows.get_progress(
        project_id=project.project_id,
        workflow_run_id=run.workflow_run_id,
        user_id="user-1",
    )
    assert progress.total_stage_count == 13
    assert progress.completed_stage_count == 0
    assert progress.current_stage_codes == ["AREA_RESOLUTION"]
    assert progress.human_review_requests == []
    assert progress.terminal_reason_codes == []
    assert progress.poll_after_ms == 1500


def test_concurrent_workflow_redelivery_returns_one_run(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project = onboarded_project(repository)
    workflows = WorkflowService(
        PostgresWorkflowRepository(
            postgres_engine, policy_snapshot_id="policy-v1", seed_registry_id="seed-v1"
        )
    )

    def start(_index: int) -> str:
        return workflows.start(
            project_id=project.project_id,
            user_id="user-1",
            workflow_code=WorkflowCode.FIRST_PROPOSAL,
            idempotency_key="same-command",
        ).workflow_run_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        workflow_ids = list(executor.map(start, range(2)))

    assert workflow_ids[0] == workflow_ids[1]
    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM workflow_runs")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM workflow_outbox")).scalar_one() == 1


def test_workflow_is_hidden_from_other_project_owner(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project = onboarded_project(repository)
    workflows = WorkflowService(
        PostgresWorkflowRepository(
            postgres_engine, policy_snapshot_id="policy-v1", seed_registry_id="seed-v1"
        )
    )
    run = workflows.start(
        project_id=project.project_id,
        user_id="user-1",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="workflow-1",
    )

    from app.domain.errors import WorkflowNotFoundError

    with pytest.raises(WorkflowNotFoundError):
        workflows.get(
            project_id=project.project_id,
            workflow_run_id=run.workflow_run_id,
            user_id="user-2",
        )


def test_cancel_increments_generation_and_durably_cancels_stage(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project = onboarded_project(repository)
    workflows = WorkflowService(
        PostgresWorkflowRepository(
            postgres_engine, policy_snapshot_id="policy-v1", seed_registry_id="seed-v1"
        )
    )
    run = workflows.start(
        project_id=project.project_id,
        user_id="user-1",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="workflow-1",
    )

    cancelled = workflows.cancel(
        project_id=project.project_id,
        workflow_run_id=run.workflow_run_id,
        user_id="user-1",
        idempotency_key="cancel-1",
    )
    replay = workflows.cancel(
        project_id=project.project_id,
        workflow_run_id=run.workflow_run_id,
        user_id="user-1",
        idempotency_key="cancel-1",
    )

    assert cancelled == replay
    assert cancelled.status == WorkflowStatus.CANCELLED
    events = workflows.list_events(
        project_id=project.project_id,
        workflow_run_id=run.workflow_run_id,
        user_id="user-1",
    )
    assert [event.event_type for event in events] == [
        "WORKFLOW_QUEUED",
        "WORKFLOW_CANCELLED",
    ]
    with postgres_engine.connect() as connection:
        generation = connection.execute(
            text("SELECT workflow_generation FROM venture_projects WHERE project_id=:id"),
            {"id": project.project_id},
        ).scalar_one()
        stage_statuses = list(connection.execute(text("SELECT status FROM stage_runs")).scalars())
        topics = list(
            connection.execute(
                text("SELECT topic FROM workflow_outbox ORDER BY outbox_id")
            ).scalars()
        )
    assert generation == 2
    assert set(stage_statuses) == {"CANCELLED"}
    assert topics == ["WORKFLOW_STAGE_READY", "WORKFLOW_CLEANUP"]


def test_http_202_workflow_survives_api_instance_shutdown(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE result_bundles, workflow_outbox, workflow_idempotency_records, "
                "workflow_events, "
                "stage_runs, workflow_runs, idempotency_records, project_events, "
                "venture_states, venture_projects CASCADE"
            )
        )
    monkeypatch.setenv(
        "DATABASE_URL",
        postgres_engine.url.render_as_string(hide_password=False),
    )
    monkeypatch.setenv("CAFFEMATE_POLICY_SNAPSHOT_ID", "policy-v1")

    from fastapi.testclient import TestClient

    headers = {"Authorization": "Bearer valid-token"}
    dependencies = ConfiguredExternalDependencies()
    immediate_outbox = RecordingOutboxDispatcher()
    with TestClient(
        create_app(
            identity_verifier=FixedIdentityVerifier(),
            agent_runtime=dependencies,  # type: ignore[arg-type]
            mcp_client=dependencies,  # type: ignore[arg-type]
            mcp_manifest_preflight=dependencies,  # type: ignore[arg-type]
            outbox_dispatcher=immediate_outbox,
        )
    ) as client:
        project = client.post(
            "/v1/projects",
            headers={**headers, "Idempotency-Key": "create-1"},
            json={},
        ).json()
        client.post(
            f"/v1/projects/{project['project_id']}/onboarding/confirm",
            headers={**headers, "Idempotency-Key": "onboarding-1"},
            json={"founder": founder().model_dump(mode="json")},
        )
        response = client.post(
            f"/v1/projects/{project['project_id']}/workflows/FIRST_PROPOSAL",
            headers={**headers, "Idempotency-Key": "workflow-1"},
            json={},
        )
        assert response.status_code == 202
        assert dependencies.preflight_project_ids == [project["project_id"]]
        workflow_run_id = response.json()["workflow_run_id"]
        progress = client.get(
            f"/v1/projects/{project['project_id']}/workflows/{workflow_run_id}",
            headers=headers,
        )
        assert progress.status_code == 200
        assert progress.json()["current_stage_codes"] == ["AREA_RESOLUTION"]
        assert progress.json()["total_stage_count"] == 13
        assert progress.json()["poll_after_ms"] == 1500
        assert immediate_outbox.calls == 1

    loaded = WorkflowService(
        PostgresWorkflowRepository(
            postgres_engine, policy_snapshot_id="policy-v1", seed_registry_id="seed-v1"
        )
    ).get(
        project_id=project["project_id"],
        workflow_run_id=workflow_run_id,
        user_id="user-1",
    )
    assert loaded.status == WorkflowStatus.QUEUED
    with postgres_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM workflow_outbox WHERE status='PENDING'")
            ).scalar_one()
            == 1
        )

    with TestClient(
        create_app(
            identity_verifier=FixedIdentityVerifier(),
            agent_runtime=dependencies,  # type: ignore[arg-type]
            mcp_client=dependencies,  # type: ignore[arg-type]
            mcp_manifest_preflight=dependencies,  # type: ignore[arg-type]
        )
    ) as restarted_client:
        cancelled = restarted_client.post(
            f"/v1/projects/{project['project_id']}/workflows/{workflow_run_id}:cancel",
            headers={**headers, "Idempotency-Key": "cancel-1"},
            json={},
        )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"


def test_workflow_start_reports_exact_missing_stage_configuration(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE result_bundles, workflow_outbox, workflow_idempotency_records, "
                "workflow_events, stage_runs, workflow_runs, idempotency_records, "
                "project_events, venture_states, venture_projects CASCADE"
            )
        )
    monkeypatch.setenv(
        "DATABASE_URL",
        postgres_engine.url.render_as_string(hide_password=False),
    )
    monkeypatch.setenv("CAFFEMATE_POLICY_SNAPSHOT_ID", "policy-v1")

    from fastapi.testclient import TestClient

    headers = {"Authorization": "Bearer valid-token"}
    with TestClient(create_app(identity_verifier=FixedIdentityVerifier())) as client:
        project = client.post(
            "/v1/projects",
            headers={**headers, "Idempotency-Key": "create-1"},
            json={},
        ).json()
        client.post(
            f"/v1/projects/{project['project_id']}/onboarding/confirm",
            headers={**headers, "Idempotency-Key": "onboarding-1"},
            json={"founder": founder().model_dump(mode="json")},
        )
        response = client.post(
            f"/v1/projects/{project['project_id']}/workflows/FIRST_PROPOSAL",
            headers={**headers, "Idempotency-Key": "workflow-1"},
            json={},
        )

    assert response.status_code == 503
    assert response.json() == {
        "code": "FIRST_PROPOSAL_CONFIGURATION_UNAVAILABLE",
        "missing_stage_codes": [
            "AREA_RESOLUTION",
            "CANDIDATE_AUDIT",
            "EVIDENCE_ASSESS",
            "EVIDENCE_RETRIEVAL",
            "PROPOSE_FRANCHISE",
            "PROPOSE_INDEPENDENT",
        ],
    }
    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM workflow_runs")).scalar_one() == 0


def test_workflow_start_rejects_manifest_drift_before_persistence(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE result_bundles, workflow_outbox, workflow_idempotency_records, "
                "workflow_events, stage_runs, workflow_runs, idempotency_records, "
                "project_events, venture_states, venture_projects CASCADE"
            )
        )
    monkeypatch.setenv(
        "DATABASE_URL",
        postgres_engine.url.render_as_string(hide_password=False),
    )
    monkeypatch.setenv("CAFFEMATE_POLICY_SNAPSHOT_ID", "policy-v1")
    dependencies = FailedPreflightDependencies()
    headers = {"Authorization": "Bearer valid-token"}

    from fastapi.testclient import TestClient

    with TestClient(
        create_app(
            identity_verifier=FixedIdentityVerifier(),
            agent_runtime=dependencies,  # type: ignore[arg-type]
            mcp_client=dependencies,  # type: ignore[arg-type]
            mcp_manifest_preflight=dependencies,  # type: ignore[arg-type]
        )
    ) as client:
        project = client.post(
            "/v1/projects",
            headers={**headers, "Idempotency-Key": "create-preflight-failure"},
            json={},
        ).json()
        client.post(
            f"/v1/projects/{project['project_id']}/onboarding/confirm",
            headers={**headers, "Idempotency-Key": "onboarding-preflight-failure"},
            json={"founder": founder().model_dump(mode="json")},
        )
        response = client.post(
            f"/v1/projects/{project['project_id']}/workflows/FIRST_PROPOSAL",
            headers={**headers, "Idempotency-Key": "workflow-preflight-failure"},
            json={},
        )

    assert response.status_code == 503
    assert response.json() == {
        "code": "FIRST_PROPOSAL_PREFLIGHT_UNAVAILABLE",
        "reason_codes": ["MCP_MANIFEST_MISMATCH"],
    }
    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM workflow_runs")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM workflow_outbox")).scalar_one() == 0


def test_first_proposal_runs_all_real_handlers_through_worker_to_result(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project = onboarded_project(repository)
    seed_registry = IndependentSeedRegistry.load_default()
    runtime = FirstProposalAgentFixture()
    mcp = FirstProposalMcpFixture()
    handlers: dict[FirstProposalStage, FirstProposalStageHandler] = {
        FirstProposalStage.AREA_RESOLUTION: AreaResolutionStageHandler(mcp),
        FirstProposalStage.CLAIM_PLAN: ClaimPlanStageHandler(),
        FirstProposalStage.EVIDENCE_PLAN: EvidencePlanStageHandler(),
        FirstProposalStage.EVIDENCE_RETRIEVAL: EvidenceRetrievalStageHandler(mcp),
        FirstProposalStage.EVIDENCE_ASSESS: EvidenceAssessStageHandler(runtime),
        FirstProposalStage.EVIDENCE_FREEZE: EvidenceFreezeStageHandler(),
        FirstProposalStage.INDEPENDENT_SEED: IndependentSeedStageHandler(seed_registry),
        FirstProposalStage.FRANCHISE_ELIGIBILITY: FranchiseEligibilityStageHandler(),
        FirstProposalStage.PROPOSE_INDEPENDENT: ProposalStageHandler.independent(runtime),
        FirstProposalStage.PROPOSE_FRANCHISE: ProposalStageHandler.franchise(runtime),
        FirstProposalStage.CALCULATE_GATE_RANK: CalculateGateRankStageHandler(),
        FirstProposalStage.CANDIDATE_AUDIT: CandidateAuditStageHandler(runtime),
        FirstProposalStage.COMMIT_RESULT: CommitResultStageHandler(),
    }
    workflows = WorkflowService(
        PostgresWorkflowRepository(
            postgres_engine,
            policy_snapshot_id="policy-v1",
            seed_registry_id=seed_registry.registry_id,
        )
    )
    run = workflows.start(
        project_id=project.project_id,
        user_id="user-1",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="workflow-integration-1",
    )
    execution = PostgresStageExecutionRepository(postgres_engine)
    router = FirstProposalStageRouter(
        PostgresStageContextRepository(postgres_engine), handlers
    )
    processor = RouterStageProcessor(router)
    worker = DurableWorker(
        execution,
        processor,
        worker_id="integration-worker",
    )
    immediate = ImmediateWorkerPublisher(worker)
    dispatcher = OutboxPublisher(
        PostgresOutboxRepository(postgres_engine),
        immediate,
        publisher_id="integration-publisher",
        logical_topic="WORKFLOW_STAGE_READY",
    )

    published = 0
    while dispatcher.publish_one():
        published += 1

    completed = workflows.get(
        project_id=project.project_id,
        workflow_run_id=run.workflow_run_id,
        user_id="user-1",
    )
    with postgres_engine.connect() as connection:
        stage_rows = connection.execute(
            text(
                "SELECT stage_code, status, attempt, failure_json FROM stage_runs "
                "WHERE workflow_run_id=:workflow_run_id ORDER BY stage_code"
            ),
            {"workflow_run_id": run.workflow_run_id},
        ).mappings().all()
        committed_events = connection.execute(
            text(
                "SELECT COUNT(*) FROM workflow_events "
                "WHERE workflow_run_id=:workflow_run_id "
                "AND event_type='RESULT_BUNDLE_COMMITTED'"
            ),
            {"workflow_run_id": run.workflow_run_id},
        ).scalar_one()

    assert completed.status == WorkflowStatus.SUCCEEDED, (stage_rows, processor.errors)
    assert published == 13
    assert immediate.outcomes == [DeliveryOutcome.APPLIED] * 13
    assert processor.errors == []
    assert len(stage_rows) == 13
    assert {row["status"] for row in stage_rows} == {"SUCCEEDED"}
    assert {row["attempt"] for row in stage_rows} == {1}
    result = ResultService(PostgresResultRepository(postgres_engine)).get_current(
        project_id=project.project_id,
        user_id="user-1",
    )
    assert result.workflow_run_id == run.workflow_run_id
    assert len(result.candidates) == 3
    assert result.candidates[0]["case_type"] == "INDEPENDENT"
    assert result.candidates[0]["rank"] == 1
    assert result.freshness.value == "CURRENT"
    assert result.stale_head_dimensions == []
    assert result.current_head == result.head
    assert committed_events == 1
    assert mcp.tool_names[0] == "resolve_area"
    assert mcp.tool_names[1:] == [
        "get_area_profile",
        "search_cafe_observations",
        "search_cafe_observations",
        "search_cafe_observations",
        *(["retrieve_official_documents"] * 6),
    ]
    assert runtime.task_types == [
        "EVIDENCE_ASSESS",
        "PROPOSE_INDEPENDENT",
        "PROPOSE_INDEPENDENT",
        "PROPOSE_INDEPENDENT",
        "CANDIDATE_AUDIT",
    ]


    feedback_runtime = FeedbackAgentFixture()
    feedback = FeedbackService(
        PostgresFeedbackRepository(postgres_engine),
        ProjectService(repository),
        ResultService(PostgresResultRepository(postgres_engine)),
        feedback_runtime,
        new_id=lambda: "feedback-preview-1",
    )
    with postgres_engine.connect() as connection:
        state_count_before = connection.execute(
            text("SELECT COUNT(*) FROM venture_states WHERE project_id=:project_id"),
            {"project_id": project.project_id},
        ).scalar_one()
        event_count_before = connection.execute(
            text("SELECT COUNT(*) FROM project_events WHERE project_id=:project_id"),
            {"project_id": project.project_id},
        ).scalar_one()
    preview = feedback.create_preview(
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="feedback-1",
        user_input="자금은 4천만 원으로 바꿀래",
    )
    replay = feedback.create_preview(
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="feedback-1",
        user_input="자금은 4천만 원으로 바꿀래",
    )
    assert replay == preview
    assert preview.status == FeedbackPreviewStatus.REVIEW_REQUIRED
    assert preview.before_founder["own_funds_krw"] == 50_000_000
    assert preview.after_founder is not None
    assert preview.after_founder["own_funds_krw"] == 40_000_000
    assert preview.affected_candidate_ids == [
        candidate["candidate_id"] for candidate in result.candidates
    ]
    assert preview.affected_stage_codes == [
        "INDEPENDENT_SEED",
        "FRANCHISE_ELIGIBILITY",
        "PROPOSE_INDEPENDENT",
        "PROPOSE_FRANCHISE",
        "CALCULATE_GATE_RANK",
        "CANDIDATE_AUDIT",
        "COMMIT_RESULT",
    ]
    assert len(feedback_runtime.tasks) == 1
    with pytest.raises(IdempotencyKeyReusedError):
        feedback.create_preview(
            project_id=project.project_id,
            user_id="user-1",
            idempotency_key="feedback-1",
            user_input="자금은 3천만 원으로 바꿀래",
        )
    with pytest.raises(FeedbackPreviewNotFoundError):
        feedback.get_preview(
            preview_id=preview.preview_id,
            project_id=project.project_id,
            user_id="user-2",
        )
    with postgres_engine.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM venture_states WHERE project_id=:project_id"),
            {"project_id": project.project_id},
        ).scalar_one() == state_count_before
        assert connection.execute(
            text("SELECT COUNT(*) FROM project_events WHERE project_id=:project_id"),
            {"project_id": project.project_id},
        ).scalar_one() == event_count_before

    assert preview.proposal_digest is not None
    with pytest.raises(FeedbackPreconditionError):
        feedback.confirm_preview(
            preview_id=preview.preview_id,
            project_id=project.project_id,
            user_id="user-1",
            idempotency_key="confirm-feedback-wrong",
            expected_head=preview.head,
            proposal_digest="sha256:" + "0" * 64,
        )
    confirmed = feedback.confirm_preview(
        preview_id=preview.preview_id,
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="confirm-feedback-1",
        expected_head=preview.head,
        proposal_digest=preview.proposal_digest,
    )
    confirmed_replay = feedback.confirm_preview(
        preview_id=preview.preview_id,
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="confirm-feedback-1",
        expected_head=preview.head,
        proposal_digest=preview.proposal_digest,
    )
    assert confirmed_replay == confirmed
    with pytest.raises(FeedbackPreconditionError):
        feedback.confirm_preview(
            preview_id=preview.preview_id,
            project_id=project.project_id,
            user_id="user-1",
            idempotency_key="confirm-feedback-other-key",
            expected_head=preview.head,
            proposal_digest=preview.proposal_digest,
        )
    assert confirmed.preview.status == FeedbackPreviewStatus.CONFIRMED
    assert confirmed.state_version == 2
    assert confirmed.workflow is not None
    assert confirmed.workflow.head.state_version == 2
    with postgres_engine.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM venture_states WHERE project_id=:project_id"),
            {"project_id": project.project_id},
        ).scalar_one() == state_count_before + 1
        assert connection.execute(
            text("SELECT COUNT(*) FROM project_events WHERE project_id=:project_id"),
            {"project_id": project.project_id},
        ).scalar_one() == event_count_before + 1
        feedback_event = connection.execute(
            text(
                "SELECT event_json FROM project_events "
                "WHERE project_id=:project_id AND event_type='FEEDBACK_CHANGE_CONFIRMED'"
            ),
            {"project_id": project.project_id},
        ).scalar_one()
        rerun_stages = connection.execute(
            text(
                "SELECT stage_code, status, attempt FROM stage_runs "
                "WHERE workflow_run_id=:workflow_run_id ORDER BY created_at, stage_code"
            ),
            {"workflow_run_id": confirmed.workflow.workflow_run_id},
        ).mappings().all()
    assert feedback_event["preview_id"] == preview.preview_id
    assert {row["stage_code"] for row in rerun_stages if row["attempt"] == 0} >= {
        "AREA_RESOLUTION",
        "CLAIM_PLAN",
        "EVIDENCE_PLAN",
        "EVIDENCE_RETRIEVAL",
        "EVIDENCE_ASSESS",
        "EVIDENCE_FREEZE",
    }
    assert next(
        row for row in rerun_stages if row["stage_code"] == "INDEPENDENT_SEED"
    )["status"] == "READY"

    republished = 0
    while dispatcher.publish_one():
        republished += 1
    assert republished == 7
    recomputed = ResultService(PostgresResultRepository(postgres_engine)).get_current(
        project_id=project.project_id,
        user_id="user-1",
    )
    assert recomputed.freshness.value == "CURRENT"
    assert recomputed.workflow_run_id == confirmed.workflow.workflow_run_id
    assert processor.errors == []

    cancelling_feedback = FeedbackService(
        PostgresFeedbackRepository(postgres_engine),
        ProjectService(repository),
        ResultService(PostgresResultRepository(postgres_engine)),
        FeedbackAgentFixture(target_funds=30_000_000),
        new_id=lambda: "feedback-preview-cancel",
    )
    cancellable = cancelling_feedback.create_preview(
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="feedback-cancel-preview",
        user_input="자금은 4천만 원으로 바꿀래",
    )
    with postgres_engine.connect() as connection:
        state_count_before_cancel = connection.execute(
            text("SELECT COUNT(*) FROM venture_states WHERE project_id=:project_id"),
            {"project_id": project.project_id},
        ).scalar_one()
        event_count_before_cancel = connection.execute(
            text("SELECT COUNT(*) FROM project_events WHERE project_id=:project_id"),
            {"project_id": project.project_id},
        ).scalar_one()
    cancelled = cancelling_feedback.cancel_preview(
        preview_id=cancellable.preview_id,
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="cancel-feedback-1",
    )
    cancelled_replay = cancelling_feedback.cancel_preview(
        preview_id=cancellable.preview_id,
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="cancel-feedback-1",
    )
    assert cancelled_replay == cancelled
    assert cancelled.preview.status == FeedbackPreviewStatus.CANCELLED
    assert cancelled.state_version is None
    assert cancelled.workflow is None
    with postgres_engine.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM venture_states WHERE project_id=:project_id"),
            {"project_id": project.project_id},
        ).scalar_one() == state_count_before_cancel
        assert connection.execute(
            text("SELECT COUNT(*) FROM project_events WHERE project_id=:project_id"),
            {"project_id": project.project_id},
        ).scalar_one() == event_count_before_cancel

    class HeadChangingFeedbackRuntime(FeedbackAgentFixture):
        replacement: WorkflowRun | None = None

        def __init__(self) -> None:
            super().__init__(target_funds=30_000_000)

        def invoke(self, task: dict[str, Any]) -> dict[str, Any]:
            self.replacement = workflows.start(
                project_id=project.project_id,
                user_id="user-1",
                workflow_code=WorkflowCode.FIRST_PROPOSAL,
                idempotency_key="workflow-integration-2",
            )
            return super().invoke(task)

    changing_runtime = HeadChangingFeedbackRuntime()
    expiring_feedback = FeedbackService(
        PostgresFeedbackRepository(postgres_engine),
        ProjectService(repository),
        ResultService(PostgresResultRepository(postgres_engine)),
        changing_runtime,
        new_id=lambda: "feedback-preview-expiring",
    )
    expired = expiring_feedback.create_preview(
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="feedback-expiring",
        user_input="자금은 4천만 원으로 바꿀래",
    )
    assert expired.status == FeedbackPreviewStatus.EXPIRED
    assert expired.after_founder is None
    assert changing_runtime.replacement is not None
    replacement = changing_runtime.replacement
    stale = ResultService(PostgresResultRepository(postgres_engine)).get_current(
        project_id=project.project_id,
        user_id="user-1",
    )
    assert stale.freshness.value == "STALE"
    assert stale.stale_head_dimensions == ["workflow_generation"]
    assert stale.current_head.workflow_generation == replacement.head.workflow_generation
    assert stale.head.workflow_generation == confirmed.workflow.head.workflow_generation
    with pytest.raises(FeedbackPreconditionError):
        feedback.create_preview(
            project_id=project.project_id,
            user_id="user-1",
            idempotency_key="feedback-stale",
            user_input="자금은 3천만 원으로 바꿀래",
        )


def test_first_proposal_canary_cleaner_removes_only_generated_project_artifacts(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    canary = onboarded_project(repository, user_id="first-proposal-canary-test")
    preserved = onboarded_project(repository, user_id="preserved-user")
    seed_registry = IndependentSeedRegistry.load_default()
    workflows = WorkflowService(
        PostgresWorkflowRepository(
            postgres_engine,
            policy_snapshot_id="policy-v1",
            seed_registry_id=seed_registry.registry_id,
        )
    )
    run = workflows.start(
        project_id=canary.project_id,
        user_id=canary.user_id,
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="canary-workflow",
    )
    with postgres_engine.begin() as connection:
        freeze_stage_id = connection.execute(
            text(
                "SELECT stage_run_id FROM stage_runs "
                "WHERE workflow_run_id=:run_id AND stage_code='EVIDENCE_FREEZE'"
            ),
            {"run_id": run.workflow_run_id},
        ).scalar_one()
        connection.execute(
            text(
                """
                INSERT INTO evidence_records(
                    project_id, evidence_id, record_json, record_digest, created_at
                ) VALUES (
                    :project_id, 'canary-evidence', CAST(:record_json AS JSONB),
                    :record_digest, :created_at
                )
                """
            ),
            {
                "project_id": canary.project_id,
                "record_json": json.dumps({"canary": True}),
                "record_digest": "a" * 64,
                "created_at": datetime.now(UTC),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO evidence_snapshots(
                    evidence_snapshot_id, project_id, workflow_run_id,
                    source_stage_run_id, snapshot_json, snapshot_digest, created_at
                ) VALUES (
                    'canary-snapshot', :project_id, :run_id, :stage_id,
                    CAST(:snapshot_json AS JSONB), :snapshot_digest, :created_at
                )
                """
            ),
            {
                "project_id": canary.project_id,
                "run_id": run.workflow_run_id,
                "stage_id": freeze_stage_id,
                "snapshot_json": json.dumps({"canary": True}),
                "snapshot_digest": "sha256:" + "b" * 64,
                "created_at": datetime.now(UTC),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO evidence_snapshot_records(
                    evidence_snapshot_id, project_id, evidence_id
                ) VALUES ('canary-snapshot', :project_id, 'canary-evidence')
                """
            ),
            {"project_id": canary.project_id},
        )

    PostgresFirstProposalCanaryCleaner(postgres_engine).cleanup(
        project_id=canary.project_id,
        user_id=canary.user_id,
    )

    with postgres_engine.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM venture_projects WHERE project_id=:project_id"),
            {"project_id": canary.project_id},
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT COUNT(*) FROM evidence_snapshots WHERE project_id=:project_id"),
            {"project_id": canary.project_id},
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT COUNT(*) FROM evidence_records WHERE project_id=:project_id"),
            {"project_id": canary.project_id},
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT COUNT(*) FROM workflow_outbox "
                "WHERE payload_json->>'workflow_run_id'=:run_id"
            ),
            {"run_id": run.workflow_run_id},
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT COUNT(*) FROM idempotency_records WHERE user_id=:user_id"),
            {"user_id": canary.user_id},
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT COUNT(*) FROM workflow_idempotency_records "
                "WHERE user_id=:user_id"
            ),
            {"user_id": canary.user_id},
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT COUNT(*) FROM venture_projects WHERE project_id=:project_id"),
            {"project_id": preserved.project_id},
        ).scalar_one() == 1


def create_ready_stage(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> tuple[Project, str, str, WorkflowService]:
    project = onboarded_project(repository)
    workflows = WorkflowService(
        PostgresWorkflowRepository(
            postgres_engine, policy_snapshot_id="policy-v1", seed_registry_id="seed-v1"
        )
    )
    workflows.start(
        project_id=project.project_id,
        user_id="user-1",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="workflow-1",
    )
    with postgres_engine.connect() as connection:
        stage_id, input_digest = connection.execute(
            text("SELECT stage_run_id, input_digest FROM stage_runs WHERE status='READY'")
        ).one()
    return project, stage_id, input_digest, workflows


def create_commit_stage(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> tuple[Project, str, str, WorkflowService]:
    project, _stage_id, _input_digest, workflows = create_ready_stage(repository, postgres_engine)
    with postgres_engine.begin() as connection:
        connection.execute(
            text("UPDATE workflow_runs SET status='RUNNING' WHERE project_id=:project_id"),
            {"project_id": project.project_id},
        )
        connection.execute(
            text(
                "UPDATE stage_runs SET status=CASE WHEN stage_code='COMMIT_RESULT' "
                "THEN 'READY' ELSE 'SUCCEEDED' END"
            )
        )
        stage_id, input_digest = connection.execute(
            text(
                "SELECT stage_run_id, input_digest FROM stage_runs WHERE stage_code='COMMIT_RESULT'"
            )
        ).one()
    return project, stage_id, input_digest, workflows


@pytest.mark.parametrize(
    ("disposition", "workflow_status", "current_stage_status", "other_stage_status"),
    [
        ("WAITING_FOR_HUMAN", "WAITING_FOR_HUMAN", "WAITING_FOR_HUMAN", "PENDING"),
        ("ABSTAIN", "PARTIAL", "SKIPPED", "CANCELLED"),
    ],
)
def test_noncontinuing_stage_does_not_publish_or_persist_a_result(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
    disposition: str,
    workflow_status: str,
    current_stage_status: str,
    other_stage_status: str,
) -> None:
    project, stage_id, input_digest, workflows = create_ready_stage(repository, postgres_engine)
    execution = PostgresStageExecutionRepository(postgres_engine)
    lease = execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-1",
        expected_input_digest=input_digest,
    )
    assert lease is not None

    assert (
        execution.checkpoint(
            stage_run_id=stage_id,
            lease_token=lease.lease_token,
            input_digest=lease.input_digest,
            result={
                "stage_control": {
                    "disposition": disposition,
                    "reason_codes": ["AREA_SELECTION_REQUIRED"],
                },
                "area_resolution": {"selected": None},
            },
        )
        == CheckpointOutcome.APPLIED
    )

    with postgres_engine.connect() as connection:
        stored_workflow_status = connection.execute(
            text("SELECT status FROM workflow_runs WHERE project_id=:project_id"),
            {"project_id": project.project_id},
        ).scalar_one()
        current_status = connection.execute(
            text("SELECT status FROM stage_runs WHERE stage_run_id=:stage_run_id"),
            {"stage_run_id": stage_id},
        ).scalar_one()
        other_statuses = set(
            connection.execute(
                text("SELECT status FROM stage_runs WHERE stage_run_id<>:stage_run_id"),
                {"stage_run_id": stage_id},
            ).scalars()
        )
        outbox_count = connection.execute(text("SELECT COUNT(*) FROM workflow_outbox")).scalar_one()
        result_count = connection.execute(text("SELECT COUNT(*) FROM result_bundles")).scalar_one()
    assert stored_workflow_status == workflow_status
    assert current_status == current_stage_status
    assert other_statuses == {other_stage_status}
    assert outbox_count == 1
    assert result_count == 0
    progress = workflows.get_progress(
        project_id=project.project_id,
        workflow_run_id=lease.workflow_run_id,
        user_id="user-1",
    )
    assert progress.poll_after_ms is None
    if disposition == "WAITING_FOR_HUMAN":
        assert [request.model_dump(mode="json") for request in progress.human_review_requests] == [
            {
                "stage_run_id": stage_id,
                "stage_code": "AREA_RESOLUTION",
                "reason_codes": ["AREA_SELECTION_REQUIRED"],
            }
        ]
        assert progress.current_stage_codes == ["AREA_RESOLUTION"]
    else:
        assert progress.human_review_requests == []
        assert progress.terminal_reason_codes == ["AREA_SELECTION_REQUIRED"]


def test_stage_context_loads_fenced_state_and_direct_dependency_results(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project, stage_id, input_digest, _workflows = create_ready_stage(repository, postgres_engine)
    execution = PostgresStageExecutionRepository(postgres_engine)
    root_lease = execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-1",
        expected_input_digest=input_digest,
    )
    assert root_lease is not None
    root_result = {"area_resolution": "UNRESOLVED"}
    assert (
        execution.checkpoint(
            stage_run_id=stage_id,
            lease_token=root_lease.lease_token,
            input_digest=root_lease.input_digest,
            result=root_result,
        )
        == CheckpointOutcome.APPLIED
    )

    with postgres_engine.connect() as connection:
        next_stage_id, next_digest = connection.execute(
            text(
                "SELECT stage_run_id, input_digest FROM stage_runs "
                "WHERE stage_code='CLAIM_PLAN' AND status='READY'"
            )
        ).one()
    next_lease = execution.claim(
        stage_run_id=next_stage_id,
        worker_id="worker-1",
        expected_input_digest=next_digest,
    )
    assert next_lease is not None

    loaded = PostgresStageContextRepository(postgres_engine).load(next_lease)

    assert loaded.project_id == project.project_id
    assert loaded.state.project_id == project.project_id
    assert loaded.state.state_version == next_lease.head.state_version
    assert loaded.dependency_results == {"AREA_RESOLUTION": root_result}


def test_first_proposal_dag_promotes_ready_stages_and_joins_both_branches(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project = onboarded_project(repository)
    workflows = WorkflowService(
        PostgresWorkflowRepository(
            postgres_engine, policy_snapshot_id="policy-v1", seed_registry_id="seed-v1"
        )
    )
    run = workflows.start(
        project_id=project.project_id,
        user_id="user-1",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="workflow-1",
    )
    execution = PostgresStageExecutionRepository(postgres_engine)
    expected_batches = [
        {"AREA_RESOLUTION"},
        {"CLAIM_PLAN"},
        {"EVIDENCE_PLAN"},
        {"EVIDENCE_RETRIEVAL"},
        {"EVIDENCE_ASSESS"},
        {"EVIDENCE_FREEZE"},
        {"INDEPENDENT_SEED", "FRANCHISE_ELIGIBILITY"},
        {"PROPOSE_INDEPENDENT", "PROPOSE_FRANCHISE"},
        {"CALCULATE_GATE_RANK"},
        {"CANDIDATE_AUDIT"},
        {"COMMIT_RESULT"},
    ]
    expected_snapshot_id: str | None = None

    for expected in expected_batches:
        with postgres_engine.connect() as connection:
            ready = (
                connection.execute(
                    text(
                        "SELECT stage_run_id, stage_code, input_digest FROM stage_runs "
                        "WHERE workflow_run_id=:workflow_run_id AND status='READY'"
                    ),
                    {"workflow_run_id": run.workflow_run_id},
                )
                .mappings()
                .all()
            )
        assert {stage["stage_code"] for stage in ready} == expected
        for stage in ready:
            lease = execution.claim(
                stage_run_id=stage["stage_run_id"],
                worker_id="worker-1",
                expected_input_digest=stage["input_digest"],
            )
            assert lease is not None
            if stage["stage_code"] == "COMMIT_RESULT":
                result = result_payload(project_id=project.project_id)
            elif stage["stage_code"] == "EVIDENCE_FREEZE":
                snapshot_body = {
                    "schema_version": "1.0.0",
                    "project_id": project.project_id,
                    "workflow_run_id": run.workflow_run_id,
                    "source_stage_run_id": stage["stage_run_id"],
                    "evidence_records": [],
                    "conflicts": [],
                    "missing_claim_ids": ["claim:AREA_PROFILE"],
                    "reason_codes": ["MISSING_EVIDENCE"],
                    "retrieval_completeness": "UNAVAILABLE",
                    "franchise_universe": [],
                }
                snapshot_digest = hashlib.sha256(rfc8785.dumps(snapshot_body)).hexdigest()
                expected_snapshot_id = f"evidence-{snapshot_digest[:40]}"
                result = {
                    "evidence_freeze": {
                        "snapshot_id": expected_snapshot_id,
                        "snapshot_digest": f"sha256:{snapshot_digest}",
                        **snapshot_body,
                    }
                }
            else:
                result = {"stage": stage["stage_code"]}
            assert (
                execution.checkpoint(
                    stage_run_id=stage["stage_run_id"],
                    lease_token=lease.lease_token,
                    input_digest=lease.input_digest,
                    result=result,
                )
                == CheckpointOutcome.APPLIED
            )

    completed = workflows.get(
        project_id=project.project_id,
        workflow_run_id=run.workflow_run_id,
        user_id="user-1",
    )
    assert completed.status == WorkflowStatus.SUCCEEDED
    with postgres_engine.connect() as connection:
        statuses = set(
            connection.execute(
                text("SELECT status FROM stage_runs WHERE workflow_run_id=:workflow_run_id"),
                {"workflow_run_id": run.workflow_run_id},
            ).scalars()
        )
        ready_messages = connection.execute(
            text("SELECT COUNT(*) FROM workflow_outbox WHERE topic='WORKFLOW_STAGE_READY'")
        ).scalar_one()
        snapshot = (
            connection.execute(
                text(
                    "SELECT evidence_snapshot_id, project_id, workflow_run_id "
                    "FROM evidence_snapshots WHERE workflow_run_id=:workflow_run_id"
                ),
                {"workflow_run_id": run.workflow_run_id},
            )
            .mappings()
            .one()
        )
        head_snapshot_ids = (
            connection.execute(
                text(
                    "SELECT w.evidence_snapshot_id AS workflow_snapshot_id, "
                    "h.evidence_snapshot_id AS project_snapshot_id "
                    "FROM workflow_runs w JOIN project_heads h ON h.project_id=w.project_id "
                    "WHERE w.workflow_run_id=:workflow_run_id"
                ),
                {"workflow_run_id": run.workflow_run_id},
            )
            .mappings()
            .one()
        )
    assert statuses == {"SUCCEEDED"}
    assert ready_messages == 13
    assert expected_snapshot_id is not None
    assert snapshot == {
        "evidence_snapshot_id": expected_snapshot_id,
        "project_id": project.project_id,
        "workflow_run_id": run.workflow_run_id,
    }
    assert head_snapshot_ids == {
        "workflow_snapshot_id": expected_snapshot_id,
        "project_snapshot_id": expected_snapshot_id,
    }
    next_run = workflows.start(
        project_id=project.project_id,
        user_id="user-1",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="workflow-2",
    )
    assert next_run.head.evidence_snapshot_id == expected_snapshot_id


def valid_candidate(*, project_id: str, state_version: int = 1) -> dict[str, object]:
    initial_cash = {
        "currency": "KRW",
        "low": 40_000_000,
        "base": 45_000_000,
        "high": 50_000_000,
        "provenance_refs": ["evidence-1"],
    }
    monthly_fixed_cost = {
        "currency": "KRW",
        "low": 5_000_000,
        "base": 6_000_000,
        "high": 7_000_000,
        "provenance_refs": ["evidence-1"],
    }
    return {
        "schema_version": "2.0.0",
        "candidate_id": "candidate-1",
        "project_id": project_id,
        "state_version": state_version,
        "case_type": "INDEPENDENT",
        "display_name": "소형 개인카페",
        "review_status": "REVIEW_RECOMMENDED",
        "reason_codes": ["CURRENT_CONSTRAINTS_SATISFIED"],
        "summary": "현재 자금 범위에서 다음 검토 가치가 있는 후보",
        "rank": 1,
        "rank_basis": "ECONOMIC_AND_FOUNDER_FIT",
        "is_primary_next_review": True,
        "franchise": None,
        "independent_model": {"model_id": "independent-v1", "adjusted_fields": []},
        "evidence_refs": ["evidence-1"],
        "assumption_refs": [],
        "financial_summary": {
            "initial_cash": initial_cash,
            "monthly_fixed_cost": monthly_fixed_cost,
            "break_even_monthly_sales_krw": 15_000_000,
            "required_daily_orders": 80,
            "unknown_cost_fields": [],
        },
        "missing_fields": [],
        "risks": [],
        "counterfactuals": [
            {
                "variable": "monthly_rent",
                "condition": "월세가 기준보다 15% 높아짐",
                "decision_impact": "다음 검토 우선순위를 재계산",
            }
        ],
        "next_actions": ["실제 점포 조건 확인"],
    }


def result_payload(*, project_id: str, state_version: int = 1) -> dict[str, object]:
    return {
        "result_bundle": {
            "candidates": [valid_candidate(project_id=project_id, state_version=state_version)],
            "primary_candidate_id": "candidate-1",
            "audit_status": "PASSED",
        }
    }


def test_result_bundle_checkpoint_is_atomic_and_owner_scoped(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, stage_id, input_digest, _workflows = create_commit_stage(repository, postgres_engine)
    execution = PostgresStageExecutionRepository(
        postgres_engine,
        new_result_id=lambda: "result-1",
    )
    lease = execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-1",
        expected_input_digest=input_digest,
    )
    assert lease is not None

    assert (
        execution.checkpoint(
            stage_run_id=stage_id,
            lease_token=lease.lease_token,
            input_digest=lease.input_digest,
            result=result_payload(project_id=project.project_id),
        )
        == CheckpointOutcome.APPLIED
    )
    assert (
        execution.checkpoint(
            stage_run_id=stage_id,
            lease_token=lease.lease_token,
            input_digest=lease.input_digest,
            result=result_payload(project_id=project.project_id),
        )
        == CheckpointOutcome.DUPLICATE_DISCARDED
    )

    results = ResultService(PostgresResultRepository(postgres_engine))
    loaded = results.get_current(project_id=project.project_id, user_id="user-1")
    assert loaded.result_bundle_id == "result-1"
    assert loaded.workflow_run_id == lease.workflow_run_id
    assert loaded.head == lease.head
    assert loaded.freshness.value == "CURRENT"
    assert loaded.stale_head_dimensions == []
    assert loaded.current_head == loaded.head
    assert loaded.primary_candidate_id == "candidate-1"
    assert loaded.candidates[0]["display_name"] == "소형 개인카페"
    with pytest.raises(ResultNotFoundError):
        results.get_current(project_id=project.project_id, user_id="user-2")

    with postgres_engine.connect() as connection:
        result_count = connection.execute(text("SELECT COUNT(*) FROM result_bundles")).scalar_one()
        pointer = connection.execute(
            text(
                "SELECT current_result_bundle_id FROM venture_projects WHERE project_id=:project_id"
            ),
            {"project_id": project.project_id},
        ).scalar_one()
        committed_event = connection.execute(
            text(
                "SELECT event_json FROM workflow_events "
                "WHERE workflow_run_id=:workflow_run_id "
                "AND event_type='RESULT_BUNDLE_COMMITTED'"
            ),
            {"workflow_run_id": lease.workflow_run_id},
        ).scalar_one()
    assert result_count == 1
    assert pointer == "result-1"
    assert committed_event == {
        "result_bundle_id": "result-1",
        "primary_candidate_id": "candidate-1",
        "audit_status": "PASSED",
    }

    monkeypatch.setenv(
        "DATABASE_URL",
        postgres_engine.url.render_as_string(hide_password=False),
    )
    from fastapi.testclient import TestClient

    document_storage = DocumentStorageFixture()
    document_service = DocumentService(postgres_engine, document_storage)
    extraction_runtime = DocumentExtractionAgentFixture()
    extraction_service = DocumentExtractionService(postgres_engine, extraction_runtime)
    with TestClient(
        create_app(
            identity_verifier=FixedIdentityVerifier(),
            internal_identity_verifier=FixedIdentityVerifier(),
            document_service=document_service,
            document_extraction_service=extraction_service,
        )
    ) as client:
        response = client.get(
            f"/v1/projects/{project.project_id}/result",
            headers={"Authorization": "Bearer valid-token"},
        )
        missing = client.post(
            f"/v1/projects/{project.project_id}/candidate-selections",
            headers={
                "Authorization": "Bearer valid-token",
                "Idempotency-Key": "selection-missing",
            },
            json={
                "result_bundle_id": loaded.result_bundle_id,
                "candidate_id": "candidate-not-in-result",
                "expected_head": loaded.head.model_dump(mode="json"),
            },
        )
        selected = client.post(
            f"/v1/projects/{project.project_id}/candidate-selections",
            headers={
                "Authorization": "Bearer valid-token",
                "Idempotency-Key": "selection-1",
            },
            json={
                "result_bundle_id": loaded.result_bundle_id,
                "candidate_id": "candidate-1",
                "expected_head": loaded.head.model_dump(mode="json"),
            },
        )
        replay = client.post(
            f"/v1/projects/{project.project_id}/candidate-selections",
            headers={
                "Authorization": "Bearer valid-token",
                "Idempotency-Key": "selection-1",
            },
            json={
                "result_bundle_id": loaded.result_bundle_id,
                "candidate_id": "candidate-1",
                "expected_head": loaded.head.model_dump(mode="json"),
            },
        )
        project_after = client.get(
            f"/v1/projects/{project.project_id}",
            headers={"Authorization": "Bearer valid-token"},
        )
        content = b"%PDF-1.7 caffemate"
        content_sha256 = hashlib.sha256(content).hexdigest()
        upload = client.post(
            f"/v1/projects/{project.project_id}/documents/uploads",
            headers={
                "Authorization": "Bearer valid-token",
                "Idempotency-Key": "document-upload-1",
            },
            json={
                "document_type": "COMMERCIAL_LEASE",
                "filename": "../lease.pdf",
                "content_type": "application/pdf",
                "size_bytes": len(content),
                "sha256": content_sha256,
            },
        )
        assert upload.status_code == 201, upload.json()
        upload_body = upload.json()
        assert upload_body["object_path"].startswith(
            f"projects/{project.project_id}/documents/"
        )
        assert ".." not in upload_body["object_path"]
        assert upload_body["required_headers"] == {
            "Content-Type": "application/pdf",
            "x-goog-meta-caffemate-sha256": content_sha256,
        }
        document_storage.objects[upload_body["object_path"]] = StoredObject(
            content_type="application/pdf",
            size_bytes=len(content),
            sha256=content_sha256,
        )
        completed_upload = client.post(
            f"/v1/projects/{project.project_id}/documents/uploads:complete",
            headers={"Authorization": "Bearer valid-token"},
            json={"document_revision_id": upload_body["document_revision_id"]},
        )
        scanned = client.post(
            "/internal/v1/documents/"
            f"{upload_body['document_revision_id']}:scan-result",
            headers={"Authorization": "Bearer valid-token"},
            json={"project_id": project.project_id, "clean": True, "threat_codes": []},
        )
        parser_result = client.post(
            "/internal/v1/documents/"
            f"{upload_body['document_revision_id']}:parser-result",
            headers={"Authorization": "Bearer valid-token"},
            json={
                "project_id": project.project_id,
                "document_id": upload_body["document_id"],
                "parser_version": "layout-parser.v1",
                "blocks": [
                    {
                        "block_id": "block-1",
                        "text": "보증금 5,000만원",
                        "anchor": {
                            "document_revision_id": upload_body["document_revision_id"],
                            "page_index": 0,
                            "section_path": "임대조건",
                            "table_id": None,
                            "row": None,
                            "column": None,
                            "bbox": None,
                        },
                    }
                ],
                "prompt_injection_flags": [],
            },
        )
        form_get = client.get(
            f"/v1/projects/{project.project_id}/documents/"
            f"{upload_body['document_revision_id']}/extraction-form",
            headers={"Authorization": "Bearer valid-token"},
        )
        form_body = form_get.json()
        deposit_id = next(
            field["field_id"]
            for field in form_body["fields"]
            if field["claim_type"] == "LEASE_DEPOSIT"
        )
        edited_form = client.put(
            f"/v1/projects/{project.project_id}/documents/"
            f"{upload_body['document_revision_id']}/extraction-form",
            headers={"Authorization": "Bearer valid-token"},
            json={
                "expected_state_version": 2,
                "edits": [{"field_id": deposit_id, "value": 45_000_000}],
            },
        )
        applied_form = client.post(
            f"/v1/projects/{project.project_id}/documents/"
            f"{upload_body['document_revision_id']}/extraction-form:apply",
            headers={
                "Authorization": "Bearer valid-token",
                "Idempotency-Key": "document-apply-1",
            },
            json={
                "expected_state_version": 2,
                "expected_form_digest": edited_form.json()["form_digest"],
            },
        )
        applied_replay = client.post(
            f"/v1/projects/{project.project_id}/documents/"
            f"{upload_body['document_revision_id']}/extraction-form:apply",
            headers={
                "Authorization": "Bearer valid-token",
                "Idempotency-Key": "document-apply-1",
            },
            json={
                "expected_state_version": 2,
                "expected_form_digest": edited_form.json()["form_digest"],
            },
        )
        stale_apply = client.post(
            f"/v1/projects/{project.project_id}/documents/"
            f"{upload_body['document_revision_id']}/extraction-form:apply",
            headers={
                "Authorization": "Bearer valid-token",
                "Idempotency-Key": "document-apply-stale",
            },
            json={
                "expected_state_version": 2,
                "expected_form_digest": edited_form.json()["form_digest"],
            },
        )
        second_upload = client.post(
            f"/v1/projects/{project.project_id}/documents/uploads",
            headers={
                "Authorization": "Bearer valid-token",
                "Idempotency-Key": "document-upload-2",
            },
            json={
                "document_type": "COMMERCIAL_LEASE",
                "filename": "lease-revision-2.pdf",
                "content_type": "application/pdf",
                "size_bytes": len(content),
                "sha256": content_sha256,
            },
        ).json()
        document_storage.objects[second_upload["object_path"]] = StoredObject(
            content_type="application/pdf",
            size_bytes=len(content),
            sha256=content_sha256,
        )
        client.post(
            f"/v1/projects/{project.project_id}/documents/uploads:complete",
            headers={"Authorization": "Bearer valid-token"},
            json={"document_revision_id": second_upload["document_revision_id"]},
        )
        client.post(
            f"/internal/v1/documents/{second_upload['document_revision_id']}:scan-result",
            headers={"Authorization": "Bearer valid-token"},
            json={"project_id": project.project_id, "clean": True, "threat_codes": []},
        )
        second_parser = client.post(
            f"/internal/v1/documents/{second_upload['document_revision_id']}:parser-result",
            headers={"Authorization": "Bearer valid-token"},
            json={
                "project_id": project.project_id,
                "document_id": second_upload["document_id"],
                "parser_version": "layout-parser.v1",
                "blocks": [
                    {
                        "block_id": "block-2",
                        "text": "보증금 5,000만원",
                        "anchor": {
                            "document_revision_id": second_upload["document_revision_id"],
                            "page_index": 0,
                        },
                    }
                ],
            },
        )
        second_form = second_parser.json()
        conflicting_apply = client.post(
            f"/v1/projects/{project.project_id}/documents/"
            f"{second_upload['document_revision_id']}/extraction-form:apply",
            headers={
                "Authorization": "Bearer valid-token",
                "Idempotency-Key": "document-apply-2",
            },
            json={
                "expected_state_version": 3,
                "expected_form_digest": second_form["form_digest"],
            },
        )
        project_after_documents = client.get(
            f"/v1/projects/{project.project_id}",
            headers={"Authorization": "Bearer valid-token"},
        )
        download = client.get(
            f"/v1/projects/{project.project_id}/documents/"
            f"{upload_body['document_revision_id']}/download",
            headers={"Authorization": "Bearer valid-token"},
        )
        bad_upload = client.post(
            f"/v1/projects/{project.project_id}/documents/uploads",
            headers={
                "Authorization": "Bearer valid-token",
                "Idempotency-Key": "document-upload-bad",
            },
            json={
                "document_type": "EQUIPMENT_QUOTE",
                "filename": "quote.pdf",
                "content_type": "application/pdf",
                "size_bytes": len(content),
                "sha256": content_sha256,
            },
        )
        bad_body = bad_upload.json()
        document_storage.objects[bad_body["object_path"]] = StoredObject(
            content_type="image/png",
            size_bytes=len(content) + 1,
            sha256="0" * 64,
        )
        quarantined = client.post(
            f"/v1/projects/{project.project_id}/documents/uploads:complete",
            headers={"Authorization": "Bearer valid-token"},
            json={"document_revision_id": bad_body["document_revision_id"]},
        )
        quarantined_download = client.get(
            f"/v1/projects/{project.project_id}/documents/"
            f"{bad_body['document_revision_id']}/download",
            headers={"Authorization": "Bearer valid-token"},
        )
    assert response.status_code == 200
    assert response.json()["result_bundle_id"] == "result-1"
    assert missing.status_code == 409
    assert missing.json()["code"] == "CANDIDATE_SELECTION_PRECONDITION_FAILED"
    assert selected.status_code == 201
    assert replay.json() == selected.json()
    selection = selected.json()
    assert selection["candidate_id"] == "candidate-1"
    assert selection["selected_state_version"] == 2
    assert selection["property_intake_enabled"] is True
    assert selection["document_intake_enabled"] is True
    assert selection["is_final_go_decision"] is False
    assert {item["code"] for item in selection["required_evidence"]} >= {
        "PROPERTY_LISTING",
        "LEASE_TERMS",
        "INTERIOR_QUOTE",
        "EQUIPMENT_QUOTE",
        "SUPPLIER_TERMS",
    }
    assert project_after.status_code == 200
    state = project_after.json()["state"]
    assert state["active_case_id"] == "candidate-1"
    assert state["status"] == "WAITING_FOR_HUMAN"
    assert state["venture_cases"] == [
        {
            "case_id": "candidate-1",
            "case_type": "INDEPENDENT",
            "maturity": "CANDIDATE",
            "status": "SELECTED",
            "display_name": "소형 개인카페",
            "franchise_eligibility": "NOT_APPLICABLE",
            "confirmed_claim_ids": ["evidence-1"],
            "assumption_ids": [],
            "missing_fields": [],
        }
    ]
    assert completed_upload.status_code == 200
    assert completed_upload.json()["status"] == "SCAN_PENDING"
    assert scanned.status_code == 200
    assert scanned.json()["status"] == "READY_FOR_PARSING"
    assert parser_result.status_code == 200, parser_result.json()
    assert form_get.json() == parser_result.json()
    extraction_form = parser_result.json()
    assert extraction_form["expected_state_version"] == 2
    assert extraction_form["apply_label"] == "반영하고 다시 계산"
    deposit_field = next(
        field for field in extraction_form["fields"] if field["claim_type"] == "LEASE_DEPOSIT"
    )
    assert deposit_field["current_value"] == 50_000_000
    assert deposit_field["extraction_status"] == "AUTO_FILLED"
    assert deposit_field["anchor"]["page_index"] == 0
    assert len(extraction_runtime.tasks) == 2
    assert edited_form.status_code == 200
    edited_deposit = next(
        field
        for field in edited_form.json()["fields"]
        if field["claim_type"] == "LEASE_DEPOSIT"
    )
    assert edited_deposit["current_value"] == 45_000_000
    assert edited_deposit["edit_status"] == "EDITED"
    assert applied_form.status_code == 201, applied_form.json()
    assert applied_replay.json() == applied_form.json()
    assert stale_apply.status_code == 409
    assert stale_apply.json()["code"] == "DOCUMENT_PRECONDITION_FAILED"
    application = applied_form.json()
    assert application["applied_state_version"] == 3
    assert application["requires_human_review"] is False
    assert application["conflicts"] == []
    assert application["claims"][0]["value"] == 45_000_000
    assert application["recompute_workflow_run_id"]
    assert conflicting_apply.status_code == 201, conflicting_apply.json()
    conflicting_application = conflicting_apply.json()
    assert conflicting_application["applied_state_version"] == 4
    assert conflicting_application["requires_human_review"] is True
    assert conflicting_application["conflicts"][0]["claim_type"] == "LEASE_DEPOSIT"
    assert len(conflicting_application["conflicts"][0]["competing_claim_ids"]) == 2
    document_state = project_after_documents.json()["state"]
    assert document_state["state_version"] == 4
    assert document_state["status"] == "WAITING_FOR_HUMAN"
    assert document_state["venture_cases"][0]["maturity"] == "DOCUMENT_LINKED"
    assert document_state["conflict_ids"] == [
        conflicting_application["conflicts"][0]["conflict_id"]
    ]
    assert download.status_code == 200
    assert download.json()["download_url"].startswith("https://download.invalid/")
    assert quarantined.status_code == 200
    assert quarantined.json()["status"] == "QUARANTINED"
    assert quarantined.json()["failure_codes"] == [
        "MIME_MISMATCH",
        "SIZE_MISMATCH",
        "CHECKSUM_MISMATCH",
    ]
    assert quarantined_download.status_code == 409
    with postgres_engine.connect() as connection:
        document_topics = connection.execute(
            text(
                "SELECT topic FROM workflow_outbox "
                "WHERE aggregate_id=:revision_id ORDER BY outbox_id"
            ),
            {"revision_id": upload_body["document_revision_id"]},
        ).scalars().all()
    assert document_topics == ["DOCUMENT_SCAN_REQUESTED", "DOCUMENT_PARSE_REQUESTED"]


def test_document_storage_canary_exercises_upload_agent_extraction_and_cleanup(
    postgres_engine: Engine,
) -> None:
    storage = DocumentStorageFixture()
    report = DocumentStorageCanary(
        engine=postgres_engine,
        documents=DocumentService(postgres_engine, storage),
        extraction=DocumentExtractionService(
            postgres_engine, DocumentExtractionAgentFixture()
        ),
        storage=storage,  # type: ignore[arg-type]
        policy_snapshot_id="policy-v1",
        seed_registry_id=IndependentSeedRegistry.load_default().registry_id,
        transport=DocumentCanaryTransportFixture(storage),
        new_id=lambda: "fixed",
    ).run()

    assert report.status == "verified"
    assert report.upload_status == "SCAN_PENDING"
    assert report.scan_status == "READY_FOR_PARSING"
    assert report.extraction_status == "EXTRACTION_READY"
    assert report.agent_result_statuses == ("COMPLETE",)
    assert report.extracted_field_count == 1
    assert report.download_bytes > 0
    assert storage.objects == {}
    assert storage.contents == {}
    with postgres_engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT COUNT(*) FROM venture_projects "
                "WHERE project_id='document-canary-fixed'"
            )
        ).scalar_one() == 0
        assert connection.execute(
            text(
                "SELECT COUNT(*) FROM workflow_outbox "
                "WHERE payload_json->>'project_id'='document-canary-fixed'"
            )
        ).scalar_one() == 0


def test_invalid_result_contract_rolls_back_bundle_and_stage_checkpoint(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project, stage_id, input_digest, _workflows = create_commit_stage(repository, postgres_engine)
    execution = PostgresStageExecutionRepository(postgres_engine)
    lease = execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-1",
        expected_input_digest=input_digest,
    )
    assert lease is not None

    with pytest.raises(ContractValidationError):
        execution.checkpoint(
            stage_run_id=stage_id,
            lease_token=lease.lease_token,
            input_digest=lease.input_digest,
            result=result_payload(project_id="another-project"),
        )

    with postgres_engine.connect() as connection:
        stage_status, result_count, pointer, result_event_count = connection.execute(
            text(
                "SELECT s.status, (SELECT COUNT(*) FROM result_bundles), "
                "p.current_result_bundle_id, "
                "(SELECT COUNT(*) FROM workflow_events e "
                "WHERE e.workflow_run_id=w.workflow_run_id "
                "AND e.event_type='RESULT_BUNDLE_COMMITTED') "
                "FROM stage_runs s "
                "JOIN workflow_runs w ON w.workflow_run_id=s.workflow_run_id "
                "JOIN venture_projects p ON p.project_id=w.project_id "
                "WHERE s.stage_run_id=:stage_run_id"
            ),
            {"stage_run_id": stage_id},
        ).one()
    assert (stage_status, result_count, pointer, result_event_count) == (
        "RUNNING",
        0,
        None,
        0,
    )

    assert (
        execution.checkpoint(
            stage_run_id=stage_id,
            lease_token=lease.lease_token,
            input_digest=lease.input_digest,
            result=result_payload(project_id=project.project_id),
        )
        == CheckpointOutcome.APPLIED
    )


def test_cancelled_worker_bundle_is_discarded_before_payload_validation(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project, stage_id, input_digest, workflows = create_commit_stage(repository, postgres_engine)
    execution = PostgresStageExecutionRepository(postgres_engine)
    lease = execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-1",
        expected_input_digest=input_digest,
    )
    assert lease is not None
    workflows.cancel(
        project_id=project.project_id,
        workflow_run_id=lease.workflow_run_id,
        user_id="user-1",
        idempotency_key="cancel-1",
    )

    assert (
        execution.checkpoint(
            stage_run_id=stage_id,
            lease_token=lease.lease_token,
            input_digest=lease.input_digest,
            result={"result_bundle": {"malformed": True}},
        )
        == CheckpointOutcome.CANCELLED_DISCARDED
    )
    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM result_bundles")).scalar_one() == 0


def test_expired_stage_lease_is_reclaimed_and_old_worker_cannot_checkpoint(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    _project, stage_id, input_digest, _workflows = create_ready_stage(repository, postgres_engine)
    clock = [datetime(2026, 8, 21, tzinfo=UTC)]
    tokens = iter(["old-token", "new-token"])
    execution = PostgresStageExecutionRepository(
        postgres_engine,
        now=lambda: clock[0],
        new_token=lambda: next(tokens),
    )

    old = execution.claim(
        stage_run_id=stage_id, worker_id="worker-1", expected_input_digest=input_digest
    )
    assert old is not None
    assert (
        execution.claim(
            stage_run_id=stage_id, worker_id="worker-2", expected_input_digest=input_digest
        )
        is None
    )
    clock[0] += timedelta(seconds=LEASE_SECONDS + 1)
    new = execution.claim(
        stage_run_id=stage_id, worker_id="worker-2", expected_input_digest=input_digest
    )
    assert new is not None
    assert new.attempt == 2
    assert (
        execution.checkpoint(
            stage_run_id=stage_id,
            lease_token=old.lease_token,
            input_digest=old.input_digest,
            result={"worker": "old"},
        )
        == CheckpointOutcome.LEASE_REJECTED
    )
    assert (
        execution.checkpoint(
            stage_run_id=stage_id,
            lease_token=new.lease_token,
            input_digest=new.input_digest,
            result={"worker": "new"},
        )
        == CheckpointOutcome.APPLIED
    )
    assert (
        execution.checkpoint(
            stage_run_id=stage_id,
            lease_token=new.lease_token,
            input_digest=new.input_digest,
            result={"worker": "new"},
        )
        == CheckpointOutcome.DUPLICATE_DISCARDED
    )


def test_retryable_stage_failure_is_fenced_and_third_attempt_terminates_workflow(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    _project, stage_id, input_digest, _workflows = create_ready_stage(repository, postgres_engine)
    tokens = iter(["attempt-1", "attempt-2", "attempt-3"])
    execution = PostgresStageExecutionRepository(
        postgres_engine,
        new_token=lambda: next(tokens),
    )
    failure = StageFailure(code="STAGE_PROCESSING_ERROR", retryable=True)

    first = execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-1",
        expected_input_digest=input_digest,
    )
    assert first is not None
    assert (
        execution.record_failure(
            stage_run_id=stage_id,
            lease_token=first.lease_token,
            input_digest=input_digest,
            failure=failure,
        )
        == FailureOutcome.RETRY_SCHEDULED
    )

    second = execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-2",
        expected_input_digest=input_digest,
    )
    assert second is not None
    assert (
        execution.record_failure(
            stage_run_id=stage_id,
            lease_token=first.lease_token,
            input_digest=input_digest,
            failure=failure,
        )
        == FailureOutcome.LEASE_REJECTED
    )
    assert (
        execution.record_failure(
            stage_run_id=stage_id,
            lease_token=second.lease_token,
            input_digest=input_digest,
            failure=failure,
        )
        == FailureOutcome.RETRY_SCHEDULED
    )

    third = execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-3",
        expected_input_digest=input_digest,
    )
    assert third is not None
    assert third.attempt == 3
    assert (
        execution.record_failure(
            stage_run_id=stage_id,
            lease_token=third.lease_token,
            input_digest=input_digest,
            failure=failure,
        )
        == FailureOutcome.TERMINAL_FAILED
    )

    with postgres_engine.connect() as connection:
        stage_status, stored_failure = connection.execute(
            text("SELECT status, failure_json FROM stage_runs WHERE stage_run_id=:id"),
            {"id": stage_id},
        ).one()
        workflow_status = connection.execute(text("SELECT status FROM workflow_runs")).scalar_one()
        events = list(
            connection.execute(
                text(
                    "SELECT event_type FROM workflow_events "
                    "WHERE event_type LIKE 'STAGE_%' ORDER BY sequence_id"
                )
            ).scalars()
        )
    assert stage_status == "FAILED"
    assert stored_failure == {"code": "STAGE_PROCESSING_ERROR", "retryable": True}
    assert workflow_status == "FAILED"
    assert events == ["STAGE_RETRY_SCHEDULED", "STAGE_RETRY_SCHEDULED", "STAGE_FAILED"]
    assert (
        execution.claim(
            stage_run_id=stage_id,
            worker_id="worker-4",
            expected_input_digest=input_digest,
        )
        is None
    )


def test_nonretryable_failure_terminates_on_first_attempt_without_raw_message(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    _project, stage_id, input_digest, _workflows = create_ready_stage(repository, postgres_engine)
    execution = PostgresStageExecutionRepository(postgres_engine)
    lease = execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-1",
        expected_input_digest=input_digest,
    )
    assert lease is not None

    assert (
        execution.record_failure(
            stage_run_id=stage_id,
            lease_token=lease.lease_token,
            input_digest=input_digest,
            failure=StageFailure(code="CONTRACT_REJECTED", retryable=False),
        )
        == FailureOutcome.TERMINAL_FAILED
    )
    with postgres_engine.connect() as connection:
        stage_status, stored_failure = connection.execute(
            text("SELECT status, failure_json FROM stage_runs WHERE stage_run_id=:id"),
            {"id": stage_id},
        ).one()
    assert stage_status == "FAILED"
    assert stored_failure == {"code": "CONTRACT_REJECTED", "retryable": False}


def test_internal_stage_authorization_requires_exact_live_lease(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    _project, stage_id, input_digest, _workflows = create_ready_stage(repository, postgres_engine)
    clock = [datetime(2026, 8, 21, tzinfo=UTC)]
    execution = PostgresStageExecutionRepository(postgres_engine, now=lambda: clock[0])
    lease = execution.claim(
        stage_run_id=stage_id,
        worker_id="worker-1",
        expected_input_digest=input_digest,
    )
    assert lease is not None

    assert execution.authorize(lease)
    assert not execution.authorize(lease.model_copy(update={"lease_token": "forged-token"}))
    clock[0] += timedelta(seconds=LEASE_SECONDS + 1)
    assert not execution.authorize(lease)


def test_cancelled_and_timed_out_results_are_never_checkpointed(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project, stage_id, input_digest, workflows = create_ready_stage(repository, postgres_engine)
    clock = [datetime(2026, 8, 21, tzinfo=UTC)]
    execution = PostgresStageExecutionRepository(postgres_engine, now=lambda: clock[0])
    lease = execution.claim(
        stage_run_id=stage_id, worker_id="worker-1", expected_input_digest=input_digest
    )
    assert lease is not None
    workflows.cancel(
        project_id=project.project_id,
        workflow_run_id=lease.workflow_run_id,
        user_id="user-1",
        idempotency_key="cancel-1",
    )
    assert (
        execution.checkpoint(
            stage_run_id=stage_id,
            lease_token=lease.lease_token,
            input_digest=lease.input_digest,
            result={},
        )
        == CheckpointOutcome.CANCELLED_DISCARDED
    )


def test_expired_result_is_late_and_heartbeat_extends_current_lease(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    _project, stage_id, input_digest, _workflows = create_ready_stage(repository, postgres_engine)
    clock = [datetime(2026, 8, 21, tzinfo=UTC)]
    execution = PostgresStageExecutionRepository(postgres_engine, now=lambda: clock[0])
    lease = execution.claim(
        stage_run_id=stage_id, worker_id="worker-1", expected_input_digest=input_digest
    )
    assert lease is not None
    clock[0] += timedelta(seconds=15)
    assert execution.heartbeat(stage_run_id=stage_id, lease_token=lease.lease_token)
    clock[0] += timedelta(seconds=LEASE_SECONDS + 1)
    assert not execution.heartbeat(stage_run_id=stage_id, lease_token=lease.lease_token)
    assert (
        execution.checkpoint(
            stage_run_id=stage_id,
            lease_token=lease.lease_token,
            input_digest=lease.input_digest,
            result={"must": "not persist"},
        )
        == CheckpointOutcome.LATE_DISCARDED
    )
    with postgres_engine.connect() as connection:
        status, result = connection.execute(
            text("SELECT status, result_json FROM stage_runs WHERE stage_run_id=:id"),
            {"id": stage_id},
        ).one()
    assert (status, result) == ("RUNNING", None)


@pytest.mark.parametrize(
    ("column", "replacement"),
    [
        ("workflow_generation", 99),
        ("state_version", 99),
        ("founder_snapshot_id", "changed-founder"),
        ("area_snapshot_id", "changed-area"),
        ("evidence_snapshot_id", "changed-evidence"),
        ("policy_snapshot_id", "changed-policy"),
        ("index_generation_id", "changed-index"),
        ("seed_registry_id", "changed-seed"),
    ],
)
def test_each_full_head_dimension_blocks_checkpoint(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
    column: str,
    replacement: object,
) -> None:
    _project, stage_id, input_digest, _workflows = create_ready_stage(repository, postgres_engine)
    execution = PostgresStageExecutionRepository(postgres_engine)
    lease = execution.claim(
        stage_run_id=stage_id, worker_id="worker-1", expected_input_digest=input_digest
    )
    assert lease is not None
    allowed_columns = {
        "workflow_generation",
        "state_version",
        "founder_snapshot_id",
        "area_snapshot_id",
        "evidence_snapshot_id",
        "policy_snapshot_id",
        "index_generation_id",
        "seed_registry_id",
    }
    assert column in allowed_columns
    with postgres_engine.begin() as connection:
        connection.execute(
            text(f"UPDATE project_heads SET {column}=:value"),
            {"value": replacement},
        )

    assert (
        execution.checkpoint(
            stage_run_id=stage_id,
            lease_token=lease.lease_token,
            input_digest=lease.input_digest,
            result={"must": "not persist"},
        )
        == CheckpointOutcome.STALE_DISCARDED
    )
    with postgres_engine.connect() as connection:
        row = connection.execute(
            text("SELECT status, result_json FROM stage_runs WHERE stage_run_id=:id"),
            {"id": stage_id},
        ).one()
    assert row == ("RUNNING", None)


def test_outbox_claim_recovery_is_at_least_once_and_token_fenced(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    create_ready_stage(repository, postgres_engine)
    clock = [datetime(2026, 8, 21, tzinfo=UTC)]
    tokens = iter(["claim-old", "claim-new"])
    outbox = PostgresOutboxRepository(
        postgres_engine,
        now=lambda: clock[0],
        new_token=lambda: next(tokens),
    )
    old = outbox.claim_next(publisher_id="publisher-1")
    assert old is not None
    assert outbox.claim_next(publisher_id="publisher-2") is None
    clock[0] += timedelta(seconds=46)
    new = outbox.claim_next(publisher_id="publisher-2")
    assert new is not None
    assert new.outbox_id == old.outbox_id
    assert not outbox.mark_published(
        outbox_id=old.outbox_id,
        claim_token=old.claim_token,
        pubsub_message_id="old-message",
    )
    assert outbox.mark_published(
        outbox_id=new.outbox_id,
        claim_token=new.claim_token,
        pubsub_message_id="new-message",
    )
    with postgres_engine.connect() as connection:
        status, attempts = connection.execute(
            text("SELECT status, attempts FROM workflow_outbox")
        ).one()
    assert (status, attempts) == ("PUBLISHED", 2)


def test_outbox_topic_filter_does_not_publish_cleanup_to_stage_subscription(
    repository: PostgresProjectRepository,
    postgres_engine: Engine,
) -> None:
    project, _stage_id, _input_digest, workflows = create_ready_stage(repository, postgres_engine)
    with postgres_engine.connect() as connection:
        workflow_run_id = connection.execute(
            text("SELECT workflow_run_id FROM workflow_runs")
        ).scalar_one()
    workflows.cancel(
        project_id=project.project_id,
        workflow_run_id=workflow_run_id,
        user_id="user-1",
        idempotency_key="cancel-1",
    )
    outbox = PostgresOutboxRepository(postgres_engine, new_token=lambda: "cleanup-claim")

    cleanup = outbox.claim_next(
        publisher_id="cleanup-publisher",
        logical_topic="WORKFLOW_CLEANUP",
    )

    assert cleanup is not None
    assert cleanup.topic == "WORKFLOW_CLEANUP"
    with postgres_engine.connect() as connection:
        stage_status = connection.execute(
            text("SELECT status FROM workflow_outbox WHERE topic='WORKFLOW_STAGE_READY'")
        ).scalar_one()
    assert stage_status == "PENDING"
