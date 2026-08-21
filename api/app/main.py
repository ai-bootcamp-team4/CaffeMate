from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.auth import FirebaseIdentityVerifier, IdentityVerifier, UnconfiguredIdentityVerifier
from app.database import DatabaseHandle, create_database_handle
from app.domain.errors import (
    AuthenticationUnavailableError,
    ContractValidationError,
    DomainError,
    IdempotencyKeyReusedError,
    PersistenceUnavailableError,
    ProjectNotFoundError,
    StateVersionConflictError,
    UnauthenticatedError,
    WorkflowNotFoundError,
    WorkflowPreconditionError,
)
from app.domain.models import FounderState, Project
from app.projects.postgres_repository import PostgresProjectRepository
from app.projects.service import ProjectService
from app.projects.unavailable_repository import UnavailableProjectRepository
from app.settings import RuntimeSettings
from app.workflows.models import WorkflowCode, WorkflowEvent, WorkflowRun
from app.workflows.postgres_repository import PostgresWorkflowRepository
from app.workflows.service import WorkflowService
from app.workflows.unavailable_repository import UnavailableWorkflowRepository


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfirmOnboardingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    founder: FounderState


def create_app(
    *,
    project_service: ProjectService | None = None,
    workflow_service: WorkflowService | None = None,
    identity_verifier: IdentityVerifier | None = None,
) -> FastAPI:
    database_handle: DatabaseHandle | None = None
    settings = RuntimeSettings.from_environment()
    if project_service is None or workflow_service is None:
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

    if workflow_service is None:
        workflow_repository = (
            PostgresWorkflowRepository(
                database_handle.engine,
                policy_snapshot_id=settings.policy_snapshot_id,
            )
            if database_handle is not None and settings.policy_snapshot_id is not None
            else UnavailableWorkflowRepository()
        )
        workflows = WorkflowService(workflow_repository)
    else:
        workflows = workflow_service

    if identity_verifier is not None:
        verifier = identity_verifier
    elif settings.firebase_project_id:
        verifier = FirebaseIdentityVerifier(project_id=settings.firebase_project_id)
    else:
        verifier = UnconfiguredIdentityVerifier()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if database_handle is not None:
                database_handle.close()

    app = FastAPI(title="CaffeMate Control API", version="0.2.0", lifespan=lifespan)

    def current_user(authorization: Annotated[str | None, Header()] = None) -> str:
        if authorization is None:
            raise UnauthenticatedError("Bearer ID token is required")
        scheme, separator, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not token.strip():
            raise UnauthenticatedError("Bearer ID token is required")
        return verifier.verify(token.strip())

    @app.exception_handler(DomainError)
    async def domain_error_handler(_request: object, error: DomainError) -> JSONResponse:
        status_code = {
            ProjectNotFoundError: status.HTTP_404_NOT_FOUND,
            StateVersionConflictError: status.HTTP_409_CONFLICT,
            IdempotencyKeyReusedError: status.HTTP_409_CONFLICT,
            ContractValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
            AuthenticationUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
            PersistenceUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
            UnauthenticatedError: status.HTTP_401_UNAUTHORIZED,
            WorkflowNotFoundError: status.HTTP_404_NOT_FOUND,
            WorkflowPreconditionError: status.HTTP_409_CONFLICT,
        }.get(type(error), status.HTTP_400_BAD_REQUEST)
        return JSONResponse(status_code=status_code, content={"code": error.code})

    @app.get("/healthz", tags=["operations"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

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

    @app.get("/v1/projects", response_model=list[Project])
    def list_projects(user_id: Annotated[str, Depends(current_user)]) -> list[Project]:
        return service.list_projects(user_id=user_id)

    @app.post("/v1/projects/{project_id}/onboarding/confirm", response_model=Project)
    def confirm_onboarding(
        project_id: str,
        request: ConfirmOnboardingRequest,
        user_id: Annotated[str, Depends(current_user)],
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    ) -> Project:
        return service.confirm_onboarding(
            project_id=project_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
            founder=request.founder,
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
        return workflows.start(
            project_id=project_id,
            user_id=user_id,
            workflow_code=workflow_code,
            idempotency_key=idempotency_key,
        )

    @app.get(
        "/v1/projects/{project_id}/workflows/{workflow_run_id}",
        response_model=WorkflowRun,
    )
    def get_workflow(
        project_id: str,
        workflow_run_id: str,
        user_id: Annotated[str, Depends(current_user)],
    ) -> WorkflowRun:
        return workflows.get(
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
