"""Selected-property recompute integration tests."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from testcontainers.community.postgres import PostgresContainer

from app.main import create_app
from app.migrations import apply_migrations
from tests.workflow_test_support import IdentityFixture, execute_queued_workflow, workflow_service


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
    with TestClient(
        create_app(
            identity_verifier=IdentityFixture(),
            workflow_service=workflow_service(postgres_engine),
        )
    ) as client:
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
        started = client.post(
            f"/v1/projects/{project_id}/workflows/FIRST_PROPOSAL",
            headers={**headers, "Idempotency-Key": "property-analysis"},
            json={},
        )
        execute_queued_workflow(postgres_engine, started.json()["workflow_run_id"])
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
        application = application_response.json()
        assert application["recompute_workflow"]["status"] == "QUEUED"
        queued_progress = client.get(
            (
                f"/v1/projects/{project_id}/workflows/"
                f"{application['recompute_workflow']['workflow_run_id']}"
            ),
            headers=headers,
        ).json()
        assert queued_progress["completed_stage_count"] == 0
        assert queued_progress["total_stage_count"] == 6
        execute_queued_workflow(
            postgres_engine,
            application["recompute_workflow"]["workflow_run_id"],
        )
        finished_progress = client.get(
            (
                f"/v1/projects/{project_id}/workflows/"
                f"{application['recompute_workflow']['workflow_run_id']}"
            ),
            headers=headers,
        ).json()
        assert finished_progress["completed_stage_count"] == 6
        stage_statuses = {
            stage["stage_code"]: stage["status"] for stage in finished_progress["stages"]
        }
        assert stage_statuses == {
            "EVIDENCE_RETRIEVAL": "SKIPPED",
            "EVIDENCE_ASSESS": "SKIPPED",
            "PROPOSAL_GENERATION": "SKIPPED",
            "FINANCE_AND_RANK": "SUCCEEDED",
            "CANDIDATE_AUDIT": "SKIPPED",
            "COMMIT_RESULT": "SUCCEEDED",
        }
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
    with TestClient(
        create_app(
            identity_verifier=IdentityFixture(),
            workflow_service=workflow_service(postgres_engine),
        )
    ) as client:
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
        started = client.post(
            f"/v1/projects/{project_id}/workflows/FIRST_PROPOSAL",
            headers={**headers, "Idempotency-Key": "retained-property-analysis"},
            json={},
        )
        execute_queued_workflow(postgres_engine, started.json()["workflow_run_id"])
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
        execute_queued_workflow(
            postgres_engine,
            applied.json()["recompute_workflow"]["workflow_run_id"],
        )
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
        assert rerun.json()["status"] == "QUEUED"
        execute_queued_workflow(postgres_engine, rerun.json()["workflow_run_id"])
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
    with TestClient(
        create_app(
            identity_verifier=IdentityFixture(),
            workflow_service=workflow_service(postgres_engine),
        )
    ) as client:
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
        started = client.post(
            f"/v1/projects/{project_id}/workflows/FIRST_PROPOSAL",
            headers={**headers, "Idempotency-Key": "franchise-property-analysis"},
            json={},
        )
        execute_queued_workflow(postgres_engine, started.json()["workflow_run_id"])
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
        application = application_response.json()
        assert application["recompute_workflow"]["status"] == "QUEUED"
        queued_progress = client.get(
            (
                f"/v1/projects/{project_id}/workflows/"
                f"{application['recompute_workflow']['workflow_run_id']}"
            ),
            headers=headers,
        ).json()
        assert queued_progress["completed_stage_count"] == 0
        assert queued_progress["total_stage_count"] == 6
        execute_queued_workflow(
            postgres_engine,
            application["recompute_workflow"]["workflow_run_id"],
        )
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
    assert recalculated["financial_summary"]["initial_cash"]["base"] == 147_000_000
    assert recalculated["financial_summary"]["monthly_fixed_cost"]["base"] == 6_250_000
    assert current["decision_delta"]["candidate_changes"]
