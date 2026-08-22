from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.domain.models import (
    BorrowingIntent,
    CafeTypePreference,
    FounderState,
    OperationMode,
)
from app.projects.in_memory_repository import InMemoryProjectRepository
from app.projects.service import ProjectService


def auth(user_id: str = "user-1") -> dict[str, str]:
    return {"Authorization": f"Bearer {user_id}"}


def founder_payload() -> dict[str, object]:
    return {
        "target_area_input": "수원 아주대 부근",
        "own_funds_krw": 50_000_000,
        "borrowing_intent": "UNDECIDED",
        "cafe_type_preference": "OPEN_TO_BOTH",
        "operation_mode": "DIRECT_FULL_TIME",
        "preferences": [],
        "avoidances": [],
    }


def create_project(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/v1/projects",
        headers={**auth(), "Idempotency-Key": "create-1"},
        json={},
    )
    assert response.status_code == 201
    return response.json()


def test_liveness_is_available_without_user_authentication(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_project_has_no_state_until_onboarding_is_confirmed(client: TestClient) -> None:
    project = create_project(client)
    assert project["state"] is None

    response = client.post(
        f"/v1/projects/{project['project_id']}/onboarding/confirm",
        headers={**auth(), "Idempotency-Key": "onboarding-1"},
        json={"founder": founder_payload()},
    )

    assert response.status_code == 200
    state = response.json()["state"]
    assert state["state_version"] == 1
    assert state["status"] == "ANALYZING"
    assert state["area"]["resolution_status"] == "UNRESOLVED"
    assert state["founder"]["target_area_input"] == "수원 아주대 부근"


def test_project_list_only_returns_current_users_projects(client: TestClient) -> None:
    first = create_project(client)
    second = client.post(
        "/v1/projects",
        headers={**auth("user-2"), "Idempotency-Key": "create-2"},
        json={},
    ).json()

    response = client.get("/v1/projects", headers=auth())

    assert response.status_code == 200
    assert [project["project_id"] for project in response.json()] == [first["project_id"]]
    assert second["project_id"] not in {project["project_id"] for project in response.json()}


def test_same_idempotency_key_replays_without_new_event(
    client: TestClient,
    repository: InMemoryProjectRepository,
) -> None:
    project = create_project(client)
    url = f"/v1/projects/{project['project_id']}/onboarding/confirm"
    headers = {**auth(), "Idempotency-Key": "onboarding-1"}
    body = {"founder": founder_payload()}

    first = client.post(url, headers=headers, json=body)
    second = client.post(url, headers=headers, json=body)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert [event.event_type for event in repository.events] == [
        "PROJECT_CREATED",
        "ONBOARDING_CONFIRMED",
    ]


def test_reused_idempotency_key_with_different_payload_returns_409(client: TestClient) -> None:
    project = create_project(client)
    url = f"/v1/projects/{project['project_id']}/onboarding/confirm"
    headers = {**auth(), "Idempotency-Key": "onboarding-1"}
    first_body = founder_payload()
    second_body = {**founder_payload(), "own_funds_krw": 40_000_000}

    assert client.post(url, headers=headers, json={"founder": first_body}).status_code == 200
    response = client.post(url, headers=headers, json={"founder": second_body})

    assert response.status_code == 409
    assert response.json() == {"code": "IDEMPOTENCY_KEY_REUSED"}


def test_other_user_cannot_discover_or_change_project(client: TestClient) -> None:
    project = create_project(client)
    project_id = project["project_id"]

    read = client.get(f"/v1/projects/{project_id}", headers=auth("user-2"))
    write = client.post(
        f"/v1/projects/{project_id}/onboarding/confirm",
        headers={**auth("user-2"), "Idempotency-Key": "attack-1"},
        json={"founder": founder_payload()},
    )

    assert read.status_code == 404
    assert write.status_code == 404
    assert read.json() == write.json() == {"code": "PROJECT_NOT_FOUND"}


def test_second_onboarding_command_returns_version_conflict(client: TestClient) -> None:
    project = create_project(client)
    url = f"/v1/projects/{project['project_id']}/onboarding/confirm"

    assert client.post(
        url,
        headers={**auth(), "Idempotency-Key": "onboarding-1"},
        json={"founder": founder_payload()},
    ).status_code == 200
    response = client.post(
        url,
        headers={**auth(), "Idempotency-Key": "onboarding-2"},
        json={"founder": founder_payload()},
    )

    assert response.status_code == 409
    assert response.json() == {"code": "STATE_VERSION_CONFLICT"}


def test_concurrent_duplicate_creates_one_onboarding_event(
    repository: InMemoryProjectRepository,
) -> None:
    service = ProjectService(repository)
    project = service.create_project(user_id="user-1", idempotency_key="create-1")
    founder = FounderState(
        target_area_input="수원 아주대 부근",
        own_funds_krw=50_000_000,
        borrowing_intent=BorrowingIntent.UNDECIDED,
        cafe_type_preference=CafeTypePreference.OPEN_TO_BOTH,
        operation_mode=OperationMode.DIRECT_FULL_TIME,
    )

    def confirm() -> int:
        result = service.confirm_onboarding(
            project_id=project.project_id,
            user_id="user-1",
            idempotency_key="same-request",
            founder=founder,
        )
        assert result.state is not None
        return result.state.state_version

    with ThreadPoolExecutor(max_workers=2) as executor:
        versions = list(executor.map(lambda _index: confirm(), range(2)))

    assert versions == [1, 1]
    assert [event.event_type for event in repository.events].count("ONBOARDING_CONFIRMED") == 1


def test_authentication_is_required(client: TestClient) -> None:
    response = client.post("/v1/projects", headers={"Idempotency-Key": "create-1"}, json={})
    assert response.status_code == 401
    assert response.json() == {"code": "UNAUTHENTICATED"}

    wrong_scheme = client.get("/v1/projects", headers={"Authorization": "Basic value"})
    assert wrong_scheme.status_code == 401
    assert wrong_scheme.json() == {"code": "UNAUTHENTICATED"}


def test_default_app_fails_closed_when_identity_is_unconfigured() -> None:
    from app.main import create_app

    with TestClient(create_app()) as default_client:
        response = default_client.post(
            "/v1/projects",
            headers={"Authorization": "Bearer token", "Idempotency-Key": "create-1"},
            json={},
        )

    assert response.status_code == 503
    assert response.json() == {"code": "AUTHENTICATION_UNAVAILABLE"}


def test_openapi_exposes_control_api_contract(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "CaffeMate Control API"
    assert "/v1/projects/{project_id}/onboarding/confirm" in schema["paths"]
    workflow_path = "/v1/projects/{project_id}/workflows/{workflow_run_id}"
    workflow_response = schema["paths"][workflow_path]["get"]["responses"]["200"]
    assert workflow_response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/WorkflowProgress"
    }
    required = set(schema["components"]["schemas"]["WorkflowProgress"]["required"])
    assert {
        "stages",
        "completed_stage_count",
        "total_stage_count",
        "current_stage_codes",
        "human_review_requests",
        "terminal_reason_codes",
    }.issubset(required)
    result_response = schema["paths"]["/v1/projects/{project_id}/result"]["get"][
        "responses"
    ]["200"]
    assert result_response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ResultView"
    }
    result_required = set(schema["components"]["schemas"]["ResultView"]["required"])
    assert {"freshness", "stale_head_dimensions", "current_head"}.issubset(
        result_required
    )
    preview_path = "/v1/projects/{project_id}/feedback/previews"
    preview_response = schema["paths"][preview_path]["post"]["responses"]["201"]
    assert preview_response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/FeedbackPreview"
    }
    assert (
        "/v1/projects/{project_id}/feedback/previews/{preview_id}"
        in schema["paths"]
    )
    assert (
        "/v1/projects/{project_id}/feedback/{preview_id}/confirm"
        in schema["paths"]
    )
    assert (
        "/v1/projects/{project_id}/feedback/{preview_id}/cancel"
        in schema["paths"]
    )
    assert "/v1/projects/{project_id}/candidate-selections" in schema["paths"]
    assert (
        "/v1/projects/{project_id}/documents/{document_revision_id}/extraction-form:apply"
        in schema["paths"]
    )
    assert "/internal/v1/evidence:refresh" in schema["paths"]
    assert "/v1/projects/{project_id}/documents/uploads" in schema["paths"]
    assert "/v1/projects/{project_id}/documents/uploads:complete" in schema["paths"]
    assert (
        "/v1/projects/{project_id}/documents/{document_revision_id}/extraction-form"
        in schema["paths"]
    )


def test_evidence_refresh_requires_worker_identity(client: TestClient) -> None:
    response = client.post(
        "/internal/v1/evidence:refresh",
        json={
            "project_id": "project-1",
            "observations": [
                {
                    "source_ref": "official://source",
                    "source_revision": "v2",
                    "source_observed_at": "2026-08-22T00:00:00Z",
                    "availability": "AVAILABLE",
                }
            ],
        },
    )

    assert response.status_code == 401
    assert response.json() == {"code": "UNAUTHENTICATED"}
