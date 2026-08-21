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
)
from app.domain.models import FounderState, Project
from app.projects.postgres_repository import PostgresProjectRepository
from app.projects.service import ProjectService
from app.projects.unavailable_repository import UnavailableProjectRepository
from app.settings import RuntimeSettings


class EmptyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfirmOnboardingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    founder: FounderState


def create_app(
    *,
    project_service: ProjectService | None = None,
    identity_verifier: IdentityVerifier | None = None,
) -> FastAPI:
    database_handle: DatabaseHandle | None = None
    settings = RuntimeSettings.from_environment()
    if project_service is None:
        database_handle = create_database_handle(settings)
        repository = (
            PostgresProjectRepository(database_handle.engine)
            if database_handle is not None
            else UnavailableProjectRepository()
        )
        service = ProjectService(repository)
    else:
        service = project_service

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

    return app


app = create_app()
