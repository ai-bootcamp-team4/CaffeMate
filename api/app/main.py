from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Protocol, cast

from fastapi import Depends, FastAPI, Header, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from google.api_core.exceptions import GoogleAPICallError
from pydantic import BaseModel, ConfigDict
from worker.outbox import OutboxPublisher, PostgresOutboxRepository
from worker.pubsub import GooglePubSubPublisher

from app.agents.runtime import (
    AgentRuntimeHttpClient,
    GoogleAccessTokenProvider,
    PostgresAgentCleanupSink,
)
from app.areas.models import AreaSearchRequest, AreaSearchResult
from app.areas.service import AreaLookupService, UnavailableAreaLookupService
from app.areas.token import AreaSelectionTokenSigner
from app.auth import (
    FirebaseIdentityVerifier,
    GoogleServiceIdentityVerifier,
    IdentityVerifier,
    UnconfiguredIdentityVerifier,
)
from app.candidates.seed_registry import IndependentSeedRegistry
from app.database import DatabaseHandle, create_database_handle
from app.documents.extraction import (
    DocumentExtractionService,
    UnavailableDocumentExtractionService,
)
from app.documents.models import (
    ApplyExtractionFormRequest,
    BeginDocumentUploadRequest,
    CompleteDocumentUploadRequest,
    DocumentDownload,
    DocumentExtractionForm,
    DocumentRevision,
    DocumentScanResultRequest,
    ExtractionFormApplication,
    ParserResultRequest,
    SignedUpload,
    UpdateExtractionFormRequest,
)
from app.documents.service import DocumentService, UnavailableDocumentService
from app.documents.storage import GoogleCloudDocumentStorage
from app.domain.errors import (
    AuthenticationUnavailableError,
    CandidateSelectionPreconditionError,
    ContractValidationError,
    DocumentNotFoundError,
    DocumentPreconditionError,
    DomainError,
    ExternalExecutionUnavailableError,
    FeedbackPreconditionError,
    FeedbackPreviewNotFoundError,
    FirstProposalConfigurationUnavailableError,
    FirstProposalPreflightUnavailableError,
    IdempotencyKeyReusedError,
    PersistenceUnavailableError,
    ProjectNotFoundError,
    ResultNotFoundError,
    StageLeaseRejectedError,
    StateVersionConflictError,
    UnauthenticatedError,
    WorkflowNotFoundError,
    WorkflowPreconditionError,
)
from app.domain.models import (
    AreaResolutionStatus,
    AreaState,
    CandidateSetCompleteness,
    CoverageProfile,
    FounderState,
    Project,
)
from app.evidence.models import EvidenceRefreshRequest, EvidenceRefreshResult
from app.evidence.refresh import (
    EvidenceRefreshService,
    UnavailableEvidenceRefreshService,
)
from app.feedback.models import (
    ConfirmFeedbackRequest,
    CreateFeedbackPreviewRequest,
    FeedbackPreview,
    FeedbackResolution,
)
from app.feedback.postgres_repository import PostgresFeedbackRepository
from app.feedback.service import (
    FeedbackService,
    UnavailableFeedbackService,
)
from app.mcp.client import GoogleIdentityTokenProvider, McpHttpClient
from app.mcp.preflight import McpManifestPreflight
from app.mcp.scope import ScopeTokenSigner
from app.projects.postgres_repository import PostgresProjectRepository
from app.projects.service import ProjectService
from app.projects.unavailable_repository import UnavailableProjectRepository
from app.results.models import ResultView
from app.results.postgres_repository import PostgresResultRepository
from app.results.service import ResultService
from app.results.unavailable_repository import UnavailableResultRepository
from app.selections.models import CandidateSelection, SelectCandidateRequest
from app.selections.preparation import (
    PreparationGuide,
    PreparationGuideService,
    UnavailablePreparationGuideService,
)
from app.selections.service import (
    CandidateSelectionService,
    UnavailableCandidateSelectionService,
)
from app.settings import RuntimeSettings
from app.workflows.area_resolution import AreaMcpClient, AreaResolutionStageHandler
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
from app.workflows.evidence_plan import AgentRuntime, EvidencePlanStageHandler
from app.workflows.evidence_retrieval import (
    EvidenceMcpClient,
    EvidenceRetrievalStageHandler,
)
from app.workflows.execution_repository import PostgresStageExecutionRepository
from app.workflows.first_proposal import FirstProposalStage
from app.workflows.models import (
    StageLease,
    WorkflowCode,
    WorkflowEvent,
    WorkflowProgress,
    WorkflowRun,
)
from app.workflows.postgres_repository import PostgresWorkflowRepository
from app.workflows.proposal import ProposalStageHandler
from app.workflows.service import WorkflowService
from app.workflows.stage_context import PostgresStageContextRepository
from app.workflows.stage_router import (
    FirstProposalStageHandler,
    FirstProposalStageRouter,
)
from app.workflows.stage_service import (
    StageExecution,
    StageExecutionService,
    UnavailableStageExecutionService,
)
from app.workflows.start_guard import FirstProposalStartGuard, McpManifestStartGate
from app.workflows.unavailable_repository import UnavailableWorkflowRepository


class OutboxDispatcher(Protocol):
    def publish_one(self) -> bool: ...


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfirmOnboardingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    founder: FounderState
    area_selection_token: str | None = None


class StageExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease: StageLease


class StageExecuteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result: dict[str, object]


def create_app(
    *,
    project_service: ProjectService | None = None,
    workflow_service: WorkflowService | None = None,
    result_service: ResultService | None = None,
    feedback_service: FeedbackService | UnavailableFeedbackService | None = None,
    candidate_selection_service: (
        CandidateSelectionService | UnavailableCandidateSelectionService | None
    ) = None,
    preparation_guide_service: (
        PreparationGuideService | UnavailablePreparationGuideService | None
    ) = None,
    document_service: DocumentService | UnavailableDocumentService | None = None,
    document_extraction_service: (
        DocumentExtractionService | UnavailableDocumentExtractionService | None
    ) = None,
    evidence_refresh_service: (
        EvidenceRefreshService | UnavailableEvidenceRefreshService | None
    ) = None,
    area_lookup_service: AreaLookupService | UnavailableAreaLookupService | None = None,
    identity_verifier: IdentityVerifier | None = None,
    stage_execution_service: StageExecution | None = None,
    internal_identity_verifier: IdentityVerifier | None = None,
    agent_runtime: AgentRuntime | None = None,
    mcp_client: AreaMcpClient | EvidenceMcpClient | None = None,
    mcp_manifest_preflight: McpManifestPreflight | None = None,
    outbox_dispatcher: OutboxDispatcher | None = None,
) -> FastAPI:
    database_handle: DatabaseHandle | None = None
    settings = RuntimeSettings.from_environment()
    seed_registry = IndependentSeedRegistry.load_default()
    if project_service is None or workflow_service is None or result_service is None:
        database_handle = create_database_handle(settings)
    if project_service is None:
        repository = (
            PostgresProjectRepository(database_handle.engine)
            if database_handle is not None
            else UnavailableProjectRepository()
        )
        service = ProjectService(repository)
    else:
        service = project_service

    if result_service is None:
        result_repository = (
            PostgresResultRepository(database_handle.engine)
            if database_handle is not None
            else UnavailableResultRepository()
        )
        results = ResultService(result_repository)
    else:
        results = result_service

    configured_agent_runtime = agent_runtime
    if (
        configured_agent_runtime is None
        and database_handle is not None
        and settings.has_agent_runtime_configuration
    ):
        configured_agent_runtime = AgentRuntimeHttpClient(
            gcp_project_id=cast(str, settings.agent_runtime_project_id),
            resource_id=cast(str, settings.agent_runtime_resource_id),
            user_hmac_secret=cast(str, settings.agent_runtime_user_hmac_secret),
            access_tokens=GoogleAccessTokenProvider(),
            cleanup_sink=PostgresAgentCleanupSink(database_handle.engine),
        )

    configured_mcp_client = mcp_client
    if configured_mcp_client is None and settings.has_mcp_configuration:
        configured_mcp_client = McpHttpClient(
            base_url=cast(str, settings.mcp_base_url),
            audience=cast(str, settings.mcp_audience),
            identity_provider=GoogleIdentityTokenProvider(),
            scope_signer=ScopeTokenSigner(
                secret=cast(str, settings.mcp_scope_hmac_secret),
                issuer="caffemate-control-api",
                audience="caffemate-mcp",
            ),
        )

    configured_mcp_preflight = mcp_manifest_preflight
    if configured_mcp_preflight is None and settings.has_mcp_configuration:
        configured_mcp_preflight = McpManifestPreflight(
            base_url=cast(str, settings.mcp_base_url),
            audience=cast(str, settings.mcp_audience),
            identity_provider=GoogleIdentityTokenProvider(),
            scope_signer=ScopeTokenSigner(
                secret=cast(str, settings.mcp_scope_hmac_secret),
                issuer="caffemate-control-api",
                audience="caffemate-mcp",
            ),
        )

    stage_handlers: dict[FirstProposalStage, FirstProposalStageHandler] = {
        FirstProposalStage.CLAIM_PLAN: ClaimPlanStageHandler(),
        FirstProposalStage.EVIDENCE_FREEZE: EvidenceFreezeStageHandler(),
        FirstProposalStage.INDEPENDENT_SEED: IndependentSeedStageHandler(seed_registry),
        FirstProposalStage.FRANCHISE_ELIGIBILITY: FranchiseEligibilityStageHandler(),
        FirstProposalStage.CALCULATE_GATE_RANK: CalculateGateRankStageHandler(),
        FirstProposalStage.COMMIT_RESULT: CommitResultStageHandler(),
    }
    if configured_mcp_client is not None:
        stage_handlers.update(
            {
                FirstProposalStage.AREA_RESOLUTION: AreaResolutionStageHandler(
                    configured_mcp_client
                ),
                FirstProposalStage.EVIDENCE_RETRIEVAL: EvidenceRetrievalStageHandler(
                    configured_mcp_client
                ),
            }
        )
    if configured_agent_runtime is not None:
        stage_handlers[FirstProposalStage.EVIDENCE_PLAN] = EvidencePlanStageHandler(
            configured_agent_runtime
        )
        stage_handlers[FirstProposalStage.EVIDENCE_ASSESS] = EvidenceAssessStageHandler(
            configured_agent_runtime
        )
        stage_handlers[FirstProposalStage.PROPOSE_INDEPENDENT] = (
            ProposalStageHandler.independent(configured_agent_runtime)
        )
        stage_handlers[FirstProposalStage.PROPOSE_FRANCHISE] = (
            ProposalStageHandler.franchise(configured_agent_runtime)
        )
        stage_handlers[FirstProposalStage.CANDIDATE_AUDIT] = CandidateAuditStageHandler(
            configured_agent_runtime
        )

    if workflow_service is None:
        workflow_repository = (
            PostgresWorkflowRepository(
                database_handle.engine,
                policy_snapshot_id=settings.policy_snapshot_id,
                seed_registry_id=seed_registry.registry_id,
            )
            if database_handle is not None and settings.policy_snapshot_id is not None
            else UnavailableWorkflowRepository()
        )
        start_guard = (
            FirstProposalStartGuard(
                stage_handlers,
                manifest_gate=(
                    McpManifestStartGate(
                        configured_mcp_preflight,
                        policy_snapshot_id=settings.policy_snapshot_id,
                        seed_registry_id=seed_registry.registry_id,
                    )
                    if configured_mcp_preflight is not None
                    else None
                ),
            )
            if database_handle is not None and settings.policy_snapshot_id is not None
            else None
        )
        workflows = WorkflowService(workflow_repository, start_guard=start_guard)
    else:
        workflows = workflow_service

    immediate_outbox = outbox_dispatcher
    if (
        immediate_outbox is None
        and database_handle is not None
        and settings.workflow_stage_topic_resource
    ):
        immediate_outbox = OutboxPublisher(
            PostgresOutboxRepository(database_handle.engine),
            GooglePubSubPublisher(
                topic_resources={
                    "WORKFLOW_STAGE_READY": settings.workflow_stage_topic_resource,
                }
            ),
            publisher_id="caffemate-api",
            logical_topic="WORKFLOW_STAGE_READY",
        )

    if feedback_service is not None:
        feedback = feedback_service
    elif database_handle is not None and configured_agent_runtime is not None:
        feedback = FeedbackService(
            PostgresFeedbackRepository(database_handle.engine),
            service,
            results,
            configured_agent_runtime,
        )
    else:
        feedback = UnavailableFeedbackService()

    if candidate_selection_service is not None:
        candidate_selections = candidate_selection_service
    elif database_handle is not None:
        candidate_selections = CandidateSelectionService(database_handle.engine)
    else:
        candidate_selections = UnavailableCandidateSelectionService()

    if preparation_guide_service is not None:
        preparation_guides = preparation_guide_service
    elif database_handle is not None and configured_mcp_client is not None:
        preparation_guides = PreparationGuideService(
            database_handle.engine,
            configured_mcp_client,
        )
    else:
        preparation_guides = UnavailablePreparationGuideService()

    if document_service is not None:
        documents = document_service
    elif database_handle is not None and settings.document_bucket:
        documents = DocumentService(
            database_handle.engine,
            GoogleCloudDocumentStorage(settings.document_bucket),
        )
    else:
        documents = UnavailableDocumentService()

    if document_extraction_service is not None:
        document_extraction = document_extraction_service
    elif database_handle is not None and configured_agent_runtime is not None:
        document_extraction = DocumentExtractionService(
            database_handle.engine, configured_agent_runtime
        )
    else:
        document_extraction = UnavailableDocumentExtractionService()

    if evidence_refresh_service is not None:
        evidence_refresh = evidence_refresh_service
    elif database_handle is not None:
        evidence_refresh = EvidenceRefreshService(database_handle.engine)
    else:
        evidence_refresh = UnavailableEvidenceRefreshService()

    if area_lookup_service is not None:
        area_lookup = area_lookup_service
    elif (
        configured_mcp_client is not None
        and settings.mcp_scope_hmac_secret is not None
        and settings.policy_snapshot_id is not None
    ):
        area_lookup = AreaLookupService(
            configured_mcp_client,
            token_signer=AreaSelectionTokenSigner(secret=settings.mcp_scope_hmac_secret),
            policy_snapshot_id=settings.policy_snapshot_id,
        )
    else:
        area_lookup = UnavailableAreaLookupService()

    if stage_execution_service is not None:
        internal_stages = stage_execution_service
    elif database_handle is not None:
        internal_stages = StageExecutionService(
            PostgresStageExecutionRepository(database_handle.engine),
            FirstProposalStageRouter(
                PostgresStageContextRepository(database_handle.engine),
                stage_handlers,
            ),
        )
    else:
        internal_stages = UnavailableStageExecutionService()

    if identity_verifier is not None:
        verifier = identity_verifier
    elif settings.firebase_project_id:
        verifier = FirebaseIdentityVerifier(project_id=settings.firebase_project_id)
    else:
        verifier = UnconfiguredIdentityVerifier()

    if internal_identity_verifier is not None:
        worker_verifier = internal_identity_verifier
    elif settings.control_api_audience and settings.worker_service_account_email:
        worker_verifier = GoogleServiceIdentityVerifier(
            audience=settings.control_api_audience,
            allowed_service_account_email=settings.worker_service_account_email,
        )
    else:
        worker_verifier = UnconfiguredIdentityVerifier()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if database_handle is not None:
                database_handle.close()

    app = FastAPI(title="CaffeMate Control API", version="0.2.0", lifespan=lifespan)
    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_allowed_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
            max_age=3600,
        )

    def current_user(authorization: Annotated[str | None, Header()] = None) -> str:
        if authorization is None:
            raise UnauthenticatedError("Bearer ID token is required")
        scheme, separator, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not token.strip():
            raise UnauthenticatedError("Bearer ID token is required")
        return verifier.verify(token.strip())

    @app.exception_handler(DomainError)
    async def domain_error_handler(_request: object, error: DomainError) -> JSONResponse:
        status_by_error_type = {
            ProjectNotFoundError: status.HTTP_404_NOT_FOUND,
            ResultNotFoundError: status.HTTP_404_NOT_FOUND,
            StateVersionConflictError: status.HTTP_409_CONFLICT,
            IdempotencyKeyReusedError: status.HTTP_409_CONFLICT,
            ContractValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
            AuthenticationUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
            PersistenceUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
            UnauthenticatedError: status.HTTP_401_UNAUTHORIZED,
            WorkflowNotFoundError: status.HTTP_404_NOT_FOUND,
            WorkflowPreconditionError: status.HTTP_409_CONFLICT,
            FeedbackPreconditionError: status.HTTP_409_CONFLICT,
            FeedbackPreviewNotFoundError: status.HTTP_404_NOT_FOUND,
            CandidateSelectionPreconditionError: status.HTTP_409_CONFLICT,
            DocumentNotFoundError: status.HTTP_404_NOT_FOUND,
            DocumentPreconditionError: status.HTTP_409_CONFLICT,
            StageLeaseRejectedError: status.HTTP_409_CONFLICT,
            ExternalExecutionUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
            FirstProposalConfigurationUnavailableError: (
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            FirstProposalPreflightUnavailableError: (
                status.HTTP_503_SERVICE_UNAVAILABLE
            ),
        }
        status_code = next(
            (
                mapped_status
                for error_type, mapped_status in status_by_error_type.items()
                if isinstance(error, error_type)
            ),
            status.HTTP_400_BAD_REQUEST,
        )
        content: dict[str, object] = {"code": error.code}
        if isinstance(error, FirstProposalConfigurationUnavailableError):
            content["missing_stage_codes"] = error.missing_stage_codes
        if isinstance(error, FirstProposalPreflightUnavailableError):
            content["reason_codes"] = error.reason_codes
        return JSONResponse(status_code=status_code, content=content)

    @app.get("/health", tags=["operations"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/internal/v1/workflows/{workflow_run_id}/stages/{stage_run_id}:execute",
        response_model=StageExecuteResponse,
        tags=["internal"],
    )
    def execute_stage(
        workflow_run_id: str,
        stage_run_id: str,
        request: StageExecuteRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> StageExecuteResponse:
        if authorization is None:
            raise UnauthenticatedError("Worker service identity token is required")
        scheme, separator, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not token.strip():
            raise UnauthenticatedError("Worker service identity token is required")
        worker_verifier.verify(token.strip())
        result = internal_stages.execute(
            workflow_run_id=workflow_run_id,
            stage_run_id=stage_run_id,
            lease=request.lease,
        )
        return StageExecuteResponse(result=result)

    @app.post(
        "/internal/v1/documents/{document_revision_id}:scan-result",
        response_model=DocumentRevision,
        tags=["internal"],
    )
    def record_document_scan_result(
        document_revision_id: str,
        request: DocumentScanResultRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> DocumentRevision:
        if authorization is None:
            raise UnauthenticatedError("Worker service identity token is required")
        scheme, separator, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not token.strip():
            raise UnauthenticatedError("Worker service identity token is required")
        worker_verifier.verify(token.strip())
        return documents.record_scan_result(
            project_id=request.project_id,
            document_revision_id=document_revision_id,
            clean=request.clean,
            threat_codes=request.threat_codes,
        )

    @app.post(
        "/internal/v1/documents/{document_revision_id}:parser-result",
        response_model=DocumentExtractionForm,
        tags=["internal"],
    )
    def record_document_parser_result(
        document_revision_id: str,
        request: ParserResultRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> DocumentExtractionForm:
        if authorization is None:
            raise UnauthenticatedError("Worker service identity token is required")
        scheme, separator, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not token.strip():
            raise UnauthenticatedError("Worker service identity token is required")
        worker_verifier.verify(token.strip())
        return document_extraction.accept_parser_result(
            document_revision_id=document_revision_id,
            request=request,
        )

    @app.post(
        "/internal/v1/evidence:refresh",
        response_model=EvidenceRefreshResult,
        tags=["internal"],
    )
    def refresh_evidence(
        request: EvidenceRefreshRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> EvidenceRefreshResult:
        if authorization is None:
            raise UnauthenticatedError("Worker service identity token is required")
        scheme, separator, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not token.strip():
            raise UnauthenticatedError("Worker service identity token is required")
        worker_verifier.verify(token.strip())
        return evidence_refresh.refresh(request)

    @app.post("/v1/projects", response_model=Project, status_code=status.HTTP_201_CREATED)
    def create_project(
        _request: EmptyRequest,
        user_id: Annotated[str, Depends(current_user)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> Project:
        return service.create_project(user_id=user_id, idempotency_key=idempotency_key)

    @app.get("/v1/projects/{project_id}", response_model=Project)
    def get_project(
        project_id: str,
        user_id: Annotated[str, Depends(current_user)],
    ) -> Project:
        return service.get_project(project_id=project_id, user_id=user_id)

    @app.get("/v1/projects/{project_id}/result", response_model=ResultView)
    def get_current_result(
        project_id: str,
        user_id: Annotated[str, Depends(current_user)],
    ) -> ResultView:
        return results.get_current(project_id=project_id, user_id=user_id)

    @app.post(
        "/v1/projects/{project_id}/feedback/previews",
        response_model=FeedbackPreview,
        status_code=status.HTTP_201_CREATED,
    )
    def create_feedback_preview(
        project_id: str,
        request: CreateFeedbackPreviewRequest,
        user_id: Annotated[str, Depends(current_user)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> FeedbackPreview:
        return feedback.create_preview(
            project_id=project_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            user_input=request.input,
        )

    @app.get(
        "/v1/projects/{project_id}/feedback/previews/{preview_id}",
        response_model=FeedbackPreview,
    )
    def get_feedback_preview(
        project_id: str,
        preview_id: str,
        user_id: Annotated[str, Depends(current_user)],
    ) -> FeedbackPreview:
        return feedback.get_preview(
            project_id=project_id,
            preview_id=preview_id,
            user_id=user_id,
        )

    @app.post(
        "/v1/projects/{project_id}/feedback/{preview_id}/confirm",
        response_model=FeedbackResolution,
    )
    def confirm_feedback_preview(
        project_id: str,
        preview_id: str,
        request: ConfirmFeedbackRequest,
        user_id: Annotated[str, Depends(current_user)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> FeedbackResolution:
        return feedback.confirm_preview(
            project_id=project_id,
            preview_id=preview_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            expected_head=request.expected_head,
            proposal_digest=request.proposal_digest,
        )

    @app.post(
        "/v1/projects/{project_id}/feedback/{preview_id}/cancel",
        response_model=FeedbackResolution,
    )
    def cancel_feedback_preview(
        project_id: str,
        preview_id: str,
        user_id: Annotated[str, Depends(current_user)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> FeedbackResolution:
        return feedback.cancel_preview(
            project_id=project_id,
            preview_id=preview_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
        )

    @app.post(
        "/v1/projects/{project_id}/candidate-selections",
        response_model=CandidateSelection,
        status_code=status.HTTP_201_CREATED,
    )
    def select_candidate(
        project_id: str,
        request: SelectCandidateRequest,
        user_id: Annotated[str, Depends(current_user)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> CandidateSelection:
        return candidate_selections.select(
            project_id=project_id,
            user_id=user_id,
            result_bundle_id=request.result_bundle_id,
            candidate_id=request.candidate_id,
            expected_head=request.expected_head,
            idempotency_key=idempotency_key,
        )

    @app.get(
        "/v1/projects/{project_id}/candidate-selections/{selection_id}/preparation-guide",
        response_model=PreparationGuide,
    )
    async def get_preparation_guide(
        project_id: str,
        selection_id: str,
        user_id: Annotated[str, Depends(current_user)],
    ) -> PreparationGuide:
        return await preparation_guides.get(
            project_id=project_id,
            selection_id=selection_id,
            user_id=user_id,
        )

    @app.post(
        "/v1/projects/{project_id}/documents/uploads",
        response_model=SignedUpload,
        status_code=status.HTTP_201_CREATED,
    )
    def begin_document_upload(
        project_id: str,
        request: BeginDocumentUploadRequest,
        user_id: Annotated[str, Depends(current_user)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> SignedUpload:
        return documents.begin_upload(
            project_id=project_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            request=request,
        )

    @app.post(
        "/v1/projects/{project_id}/documents/uploads:complete",
        response_model=DocumentRevision,
    )
    def complete_document_upload(
        project_id: str,
        request: CompleteDocumentUploadRequest,
        user_id: Annotated[str, Depends(current_user)],
    ) -> DocumentRevision:
        return documents.complete_upload(
            project_id=project_id,
            user_id=user_id,
            document_revision_id=request.document_revision_id,
        )

    @app.get(
        "/v1/projects/{project_id}/documents/{document_revision_id}/download",
        response_model=DocumentDownload,
    )
    def get_document_download(
        project_id: str,
        document_revision_id: str,
        user_id: Annotated[str, Depends(current_user)],
    ) -> DocumentDownload:
        return documents.get_download(
            project_id=project_id,
            user_id=user_id,
            document_revision_id=document_revision_id,
        )

    @app.get(
        "/v1/projects/{project_id}/documents/{document_revision_id}/extraction-form",
        response_model=DocumentExtractionForm,
    )
    def get_document_extraction_form(
        project_id: str,
        document_revision_id: str,
        user_id: Annotated[str, Depends(current_user)],
    ) -> DocumentExtractionForm:
        return document_extraction.get_form(
            project_id=project_id,
            user_id=user_id,
            document_revision_id=document_revision_id,
        )

    @app.put(
        "/v1/projects/{project_id}/documents/{document_revision_id}/extraction-form",
        response_model=DocumentExtractionForm,
    )
    def update_document_extraction_form(
        project_id: str,
        document_revision_id: str,
        request: UpdateExtractionFormRequest,
        user_id: Annotated[str, Depends(current_user)],
    ) -> DocumentExtractionForm:
        return document_extraction.update_form(
            project_id=project_id,
            user_id=user_id,
            document_revision_id=document_revision_id,
            request=request,
        )

    @app.post(
        "/v1/projects/{project_id}/documents/{document_revision_id}/extraction-form:apply",
        response_model=ExtractionFormApplication,
        status_code=status.HTTP_201_CREATED,
    )
    def apply_document_extraction_form(
        project_id: str,
        document_revision_id: str,
        request: ApplyExtractionFormRequest,
        user_id: Annotated[str, Depends(current_user)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> ExtractionFormApplication:
        return document_extraction.apply_form(
            project_id=project_id,
            user_id=user_id,
            document_revision_id=document_revision_id,
            idempotency_key=idempotency_key,
            request=request,
        )

    @app.get("/v1/projects", response_model=list[Project])
    def list_projects(user_id: Annotated[str, Depends(current_user)]) -> list[Project]:
        return service.list_projects(user_id=user_id)

    @app.post(
        "/v1/projects/{project_id}/areas:search",
        response_model=AreaSearchResult,
    )
    async def search_areas(
        project_id: str,
        request: AreaSearchRequest,
        user_id: Annotated[str, Depends(current_user)],
    ) -> AreaSearchResult:
        service.get_project(project_id=project_id, user_id=user_id)
        return await area_lookup.search(
            project_id=project_id,
            query=request.query,
            limit=request.limit,
        )

    @app.post("/v1/projects/{project_id}/onboarding/confirm", response_model=Project)
    def confirm_onboarding(
        project_id: str,
        request: ConfirmOnboardingRequest,
        user_id: Annotated[str, Depends(current_user)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> Project:
        identity = area_lookup.resolve_selection(
            project_id=project_id,
            query=request.founder.target_area_input,
            selection_token=request.area_selection_token,
        )
        area = None
        if identity is not None:
            analysis_code = (
                identity.administrative_dong_codes[0]
                if len(identity.administrative_dong_codes) == 1
                else identity.legal_dong_code
            )
            area = AreaState(
                resolution_status=AreaResolutionStatus.RESOLVED,
                area_id=identity.area_id,
                scope_type=identity.scope_type,
                administrative_code=analysis_code,
                legal_dong_code=identity.legal_dong_code,
                administrative_dong_codes=identity.administrative_dong_codes,
                mapping_status=identity.mapping_status,
                candidate_set_completeness=CandidateSetCompleteness.UNVERIFIED,
                source_revision=identity.source_revision,
                display_name=identity.display_name,
                boundary_version=identity.boundary_version,
                coverage_profile=CoverageProfile.N0_NATIONWIDE_FACTS,
                unavailable_fields=(
                    ["administrative_dong_mapping"]
                    if not identity.administrative_dong_codes
                    else []
                ),
            )
        return service.confirm_onboarding(
            project_id=project_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            founder=request.founder,
            area=area,
        )

    @app.post(
        "/v1/projects/{project_id}/workflows/{workflow_run_id}:cancel",
        response_model=WorkflowRun,
    )
    def cancel_workflow(
        project_id: str,
        workflow_run_id: str,
        _request: EmptyRequest,
        user_id: Annotated[str, Depends(current_user)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> WorkflowRun:
        return workflows.cancel(
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
        )

    @app.post(
        "/v1/projects/{project_id}/workflows/{workflow_code}",
        response_model=WorkflowRun,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def start_workflow(
        project_id: str,
        workflow_code: WorkflowCode,
        _request: EmptyRequest,
        user_id: Annotated[str, Depends(current_user)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> WorkflowRun:
        run = workflows.start(
            project_id=project_id,
            user_id=user_id,
            workflow_code=workflow_code,
            idempotency_key=idempotency_key,
        )
        if immediate_outbox is not None:
            try:
                immediate_outbox.publish_one()
            except (GoogleAPICallError, RuntimeError, TimeoutError, ValueError):
                # Workflow creation already committed its durable outbox row.
                # The scheduled drain remains the recovery path.
                pass
        return run

    @app.get(
        "/v1/projects/{project_id}/workflows/{workflow_run_id}",
        response_model=WorkflowProgress,
    )
    def get_workflow(
        project_id: str,
        workflow_run_id: str,
        user_id: Annotated[str, Depends(current_user)],
    ) -> WorkflowProgress:
        return workflows.get_progress(
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            user_id=user_id,
        )

    @app.get(
        "/v1/projects/{project_id}/workflows/{workflow_run_id}/events",
        response_model=list[WorkflowEvent],
    )
    def list_workflow_events(
        project_id: str,
        workflow_run_id: str,
        user_id: Annotated[str, Depends(current_user)],
    ) -> list[WorkflowEvent]:
        return workflows.list_events(
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            user_id=user_id,
        )

    return app


app = create_app()
