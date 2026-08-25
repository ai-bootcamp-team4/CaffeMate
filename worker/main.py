"""백그라운드 서비스는 workflow delivery, lease와 운영 Outbox를 처리한다."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

from app.agents.runtime import GoogleAccessTokenProvider
from app.database import DatabaseHandle, create_database_handle
from app.observability import SafeTracingMiddleware, configure_cloud_trace, tracer
from app.settings import RuntimeSettings
from app.workflows.dispatch import PostgresPubSubWorkflowDispatcher, WorkflowDispatcher
from app.workflows.lease import PostgresWorkflowLeaseRepository
from fastapi import FastAPI, Query, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from worker.agent_cleanup import (
    AgentRuntimeSessionDeleter,
    AgentSessionCleanupConsumer,
    CleanupOutcome,
)
from worker.control_api import ControlApiWorkflowProcessor, GoogleIdentityTokenProvider
from worker.dead_letter import (
    DeadLetterOperationError,
    DeadLetterOperations,
    DeadLetterPage,
    DeadLetterReprocessResult,
    ReprocessDeadLetterRequest,
    UnavailableDeadLetterOperations,
)
from worker.outbox import PostgresOutboxRepository
from worker.pubsub import (
    InvalidPubSubEnvelopeError,
    PubSubDelivery,
    decode_push_envelope,
)
from worker.workflow_runtime import DurableWorkflowWorker, WorkerRetryRequiredError


class SessionCleanupHandler(Protocol):
    def cleanup_one(self) -> CleanupOutcome: ...


class DeadLetterHandler(Protocol):
    def list(self, *, limit: int, after_outbox_id: int | None = None) -> DeadLetterPage: ...

    def reprocess(
        self,
        *,
        outbox_id: int,
        request: ReprocessDeadLetterRequest,
    ) -> DeadLetterReprocessResult: ...


class WorkflowDeliveryHandler(Protocol):
    def handle(self, delivery: PubSubDelivery) -> object: ...


class AgentCleanupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=20, ge=1, le=100)


class AgentCleanupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deleted: int = Field(ge=0)
    retry_scheduled: int = Field(ge=0)
    dead_lettered: int = Field(ge=0)
    drained: bool


class OutboxPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=20, ge=1, le=100)


class OutboxPublishResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    published: int = Field(ge=0)
    drained: bool


class OutboxConfigurationUnavailableError(RuntimeError):
    pass


class UnavailableSessionCleanupHandler:
    def cleanup_one(self) -> CleanupOutcome:
        raise OutboxConfigurationUnavailableError("Agent cleanup is not configured")


class UnavailableWorkflowDeliveryHandler:
    def handle(self, delivery: PubSubDelivery) -> object:
        del delivery
        raise OutboxConfigurationUnavailableError("Workflow delivery is not configured")


def create_worker_app(
    *,
    cleanup_consumer: SessionCleanupHandler | None = None,
    dead_letter_operations: DeadLetterHandler | None = None,
    workflow_delivery_handler: WorkflowDeliveryHandler | None = None,
    workflow_dispatcher: WorkflowDispatcher | None = None,
) -> FastAPI:
    settings = RuntimeSettings.from_environment()
    configure_cloud_trace(
        service_name="caffemate-worker",
        service_version=os.getenv("CAFFEMATE_SOURCE_REVISION") or os.getenv("K_REVISION"),
        project_id=settings.agent_runtime_project_id,
    )
    database_handle: DatabaseHandle | None = create_database_handle(settings)

    if cleanup_consumer is not None:
        session_cleanup = cleanup_consumer
    elif (
        database_handle is not None
        and settings.agent_runtime_project_id
        and settings.agent_runtime_resource_id
        and settings.worker_id
    ):
        session_cleanup = AgentSessionCleanupConsumer(
            PostgresOutboxRepository(database_handle.engine),
            AgentRuntimeSessionDeleter(
                gcp_project_id=settings.agent_runtime_project_id,
                resource_id=settings.agent_runtime_resource_id,
                access_tokens=GoogleAccessTokenProvider(),
            ),
            consumer_id=settings.worker_id,
        )
    else:
        session_cleanup = UnavailableSessionCleanupHandler()

    if dead_letter_operations is not None:
        dead_letters = dead_letter_operations
    elif database_handle is not None:
        dead_letters = DeadLetterOperations(database_handle.engine)
    else:
        dead_letters = UnavailableDeadLetterOperations()

    if workflow_delivery_handler is not None:
        workflow_delivery = workflow_delivery_handler
    elif (
        database_handle is not None
        and settings.control_api_url
        and settings.control_api_audience
        and settings.worker_id
    ):
        workflow_delivery = DurableWorkflowWorker(
            PostgresWorkflowLeaseRepository(database_handle.engine),
            ControlApiWorkflowProcessor(
                base_url=settings.control_api_url,
                audience=settings.control_api_audience,
                token_provider=GoogleIdentityTokenProvider(),
            ),
            worker_id=settings.worker_id,
        )
    else:
        workflow_delivery = UnavailableWorkflowDeliveryHandler()

    if workflow_dispatcher is not None:
        pending_workflows = workflow_dispatcher
    elif (
        database_handle is not None
        and settings.workflow_stage_topic_resource
        and settings.worker_id
    ):
        pending_workflows = PostgresPubSubWorkflowDispatcher(
            database_handle.engine,
            topic_resource=settings.workflow_stage_topic_resource,
            publisher_id=settings.worker_id,
        )
    else:
        pending_workflows = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if database_handle is not None:
                database_handle.close()

    app = FastAPI(title="CaffeMate Worker", version="0.2.0", lifespan=lifespan)
    app.add_middleware(SafeTracingMiddleware)

    @app.get("/health", tags=["operations"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/internal/v1/pubsub/workflow-stages",
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
        tags=["internal"],
    )
    def consume_workflow_stage(body: dict[str, object]) -> Response | JSONResponse:
        if not settings.pubsub_subscription:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"code": "WORKFLOW_SUBSCRIPTION_UNAVAILABLE"},
            )
        try:
            delivery = decode_push_envelope(
                body,
                expected_subscription=settings.pubsub_subscription,
            )
            workflow_delivery.handle(delivery)
        except InvalidPubSubEnvelopeError:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"code": "INVALID_WORKFLOW_DELIVERY"},
            )
        except WorkerRetryRequiredError:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"code": "WORKFLOW_RETRY_REQUIRED"},
            )
        except OutboxConfigurationUnavailableError:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"code": "WORKFLOW_DELIVERY_UNAVAILABLE"},
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/internal/v1/outbox:publish",
        response_model=OutboxPublishResponse,
        tags=["internal"],
    )
    def publish_workflow_outbox(
        request: OutboxPublishRequest,
    ) -> OutboxPublishResponse | JSONResponse:
        if pending_workflows is None:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"code": "WORKFLOW_DISPATCH_UNAVAILABLE"},
            )
        published = 0
        try:
            for _ in range(request.limit):
                if not pending_workflows.dispatch():
                    return OutboxPublishResponse(published=published, drained=True)
                published += 1
        except Exception:  # noqa: BLE001 - Scheduler must retry transport failures
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"code": "WORKFLOW_DISPATCH_FAILED"},
            )
        return OutboxPublishResponse(published=published, drained=False)

    @app.post(
        "/internal/v1/agent-sessions:cleanup",
        response_model=AgentCleanupResponse,
        tags=["internal"],
    )
    def cleanup_agent_sessions(
        request: AgentCleanupRequest,
    ) -> AgentCleanupResponse | JSONResponse:
        counts = {
            CleanupOutcome.DELETED: 0,
            CleanupOutcome.RETRY_SCHEDULED: 0,
            CleanupOutcome.DEAD_LETTERED: 0,
        }
        drained = False
        with tracer().start_as_current_span("caffemate.worker.agent_session_cleanup"):
            try:
                for _ in range(request.limit):
                    outcome = session_cleanup.cleanup_one()
                    if outcome == CleanupOutcome.EMPTY:
                        drained = True
                        break
                    counts[outcome] += 1
            except OutboxConfigurationUnavailableError:
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content={"code": "AGENT_CLEANUP_CONFIGURATION_UNAVAILABLE"},
                )
        return AgentCleanupResponse(
            deleted=counts[CleanupOutcome.DELETED],
            retry_scheduled=counts[CleanupOutcome.RETRY_SCHEDULED],
            dead_lettered=counts[CleanupOutcome.DEAD_LETTERED],
            drained=drained,
        )

    @app.get(
        "/internal/v1/dead-letters",
        response_model=DeadLetterPage,
        tags=["internal"],
    )
    def list_dead_letters(
        limit: int = Query(default=50, ge=1, le=100),
        after_outbox_id: int | None = Query(default=None, ge=1),
    ) -> DeadLetterPage | JSONResponse:
        try:
            return dead_letters.list(limit=limit, after_outbox_id=after_outbox_id)
        except DeadLetterOperationError as error:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"code": error.code},
            )

    @app.post(
        "/internal/v1/dead-letters/{outbox_id}:reprocess",
        response_model=DeadLetterReprocessResult,
        tags=["internal"],
    )
    def reprocess_dead_letter(
        outbox_id: int,
        request: ReprocessDeadLetterRequest,
    ) -> DeadLetterReprocessResult | JSONResponse:
        try:
            return dead_letters.reprocess(outbox_id=outbox_id, request=request)
        except DeadLetterOperationError as error:
            response_status = {
                "DEAD_LETTER_NOT_FOUND": status.HTTP_404_NOT_FOUND,
                "DEAD_LETTER_REQUEST_ID_REUSED": status.HTTP_409_CONFLICT,
                "DEAD_LETTER_FAILURE_CODE_CHANGED": status.HTTP_409_CONFLICT,
                "DEAD_LETTER_STATE_CHANGED": status.HTTP_409_CONFLICT,
                "DEAD_LETTER_NOT_REPROCESSABLE": status.HTTP_422_UNPROCESSABLE_CONTENT,
            }.get(error.code, status.HTTP_503_SERVICE_UNAVAILABLE)
            return JSONResponse(
                status_code=response_status,
                content={"code": error.code},
            )

    return app


app = create_worker_app()
