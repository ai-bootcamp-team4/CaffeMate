from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth import IdentityVerifier
from app.main import create_app
from app.projects.in_memory_repository import InMemoryProjectRepository
from app.projects.service import ProjectService


class FakeIdentityVerifier(IdentityVerifier):
    def verify(self, bearer_token: str) -> str:
        return bearer_token


@pytest.fixture
def repository() -> InMemoryProjectRepository:
    counter = 0
    instant = datetime(2026, 8, 21, 0, 0, tzinfo=UTC)

    def new_id() -> str:
        nonlocal counter
        counter += 1
        return f"id-{counter}"

    def now() -> datetime:
        nonlocal instant
        current = instant
        instant += timedelta(seconds=1)
        return current

    return InMemoryProjectRepository(now=now, new_id=new_id)


@pytest.fixture
def client(repository: InMemoryProjectRepository) -> Iterator[TestClient]:
    app = create_app(
        project_service=ProjectService(repository),
        identity_verifier=FakeIdentityVerifier(),
    )
    with TestClient(app) as test_client:
        yield test_client
