"""사용자 분석 요청은 한 트랜잭션에서 실행 결과와 단일 진행 기록을 저장해야 한다."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from testcontainers.community.postgres import PostgresContainer

from app.candidates.seed_registry import IndependentSeedRegistry
from app.documents.extraction import DocumentExtractionService
from app.documents.models import AppliedDocumentClaim
from app.domain.models import (
    BorrowingIntent,
    CafeTypePreference,
    FounderState,
    OperationMode,
    VentureState,
)
from app.finance.labor_benchmark import MinimumWageReference
from app.finance.labor_oncost import (
    EmployerInsuranceComponent,
    EmployerSocialInsuranceReference,
)
from app.main import create_app
from app.migrations import apply_migrations
from app.projects.postgres_repository import PostgresProjectRepository
from app.projects.service import ProjectService
from app.results.postgres_repository import PostgresResultRepository
from app.workflows.linear_agent_pipeline import LinearMultiAgentProposalPipeline
from app.workflows.models import HeadFence, WorkflowCode, WorkflowStatus
from app.workflows.postgres_repository import PostgresWorkflowRepository
from app.workflows.selective_start import start_selective_first_proposal
from app.workflows.service import WorkflowService
from app.workflows.simple_proposal import SimpleProposalBuilder


class _Identity:
    def verify(self, bearer_token: str) -> str:
        assert bearer_token == "test-token"
        return "user-2"


class _UnusedDocumentRuntime:
    def invoke(self, _task: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("conflict scope test does not invoke the document agent")


class _RepositoryPipeline:
    """Repository tests keep persistence isolated from external Agent and MCP calls."""

    def __init__(self, registry: IndependentSeedRegistry) -> None:
        self._builder = SimpleProposalBuilder(registry)

    def run(self, **kwargs: Any) -> object:
        return self._builder.build(
            state=kwargs["state"],
            evidence_records=kwargs["evidence_records"],
            property_context=kwargs.get("property_context"),
            case_fact_resolution=kwargs.get("case_fact_resolution"),
            minimum_wage_references=[_minimum_wage_reference()],
            employer_social_insurance_references=[_social_insurance_reference()],
            franchise_universe=[
                {
                    "brand_id": "kr-ediya-coffee",
                    "display_name": "이디야커피",
                    "individual_franchise_eligibility": "VERIFIED",
                    "evidence_refs": ["franchise-eligibility:ediya"],
                    "finance_profile": {
                        "currency": "KRW",
                        "coverage": "PARTIAL",
                        "value_kind": "EVIDENCED_FACT",
                        "known_initial_cost_range_krw": {
                            "low": 27_000_000,
                            "base": 27_000_000,
                            "high": 27_000_000,
                        },
                        "reference_area_sqm": None,
                        "monthly_royalty_krw": 250_000,
                        "sales_royalty_bps": None,
                        "evidence_refs": ["franchise-cost:ediya"],
                        "source_refs": ["https://example.com/ediya"],
                        "scope_note": "repository test fixture",
                        "missing_costs": [
                            "DEPOSIT",
                            "ACQUISITION_OR_PREMIUM",
                            "CONSTRUCTION",
                            "EQUIPMENT",
                            "OPERATING_RESERVE",
                        ],
                    },
                }
            ],
        )


def _minimum_wage_reference() -> MinimumWageReference:
    return MinimumWageReference(
        evidence_ref="cost-reference:kr-minimum-wage-2026",
        effective_from="2026-01-01",
        effective_to="2026-12-31",
        hourly_rate_krw=10_320,
        monthly_equivalent_hours=209,
        monthly_equivalent_krw=2_156_880,
        source_title="최저임금위원회 연도별 최저임금",
        source_ref="https://www.minimumwage.go.kr/minWage/policy/decisionMain.do",
        data_date="2025-08-05",
    )


def _social_insurance_reference() -> EmployerSocialInsuranceReference:
    component_rows = (
        ("NATIONAL_PENSION", 47_500, "https://www.nps.or.kr/"),
        ("HEALTH_LONG_TERM_CARE", 40_674, "https://www.nhis.or.kr/"),
        ("UNEMPLOYMENT_BENEFIT", 9_000, "https://www.moel.go.kr/"),
        ("EMPLOYMENT_STABILIZATION_VOCATIONAL", 2_500, "https://www.moel.go.kr/"),
    )
    return EmployerSocialInsuranceReference(
        effective_from="2026-01-01",
        effective_to="2026-12-31",
        workplace_employee_upper_bound=149,
        components=tuple(
            EmployerInsuranceComponent(
                component=name,
                employer_rate_ppm=rate,
                evidence_ref=f"cost-reference:2026:{name.lower()}",
                source_title=f"official {name}",
                source_ref=source_ref,
                data_date="2026-01-01",
            )
            for name, rate, source_ref in component_rows
        ),
        unsupported_components=("WORKERS_COMPENSATION_INDUSTRY_RATE_REQUIRED",),
        excluded_adjustments=(
            "CONTRIBUTION_BASE_CAPS_AND_FLOORS_NOT_APPLIED",
            "EXEMPTIONS_NOT_APPLIED",
            "SUPPORT_PROGRAMS_NOT_APPLIED",
        ),
    )


def _workflow_service(engine: Engine) -> WorkflowService:
    registry = IndependentSeedRegistry.load_default()
    return WorkflowService(
        PostgresWorkflowRepository(
            engine,
            policy_snapshot_id="policy-1",
            seed_registry_id=registry.registry_id,
            pipeline=cast(
                LinearMultiAgentProposalPipeline,
                _RepositoryPipeline(registry),
            ),
            seed_registry=registry,
        )
    )


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
            pipeline=cast(
                LinearMultiAgentProposalPipeline,
                _RepositoryPipeline(registry),
            ),
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
    with TestClient(
        create_app(
            identity_verifier=_Identity(),
            workflow_service=_workflow_service(postgres_engine),
        )
    ) as client:
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
    with TestClient(
        create_app(
            identity_verifier=_Identity(),
            workflow_service=_workflow_service(postgres_engine),
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
    assert recalculated["financial_summary"]["monthly_fixed_cost"]["base"] == 6_107_493
    assert recalculated["property_context"] == {
        "property_input_id": application_response.json()["property_input_id"],
        "address": "서울특별시 마포구 공덕동 실제 점포",
        "area_sqm": 33.0,
        "floor": "1층",
        "deposit_krw": 30_000_000,
        "monthly_rent_krw": 2_200_000,
        "management_fee_krw": 200_000,
        "key_money_krw": 10_000_000,
        "provenance": "USER_INPUT",
    }
    assert all(
        candidate["property_context"] is None
        for candidate in current["candidates"]
        if candidate["candidate_id"] != recalculated["candidate_id"]
    )
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
            identity_verifier=_Identity(),
            workflow_service=_workflow_service(postgres_engine),
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
    assert candidate["financial_summary"]["monthly_fixed_cost"]["base"] == 6_107_493
    assert candidate["property_context"] == {
        "property_input_id": applied.json()["property_input_id"],
        "address": "서울특별시 마포구 공덕동 실제 점포",
        "area_sqm": 33.0,
        "floor": "1층",
        "deposit_krw": 30_000_000,
        "monthly_rent_krw": 2_200_000,
        "management_fee_krw": 200_000,
        "key_money_krw": 10_000_000,
        "provenance": "USER_INPUT",
    }


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
            identity_verifier=_Identity(),
            workflow_service=_workflow_service(postgres_engine),
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
    assert recalculated["financial_summary"]["initial_cash"]["base"] == 147_000_000
    assert recalculated["financial_summary"]["monthly_fixed_cost"]["base"] == 6_250_000
    assert recalculated["property_context"] == {
        "property_input_id": application_response.json()["property_input_id"],
        "address": "서울특별시 성동구 성수동1가 실제 점포",
        "area_sqm": 66.0,
        "floor": "1층",
        "deposit_krw": 30_000_000,
        "monthly_rent_krw": 2_200_000,
        "management_fee_krw": 200_000,
        "key_money_krw": 10_000_000,
        "provenance": "USER_INPUT",
    }
    hq_requirement = next(
        item
        for item in recalculated["verification_requirements"]
        if item["requirement_id"] == "FRANCHISE_AREA_APPROVAL"
    )
    assert hq_requirement["status"] == "EXTERNAL_CONFIRMATION_REQUIRED"
    assert hq_requirement["decision_role"] == "VERIFICATION_ONLY"
    assert hq_requirement["resolver"] == "FRANCHISE_HQ"
    assert hq_requirement["resolution_action"] == {
        "action_type": "EXTERNAL_CONFIRMATION",
        "target_fields": ["franchise.area_availability"],
        "accepted_document_types": [],
    }
    assert current["decision_delta"]["candidate_changes"]


def test_confirmed_franchise_percentage_royalty_recalculates_variable_margin(
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
            identity_verifier=_Identity(),
            workflow_service=_workflow_service(postgres_engine),
        )
    ) as client:
        project_id = client.post(
            "/v1/projects",
            headers={**headers, "Idempotency-Key": "royalty-create"},
            json={},
        ).json()["project_id"]
        client.post(
            f"/v1/projects/{project_id}/onboarding/confirm",
            headers={**headers, "Idempotency-Key": "royalty-onboard"},
            json={
                "founder": {
                    "target_area_input": "서울특별시 마포구 공덕동",
                    "own_funds_krw": 400_000_000,
                    "borrowing_intent": "NO",
                    "cafe_type_preference": "FRANCHISE_ONLY",
                    "operation_mode": "DIRECT_FULL_TIME",
                }
            },
        )
        client.post(
            f"/v1/projects/{project_id}/workflows/FIRST_PROPOSAL",
            headers={**headers, "Idempotency-Key": "royalty-analysis"},
            json={},
        )
        first = client.get(f"/v1/projects/{project_id}/result", headers=headers).json()
        selected_candidate = next(
            candidate
            for candidate in first["candidates"]
            if candidate["franchise"]["brand_id"] == "kr-ediya-coffee"
        )
        assert selected_candidate["financial_summary"]["variable_cost_rate_bps"] == 0
        assert (
            selected_candidate["financial_summary"]["effective_contribution_margin_bps"]
            == 6_500
        )
        selection_response = client.post(
            f"/v1/projects/{project_id}/candidate-selections",
            headers={**headers, "Idempotency-Key": "royalty-select"},
            json={
                "result_bundle_id": first["result_bundle_id"],
                "candidate_id": selected_candidate["candidate_id"],
                "expected_head": first["current_head"],
            },
        )
        assert selection_response.status_code == 201

        occurred_at = datetime(2026, 8, 25, 5, 0, tzinfo=UTC)
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO project_events(
                        event_id, project_id, event_type, event_json, occurred_at
                    ) VALUES (
                        'royalty-event-1', :project_id, 'DOCUMENT_CLAIMS_APPLIED',
                        CAST('{}' AS JSONB), :at
                    )
                    """
                ),
                {"project_id": project_id, "at": occurred_at},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO documents(
                        document_id, project_id, owner_user_id, active_case_id, document_type,
                        status, current_revision_number, created_at, updated_at
                    ) VALUES (
                        'royalty-document-1', :project_id, 'user-2', :case_id,
                        'FRANCHISE_AGREEMENT', 'ACTIVE', 1, :at, :at
                    )
                    """
                ),
                {
                    "project_id": project_id,
                    "case_id": selected_candidate["candidate_id"],
                    "at": occurred_at,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO document_revisions(
                        document_revision_id, document_id, project_id, revision_number,
                        object_path, original_filename, declared_content_type,
                        declared_size_bytes, declared_sha256, status, idempotency_key,
                        request_digest, created_at, updated_at
                    ) VALUES (
                        'royalty-revision-1', 'royalty-document-1', :project_id, 1,
                        :object_path, 'franchise-agreement.pdf', 'application/pdf', 100, :sha256,
                        'EXTRACTION_READY', 'royalty-upload', :request_digest, :at, :at
                    )
                    """
                ),
                {
                    "project_id": project_id,
                    "object_path": f"projects/{project_id}/documents/royalty-document-1/source.pdf",
                    "sha256": "d" * 64,
                    "request_digest": b"royalty-upload-digest",
                    "at": occurred_at,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO venture_claims(
                        claim_id, project_id, case_id, case_type, source_id, claim_type,
                        value_json, unit, materiality, status, document_id,
                        document_revision_id, anchor_json, event_id, created_at
                    ) VALUES (
                        'royalty-claim-1', :project_id, :case_id, 'FRANCHISE',
                        'kr-ediya-coffee', 'ROYALTY', CAST('3' AS JSONB), '%', 'HIGH',
                        'CONFIRMED', 'royalty-document-1', 'royalty-revision-1',
                        CAST(:anchor AS JSONB), 'royalty-event-1', :at
                    )
                    """
                ),
                {
                    "project_id": project_id,
                    "case_id": selected_candidate["candidate_id"],
                    "anchor": '{"document_revision_id":"royalty-revision-1","page_index":4}',
                    "at": occurred_at,
                },
            )

        with postgres_engine.begin() as connection:
            current_state_json = connection.execute(
                text(
                    """
                    SELECT state.state_json
                    FROM venture_projects project
                    JOIN venture_states state
                      ON state.project_id=project.project_id
                     AND state.state_version=project.current_state_version
                    WHERE project.project_id=:project_id
                    """
                ),
                {"project_id": project_id},
            ).scalar_one()
            current_head_row = connection.execute(
                text("SELECT * FROM project_heads WHERE project_id=:project_id"),
                {"project_id": project_id},
            ).mappings().one()
            current_state = VentureState.model_validate(current_state_json)
            previous_head = HeadFence(
                workflow_generation=current_head_row["workflow_generation"],
                state_version=current_head_row["state_version"],
                founder_snapshot_id=current_head_row["founder_snapshot_id"],
                area_snapshot_id=current_head_row["area_snapshot_id"],
                evidence_snapshot_id=current_head_row["evidence_snapshot_id"],
                policy_snapshot_id=current_head_row["policy_snapshot_id"],
                index_generation_id=current_head_row["index_generation_id"],
                seed_registry_id=current_head_row["seed_registry_id"],
            )
            start_selective_first_proposal(
                connection,
                project_id=project_id,
                user_id="user-2",
                state=current_state,
                source_workflow_run_id=first["workflow_run_id"],
                previous_head=previous_head,
                now=datetime(2026, 8, 25, 5, 5, tzinfo=UTC),
                new_id=lambda: str(uuid4()),
            )
        current = client.get(f"/v1/projects/{project_id}/result", headers=headers).json()

    recalculated = next(
        candidate
        for candidate in current["candidates"]
        if candidate["franchise"]["brand_id"] == "kr-ediya-coffee"
    )
    assert (
        recalculated["financial_summary"]["monthly_fixed_cost"]["base"]
        == selected_candidate["financial_summary"]["monthly_fixed_cost"]["base"]
    )
    assert recalculated["financial_summary"]["variable_cost_rate_bps"] == 300
    assert recalculated["financial_summary"]["effective_contribution_margin_bps"] == 6_200
    assert (
        recalculated["financial_summary"]["break_even_monthly_sales_krw"]
        > selected_candidate["financial_summary"]["break_even_monthly_sales_krw"]
    )
    royalty = next(
        item for item in recalculated["decision_inputs"] if item["field"] == "SALES_ROYALTY"
    )
    assert royalty["value_bps"] == 300
    assert royalty["provenance"] == "USER_INPUT"
    assert royalty["source_title"] == "franchise-agreement.pdf"
    assert royalty["source_anchor"].startswith("royalty-revision-1#page=5")
    delta_change = next(
        item
        for item in current["decision_delta"]["candidate_changes"]
        if item["candidate_key"] == "FRANCHISE:kr-ediya-coffee"
    )
    royalty_delta = next(
        item for item in delta_change["input_changes"] if item["field"] == "SALES_ROYALTY"
    )
    assert royalty_delta["previous"] is None
    assert royalty_delta["current"]["value_bps"] == 300
    assert royalty_delta["current"]["provenance"] == "USER_INPUT"


def test_confirmed_interior_quote_claim_replaces_seed_cost_on_recompute(
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
            identity_verifier=_Identity(),
            workflow_service=_workflow_service(postgres_engine),
        )
    ) as client:
        project_id = client.post(
            "/v1/projects",
            headers={**headers, "Idempotency-Key": "claim-create"},
            json={},
        ).json()["project_id"]
        client.post(
            f"/v1/projects/{project_id}/onboarding/confirm",
            headers={**headers, "Idempotency-Key": "claim-onboard"},
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
            headers={**headers, "Idempotency-Key": "claim-analysis"},
            json={},
        )
        first = client.get(f"/v1/projects/{project_id}/result", headers=headers).json()
        selected_candidate = next(
            candidate
            for candidate in first["candidates"]
            if candidate["independent_model"]["model_id"]
            == "independent-small-takeout-v1"
        )
        selection_response = client.post(
            f"/v1/projects/{project_id}/candidate-selections",
            headers={**headers, "Idempotency-Key": "claim-select"},
            json={
                "result_bundle_id": first["result_bundle_id"],
                "candidate_id": selected_candidate["candidate_id"],
                "expected_head": first["current_head"],
            },
        )
        assert selection_response.status_code == 201

        occurred_at = datetime(2026, 8, 25, 4, 0, tzinfo=UTC)
        with postgres_engine.begin() as connection:
            event_id = "claim-event-interior-1"
            document_id = "claim-document-interior-1"
            revision_id = "claim-revision-interior-1"
            claim_id = "claim-interior-total-1"
            connection.execute(
                text(
                    """
                    INSERT INTO project_events(
                    event_id, project_id, event_type, event_json, occurred_at
                )
                    VALUES (
                    :event_id, :project_id, 'DOCUMENT_CLAIMS_APPLIED',
                    CAST('{}' AS JSONB), :at
                )
                    """
                ),
                {"event_id": event_id, "project_id": project_id, "at": occurred_at},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO documents(
                        document_id, project_id, owner_user_id, active_case_id, document_type,
                        status, current_revision_number, created_at, updated_at
                    ) VALUES (
                        :document_id, :project_id, 'user-2', :case_id, 'INTERIOR_QUOTE',
                        'ACTIVE', 1, :at, :at
                    )
                    """
                ),
                {
                    "document_id": document_id,
                    "project_id": project_id,
                    "case_id": selected_candidate["candidate_id"],
                    "at": occurred_at,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO document_revisions(
                        document_revision_id, document_id, project_id, revision_number,
                        object_path, original_filename, declared_content_type,
                        declared_size_bytes, declared_sha256, status, idempotency_key,
                        request_digest, created_at, updated_at
                    ) VALUES (
                        :revision_id, :document_id, :project_id, 1,
                        :object_path, 'interior.pdf', 'application/pdf', 100, :sha256,
                        'EXTRACTION_READY', :idempotency_key, :request_digest, :at, :at
                    )
                    """
                ),
                {
                    "revision_id": revision_id,
                    "document_id": document_id,
                    "project_id": project_id,
                    "object_path": f"projects/{project_id}/documents/{document_id}/source.pdf",
                    "sha256": "a" * 64,
                    "idempotency_key": "claim-revision-upload",
                    "request_digest": b"claim-revision-digest",
                    "at": occurred_at,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO venture_claims(
                        claim_id, project_id, case_id, case_type, source_id, claim_type,
                        value_json, unit, materiality, status, document_id,
                        document_revision_id, anchor_json, event_id, created_at
                    ) VALUES (
                        :claim_id, :project_id, :case_id, 'INDEPENDENT',
                        'independent-small-takeout-v1', 'QUOTE_TOTAL',
                        CAST('26400000' AS JSONB), 'KRW', 'HIGH', 'CONFIRMED',
                        :document_id, :revision_id,
                        CAST(:anchor AS JSONB), :event_id, :at
                    )
                    """
                ),
                {
                    "claim_id": claim_id,
                    "project_id": project_id,
                    "case_id": selected_candidate["candidate_id"],
                    "document_id": document_id,
                    "revision_id": revision_id,
                    "anchor": '{"document_revision_id":"claim-revision-interior-1","page_index":0}',
                    "event_id": event_id,
                    "at": occurred_at,
                },
            )

        with postgres_engine.begin() as connection:
            current_state_json = connection.execute(
                text(
                    """
                    SELECT state.state_json
                    FROM venture_projects project
                    JOIN venture_states state
                      ON state.project_id=project.project_id
                     AND state.state_version=project.current_state_version
                    WHERE project.project_id=:project_id
                    """
                ),
                {"project_id": project_id},
            ).scalar_one()
            current_head_row = connection.execute(
                text("SELECT * FROM project_heads WHERE project_id=:project_id"),
                {"project_id": project_id},
            ).mappings().one()
            current_state = VentureState.model_validate(current_state_json)
            previous_head = HeadFence(
                workflow_generation=current_head_row["workflow_generation"],
                state_version=current_head_row["state_version"],
                founder_snapshot_id=current_head_row["founder_snapshot_id"],
                area_snapshot_id=current_head_row["area_snapshot_id"],
                evidence_snapshot_id=current_head_row["evidence_snapshot_id"],
                policy_snapshot_id=current_head_row["policy_snapshot_id"],
                index_generation_id=current_head_row["index_generation_id"],
                seed_registry_id=current_head_row["seed_registry_id"],
            )
            start_selective_first_proposal(
                connection,
                project_id=project_id,
                user_id="user-2",
                state=current_state,
                source_workflow_run_id=first["workflow_run_id"],
                previous_head=previous_head,
                now=datetime(2026, 8, 25, 4, 5, tzinfo=UTC),
                new_id=lambda: str(uuid4()),
            )
        current = client.get(f"/v1/projects/{project_id}/result", headers=headers).json()

    recalculated = next(
        candidate
        for candidate in current["candidates"]
        if candidate["independent_model"]["model_id"] == "independent-small-takeout-v1"
    )
    construction = next(
        item for item in recalculated["decision_inputs"] if item["field"] == "CONSTRUCTION"
    )
    assert selected_candidate["financial_summary"]["initial_cash"]["base"] == 139_500_000
    assert recalculated["financial_summary"]["initial_cash"]["base"] == 133_900_000
    assert construction["value_range_krw"]["base"] == 26_400_000
    assert construction["provenance"] == "USER_INPUT"
    assert construction["source_title"] == "interior.pdf"
    assert construction["source_anchor"].startswith("claim-revision-interior-1#page=1")
    delta_change = next(
        item
        for item in current["decision_delta"]["candidate_changes"]
        if item["candidate_key"] == "INDEPENDENT:independent-small-takeout-v1"
    )
    construction_delta = next(
        item for item in delta_change["input_changes"] if item["field"] == "CONSTRUCTION"
    )
    assert construction_delta["previous"]["provenance"] == "ASSUMPTION"
    assert construction_delta["current"]["provenance"] == "USER_INPUT"
    assert construction_delta["affected_calculations"] == [
        "CAPITAL_GATE",
        "INITIAL_CASH",
        "RANK",
    ]
    assert delta_change["gate_transitions"] == []



def test_quote_total_conflicts_are_scoped_by_document_type(
    postgres_engine: Engine,
) -> None:
    projects = ProjectService(PostgresProjectRepository(postgres_engine))
    project = projects.create_project(
        user_id="conflict-user",
        idempotency_key="conflict-project",
    )
    case_id = "conflict-case"
    occurred_at = datetime(2026, 8, 25, 4, 30, tzinfo=UTC)
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO project_events(
                    event_id, project_id, event_type, event_json, occurred_at
                )
                VALUES ('conflict-event', :project_id, 'DOCUMENT_CLAIMS_APPLIED',
                        CAST('{}' AS JSONB), :at)
                """
            ),
            {"project_id": project.project_id, "at": occurred_at},
        )
        connection.execute(
            text(
                """
                INSERT INTO documents(
                    document_id, project_id, owner_user_id, active_case_id, document_type,
                    status, current_revision_number, created_at, updated_at
                ) VALUES (
                    'conflict-interior-document', :project_id, 'conflict-user', :case_id,
                    'INTERIOR_QUOTE', 'ACTIVE', 1, :at, :at
                )
                """
            ),
            {"project_id": project.project_id, "case_id": case_id, "at": occurred_at},
        )
        connection.execute(
            text(
                """
                INSERT INTO document_revisions(
                    document_revision_id, document_id, project_id, revision_number,
                    object_path, original_filename, declared_content_type,
                    declared_size_bytes, declared_sha256, status, idempotency_key,
                    request_digest, created_at, updated_at
                ) VALUES (
                    'conflict-interior-r1', 'conflict-interior-document', :project_id, 1,
                    :object_path, 'interior-old.pdf', 'application/pdf', 100, :sha256,
                    'EXTRACTION_READY', 'conflict-upload', :request_digest, :at, :at
                )
                """
            ),
            {
                "project_id": project.project_id,
                "object_path": (
                    f"projects/{project.project_id}/documents/"
                    "conflict-interior/source.pdf"
                ),
                "sha256": "b" * 64,
                "request_digest": b"conflict-upload-digest",
                "at": occurred_at,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO venture_claims(
                    claim_id, project_id, case_id, case_type, source_id, claim_type,
                    value_json, unit, materiality, status, document_id,
                    document_revision_id, anchor_json, event_id, created_at
                ) VALUES (
                    'conflict-interior-old', :project_id, :case_id, 'INDEPENDENT',
                    'independent-small-takeout-v1', 'QUOTE_TOTAL', CAST('20000000' AS JSONB),
                    'KRW', 'HIGH', 'CONFIRMED', 'conflict-interior-document',
                    'conflict-interior-r1', NULL, 'conflict-event', :at
                )
                """
            ),
            {"project_id": project.project_id, "case_id": case_id, "at": occurred_at},
        )

    service = DocumentExtractionService(postgres_engine, _UnusedDocumentRuntime())
    incoming = AppliedDocumentClaim(
        claim_id="incoming-quote",
        claim_type="QUOTE_TOTAL",
        value=26_400_000,
        unit="KRW",
        materiality="HIGH",
        document_revision_id="incoming-r1",
        anchor=None,
    )
    with postgres_engine.connect() as connection:
        equipment = service._find_conflicts(
            connection,
            project_id=project.project_id,
            case_id=case_id,
            document_type="EQUIPMENT_QUOTE",
            claims=[incoming],
            occurred_at=occurred_at,
        )
        interior = service._find_conflicts(
            connection,
            project_id=project.project_id,
            case_id=case_id,
            document_type="INTERIOR_QUOTE",
            claims=[incoming],
            occurred_at=occurred_at,
        )

    assert equipment == []
    assert len(interior) == 1
    assert interior[0].claim_type == "QUOTE_TOTAL"
    assert interior[0].competing_claim_ids == ["conflict-interior-old", "incoming-quote"]
