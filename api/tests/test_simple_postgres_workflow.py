"""사용자 분석 요청은 한 트랜잭션에서 실행 결과와 단일 진행 기록을 저장해야 한다."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from testcontainers.community.postgres import PostgresContainer

from app.candidates.seed_registry import IndependentSeedRegistry
from app.domain.models import (
    BorrowingIntent,
    CafeTypePreference,
    FounderState,
    OperationMode,
)
from app.main import create_app
from app.migrations import apply_migrations
from app.projects.postgres_repository import PostgresProjectRepository
from app.projects.service import ProjectService
from app.results.postgres_repository import PostgresResultRepository
from app.workflows.models import WorkflowCode, WorkflowStatus
from app.workflows.postgres_repository import PostgresWorkflowRepository
from app.workflows.service import WorkflowService


class _Identity:
    def verify(self, bearer_token: str) -> str:
        assert bearer_token == "test-token"
        return "user-2"


@pytest.fixture(scope="module")
def postgres_engine() -> Iterator[Engine]:
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


def test_start_persists_result_and_one_completed_stage(
    postgres_engine: Engine,
) -> None:
    projects = ProjectService(PostgresProjectRepository(postgres_engine))
    project = projects.create_project(user_id="user-1", idempotency_key="create-1")
    projects.confirm_onboarding(
        project_id=project.project_id,
        user_id="user-1",
        idempotency_key="onboarding-1",
        founder=FounderState(
            target_area_input="서울특별시 마포구 공덕동",
            own_funds_krw=400_000_000,
            borrowing_intent=BorrowingIntent.NO,
            cafe_type_preference=CafeTypePreference.OPEN_TO_BOTH,
            operation_mode=OperationMode.DIRECT_FULL_TIME,
        ),
    )
    registry = IndependentSeedRegistry.load_default()
    workflows = WorkflowService(
        PostgresWorkflowRepository(
            postgres_engine,
            policy_snapshot_id="policy-1",
            seed_registry_id=registry.registry_id,
            seed_registry=registry,
        )
    )

    first = workflows.start(
        project_id=project.project_id,
        user_id="user-1",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="analysis-1",
    )
    replay = workflows.start(
        project_id=project.project_id,
        user_id="user-1",
        workflow_code=WorkflowCode.FIRST_PROPOSAL,
        idempotency_key="analysis-1",
    )
    progress = workflows.get_progress(
        project_id=project.project_id,
        workflow_run_id=first.workflow_run_id,
        user_id="user-1",
    )
    result = PostgresResultRepository(postgres_engine).get_current(
        project_id=project.project_id,
        user_id="user-1",
    )

    assert first.status == WorkflowStatus.SUCCEEDED
    assert replay.workflow_run_id == first.workflow_run_id
    assert progress.completed_stage_count == 1
    assert progress.total_stage_count == 1
    assert [stage.stage_code for stage in progress.stages] == ["RUN_PROPOSAL"]
    assert 1 <= len(result.candidates) <= 3
    assert result.workflow_run_id == first.workflow_run_id
    with postgres_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM workflow_outbox WHERE topic='WORKFLOW_STAGE_READY'")
            ).scalar_one()
            == 0
        )


def test_public_start_returns_completed_run_and_result(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        postgres_engine.url.render_as_string(hide_password=False),
    )
    monkeypatch.setenv("CAFFEMATE_POLICY_SNAPSHOT_ID", "policy-1")
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(create_app(identity_verifier=_Identity())) as client:
        created = client.post(
            "/v1/projects",
            headers={**headers, "Idempotency-Key": "create-user-2"},
            json={},
        )
        project_id = created.json()["project_id"]
        onboarded = client.post(
            f"/v1/projects/{project_id}/onboarding/confirm",
            headers={**headers, "Idempotency-Key": "onboard-user-2"},
            json={
                "founder": {
                    "target_area_input": "서울특별시 성동구 성수동1가",
                    "own_funds_krw": 400_000_000,
                    "borrowing_intent": "NO",
                    "cafe_type_preference": "OPEN_TO_BOTH",
                    "operation_mode": "DIRECT_FULL_TIME",
                }
            },
        )
        started = client.post(
            f"/v1/projects/{project_id}/workflows/FIRST_PROPOSAL",
            headers={**headers, "Idempotency-Key": "analysis-user-2"},
            json={},
        )
        result = client.get(f"/v1/projects/{project_id}/result", headers=headers)

    assert created.status_code == 201
    assert onboarded.status_code == 200
    assert started.status_code == 202
    assert started.json()["status"] == "SUCCEEDED"
    assert result.status_code == 200
    assert len(result.json()["candidates"]) >= 1


def test_property_terms_recalculate_selected_candidate_with_actual_costs(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        postgres_engine.url.render_as_string(hide_password=False),
    )
    monkeypatch.setenv("CAFFEMATE_POLICY_SNAPSHOT_ID", "policy-1")
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(create_app(identity_verifier=_Identity())) as client:
        project_id = client.post(
            "/v1/projects",
            headers={**headers, "Idempotency-Key": "property-create"},
            json={},
        ).json()["project_id"]
        client.post(
            f"/v1/projects/{project_id}/onboarding/confirm",
            headers={**headers, "Idempotency-Key": "property-onboard"},
            json={
                "founder": {
                    "target_area_input": "서울특별시 마포구 공덕동",
                    "own_funds_krw": 400_000_000,
                    "borrowing_intent": "NO",
                    "cafe_type_preference": "INDEPENDENT_ONLY",
                    "operation_mode": "DIRECT_FULL_TIME",
                }
            },
        )
        client.post(
            f"/v1/projects/{project_id}/workflows/FIRST_PROPOSAL",
            headers={**headers, "Idempotency-Key": "property-analysis"},
            json={},
        )
        first = client.get(f"/v1/projects/{project_id}/result", headers=headers).json()
        selected_candidate = next(
            candidate
            for candidate in first["candidates"]
            if candidate["independent_model"]["model_id"] == "independent-small-takeout-v1"
        )
        selection_response = client.post(
            f"/v1/projects/{project_id}/candidate-selections",
            headers={**headers, "Idempotency-Key": "property-select"},
            json={
                "result_bundle_id": first["result_bundle_id"],
                "candidate_id": selected_candidate["candidate_id"],
                "expected_head": first["current_head"],
            },
        )
        assert selection_response.status_code == 201
        selection = selection_response.json()
        application_response = client.post(
            (
                f"/v1/projects/{project_id}/candidate-selections/"
                f"{selection['selection_id']}/property-terms"
            ),
            headers={**headers, "Idempotency-Key": "property-apply"},
            json={
                "expected_state_version": selection["selected_state_version"],
                "terms": {
                    "address": "서울특별시 마포구 공덕동 실제 점포",
                    "area_sqm": 33,
                    "floor": "1층",
                    "deposit_krw": 30_000_000,
                    "monthly_rent_krw": 2_200_000,
                    "management_fee_krw": 200_000,
                    "key_money_krw": 10_000_000,
                },
            },
        )
        assert application_response.status_code == 201
        current = client.get(
            f"/v1/projects/{project_id}/result",
            headers=headers,
        ).json()

    recalculated = next(
        candidate
        for candidate in current["candidates"]
        if candidate["independent_model"]["model_id"] == "independent-small-takeout-v1"
    )
    assert current["result_bundle_id"] != first["result_bundle_id"]
    assert recalculated["financial_summary"]["initial_cash"]["base"] == 134_500_000
    assert recalculated["financial_summary"]["monthly_fixed_cost"]["base"] == 6_000_000
    assert current["decision_delta"]["candidate_changes"]


def test_later_recompute_preserves_latest_selected_property_terms(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """후속 재계산은 사용자가 입력한 실제 점포 비용을 기본 가정으로 되돌리지 않는다."""

    monkeypatch.setenv(
        "DATABASE_URL",
        postgres_engine.url.render_as_string(hide_password=False),
    )
    monkeypatch.setenv("CAFFEMATE_POLICY_SNAPSHOT_ID", "policy-1")
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(create_app(identity_verifier=_Identity())) as client:
        project_id = client.post(
            "/v1/projects",
            headers={**headers, "Idempotency-Key": "retained-property-create"},
            json={},
        ).json()["project_id"]
        client.post(
            f"/v1/projects/{project_id}/onboarding/confirm",
            headers={**headers, "Idempotency-Key": "retained-property-onboard"},
            json={
                "founder": {
                    "target_area_input": "서울특별시 마포구 공덕동",
                    "own_funds_krw": 400_000_000,
                    "borrowing_intent": "NO",
                    "cafe_type_preference": "INDEPENDENT_ONLY",
                    "operation_mode": "DIRECT_FULL_TIME",
                }
            },
        )
        client.post(
            f"/v1/projects/{project_id}/workflows/FIRST_PROPOSAL",
            headers={**headers, "Idempotency-Key": "retained-property-analysis"},
            json={},
        )
        first = client.get(f"/v1/projects/{project_id}/result", headers=headers).json()
        selected_candidate = next(
            candidate
            for candidate in first["candidates"]
            if candidate["independent_model"]["model_id"]
            == "independent-small-takeout-v1"
        )
        selection = client.post(
            f"/v1/projects/{project_id}/candidate-selections",
            headers={**headers, "Idempotency-Key": "retained-property-select"},
            json={
                "result_bundle_id": first["result_bundle_id"],
                "candidate_id": selected_candidate["candidate_id"],
                "expected_head": first["current_head"],
            },
        ).json()
        applied = client.post(
            (
                f"/v1/projects/{project_id}/candidate-selections/"
                f"{selection['selection_id']}/property-terms"
            ),
            headers={**headers, "Idempotency-Key": "retained-property-apply"},
            json={
                "expected_state_version": selection["selected_state_version"],
                "terms": {
                    "address": "서울특별시 마포구 공덕동 실제 점포",
                    "area_sqm": 33,
                    "floor": "1층",
                    "deposit_krw": 30_000_000,
                    "monthly_rent_krw": 2_200_000,
                    "management_fee_krw": 200_000,
                    "key_money_krw": 10_000_000,
                },
            },
        )
        assert applied.status_code == 201
        property_result = client.get(
            f"/v1/projects/{project_id}/result",
            headers=headers,
        ).json()
        current_candidate = next(
            candidate
            for candidate in property_result["candidates"]
            if candidate["independent_model"]["model_id"]
            == "independent-small-takeout-v1"
        )
        reselection = client.post(
            f"/v1/projects/{project_id}/candidate-selections",
            headers={**headers, "Idempotency-Key": "retained-property-reselect"},
            json={
                "result_bundle_id": property_result["result_bundle_id"],
                "candidate_id": current_candidate["candidate_id"],
                "expected_head": property_result["current_head"],
            },
        )
        assert reselection.status_code == 201
        rerun = client.post(
            f"/v1/projects/{project_id}/workflows/FIRST_PROPOSAL",
            headers={**headers, "Idempotency-Key": "retained-property-rerun"},
            json={},
        )
        assert rerun.status_code == 202
        assert rerun.json()["status"] == "SUCCEEDED"
        retained = client.get(
            f"/v1/projects/{project_id}/result",
            headers=headers,
        ).json()
    candidate = next(
        candidate
        for candidate in retained["candidates"]
        if candidate["independent_model"]["model_id"] == "independent-small-takeout-v1"
    )
    assert candidate["financial_summary"]["initial_cash"]["base"] == 134_500_000
    assert candidate["financial_summary"]["monthly_fixed_cost"]["base"] == 6_000_000


def test_property_terms_recalculate_selected_franchise_with_actual_costs(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        postgres_engine.url.render_as_string(hide_password=False),
    )
    monkeypatch.setenv("CAFFEMATE_POLICY_SNAPSHOT_ID", "policy-1")
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(create_app(identity_verifier=_Identity())) as client:
        project_id = client.post(
            "/v1/projects",
            headers={**headers, "Idempotency-Key": "franchise-property-create"},
            json={},
        ).json()["project_id"]
        client.post(
            f"/v1/projects/{project_id}/onboarding/confirm",
            headers={**headers, "Idempotency-Key": "franchise-property-onboard"},
            json={
                "founder": {
                    "target_area_input": "서울특별시 성동구 성수동1가",
                    "own_funds_krw": 400_000_000,
                    "borrowing_intent": "NO",
                    "cafe_type_preference": "FRANCHISE_ONLY",
                    "operation_mode": "DIRECT_FULL_TIME",
                }
            },
        )
        client.post(
            f"/v1/projects/{project_id}/workflows/FIRST_PROPOSAL",
            headers={**headers, "Idempotency-Key": "franchise-property-analysis"},
            json={},
        )
        first = client.get(f"/v1/projects/{project_id}/result", headers=headers).json()
        selected_candidate = next(
            candidate
            for candidate in first["candidates"]
            if candidate["franchise"]["brand_id"] == "kr-ediya-coffee"
        )
        selection_response = client.post(
            f"/v1/projects/{project_id}/candidate-selections",
            headers={**headers, "Idempotency-Key": "franchise-property-select"},
            json={
                "result_bundle_id": first["result_bundle_id"],
                "candidate_id": selected_candidate["candidate_id"],
                "expected_head": first["current_head"],
            },
        )
        assert selection_response.status_code == 201
        selection = selection_response.json()
        application_response = client.post(
            (
                f"/v1/projects/{project_id}/candidate-selections/"
                f"{selection['selection_id']}/property-terms"
            ),
            headers={**headers, "Idempotency-Key": "franchise-property-apply"},
            json={
                "expected_state_version": selection["selected_state_version"],
                "terms": {
                    "address": "서울특별시 성동구 성수동1가 실제 점포",
                    "area_sqm": 66,
                    "floor": "1층",
                    "deposit_krw": 30_000_000,
                    "monthly_rent_krw": 2_200_000,
                    "management_fee_krw": 200_000,
                    "key_money_krw": 10_000_000,
                },
            },
        )
        assert application_response.status_code == 201
        current = client.get(
            f"/v1/projects/{project_id}/result",
            headers=headers,
        ).json()

    recalculated = next(
        candidate
        for candidate in current["candidates"]
        if candidate["franchise"]["brand_id"] == "kr-ediya-coffee"
    )
    assert current["result_bundle_id"] != first["result_bundle_id"]
    assert recalculated["financial_summary"]["initial_cash"]["base"] == 210_321_000
    assert recalculated["financial_summary"]["monthly_fixed_cost"]["base"] == 12_600_000
    assert current["decision_delta"]["candidate_changes"]
