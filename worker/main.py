from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Protocol

from app.database import DatabaseHandle, create_database_handle
from app.settings import RuntimeSettings
from app.workflows.execution_repository import PostgresStageExecutionRepository
from fastapi import FastAPI, status
from fastapi.responses import JSONResponse, Response

from worker.control_api import ControlApiStageProcessor, GoogleIdentityTokenProvider
from worker.pubsub import (
    InvalidPubSubEnvelopeError,
    PubSubDelivery,
    decode_push_envelope,
)
from worker.runtime import DeliveryOutcome, DurableWorker, WorkerRetryRequiredError


class WorkerHandler(Protocol):
    def handle(self, delivery: PubSubDelivery) -> DeliveryOutcome: ...


class UnavailableWorker:
    def handle(self, delivery: PubSubDelivery) -> DeliveryOutcome:
        del delivery
        raise RuntimeError("Worker runtime is not configured")


def create_worker_app(
    *,
    worker: WorkerHandler | None = None,
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

    return app


app = create_worker_app()
