from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Protocol

from app.database import DatabaseHandle, create_database_handle
from app.settings import RuntimeSettings
from app.workflows.execution_repository import PostgresStageExecutionRepository
from fastapi import FastAPI, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from worker.control_api import ControlApiStageProcessor, GoogleIdentityTokenProvider
from worker.outbox import OutboxPublisher, PostgresOutboxRepository
from worker.pubsub import (
    GooglePubSubPublisher,
    InvalidPubSubEnvelopeError,
    PubSubDelivery,
    decode_push_envelope,
)
from worker.runtime import DeliveryOutcome, DurableWorker, WorkerRetryRequiredError


class WorkerHandler(Protocol):
    def handle(self, delivery: PubSubDelivery) -> DeliveryOutcome: ...


class OutboxDispatcher(Protocol):
    def publish_one(self) -> bool: ...


class OutboxDispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=20, ge=1, le=100)


class OutboxDispatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    published: int = Field(ge=0)
    drained: bool


class OutboxConfigurationUnavailableError(RuntimeError):
    pass


class UnavailableWorker:
    def handle(self, delivery: PubSubDelivery) -> DeliveryOutcome:
        del delivery
        raise RuntimeError("Worker runtime is not configured")


class UnavailableOutboxDispatcher:
    def publish_one(self) -> bool:
        raise OutboxConfigurationUnavailableError("Outbox publisher is not configured")


def create_worker_app(
    *,
    worker: WorkerHandler | None = None,
    outbox_dispatcher: OutboxDispatcher | None = None,
    expected_subscription: str | None = None,
) -> FastAPI:
    database_handle: DatabaseHandle | None = None
    processor: ControlApiStageProcessor | None = None
    settings = RuntimeSettings.from_environment()
    subscription = expected_subscription or settings.pubsub_subscription

    if worker is not None:
        runtime = worker
    else:
        database_handle = create_database_handle(settings)
        if (
            database_handle is not None
            and settings.control_api_url
            and settings.control_api_audience
            and settings.worker_id
        ):
            processor = ControlApiStageProcessor(
                base_url=settings.control_api_url,
                audience=settings.control_api_audience,
                token_provider=GoogleIdentityTokenProvider(),
            )
            runtime = DurableWorker(
                PostgresStageExecutionRepository(database_handle.engine),
                processor,
                worker_id=settings.worker_id,
            )
        else:
            runtime = UnavailableWorker()

    if outbox_dispatcher is not None:
        dispatcher = outbox_dispatcher
    elif (
        database_handle is not None
        and settings.workflow_stage_topic_resource
        and settings.worker_id
    ):
        dispatcher = OutboxPublisher(
            PostgresOutboxRepository(database_handle.engine),
            GooglePubSubPublisher(
                topic_resources={
                    "WORKFLOW_STAGE_READY": settings.workflow_stage_topic_resource,
                }
            ),
            publisher_id=settings.worker_id,
            logical_topic="WORKFLOW_STAGE_READY",
        )
    else:
        dispatcher = UnavailableOutboxDispatcher()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if processor is not None:
                processor.close()
            if database_handle is not None:
                database_handle.close()

    app = FastAPI(title="CaffeMate Worker", version="0.2.0", lifespan=lifespan)

    @app.get("/healthz", tags=["operations"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/internal/v1/pubsub/workflow-stages", status_code=status.HTTP_204_NO_CONTENT)
    def handle_workflow_stage(body: Mapping[str, object]) -> Response:
        if not subscription:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"code": "WORKER_CONFIGURATION_UNAVAILABLE"},
            )
        try:
            delivery = decode_push_envelope(body, expected_subscription=subscription)
        except InvalidPubSubEnvelopeError:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"code": "INVALID_PUBSUB_ENVELOPE"},
            )
        try:
            runtime.handle(delivery)
        except WorkerRetryRequiredError:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"code": "WORKER_RETRY_REQUIRED"},
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/internal/v1/outbox:publish",
        response_model=OutboxDispatchResponse,
        tags=["internal"],
    )
    def publish_outbox(request: OutboxDispatchRequest) -> OutboxDispatchResponse | JSONResponse:
        published = 0
        try:
            while published < request.limit and dispatcher.publish_one():
                published += 1
        except OutboxConfigurationUnavailableError:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"code": "OUTBOX_CONFIGURATION_UNAVAILABLE"},
            )
        return OutboxDispatchResponse(
            published=published,
            drained=published < request.limit,
        )

    return app


app = create_worker_app()
