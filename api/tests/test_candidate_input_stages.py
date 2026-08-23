from datetime import UTC, datetime, timedelta

import pytest

from app.candidates.seed_registry import (
    AllowedParameter,
    IndependentSeedDefinition,
    IndependentSeedRegistry,
    IndependentSeedRegistryDocument,
)
from app.domain.errors import ContractValidationError
from app.domain.models import (
    AreaResolutionStatus,
    AreaState,
    BorrowingIntent,
    CafeTypePreference,
    CoverageProfile,
    FounderState,
    OperationMode,
    VentureState,
    VentureStatus,
)
from app.workflows.candidate_inputs import (
    FranchiseEligibilityStageHandler,
    IndependentSeedStageHandler,
)
from app.workflows.models import HeadFence, StageLease
from app.workflows.stage_context import StageContext
from tests.test_agent_boundary import evidence_record


def registry(*, minimum_funds: int | None = None) -> IndependentSeedRegistry:
    return IndependentSeedRegistry(
        IndependentSeedRegistryDocument(
            schema_version="1.0.0",
            models=[
                IndependentSeedDefinition(
                    model_id="owner-model-v1",
                    display_name="직접 운영 모델",
                    allowed_operation_modes=[OperationMode.DIRECT_FULL_TIME],
                    minimum_own_funds_krw=minimum_funds,
                    allowed_parameters=[
                        AllowedParameter(
                            field_path="operations.seats",
                            value_kind="INTEGER",
                            unit="seat",
                        )
                    ],
                    support_refs=["seed-assumption:owner-model-v1"],
                )
            ],
        )
    )


def candidate_context(
    *,
    seed_registry_id: str,
    own_funds_krw: int = 50_000_000,
) -> StageContext:
    now = datetime(2026, 8, 21, tzinfo=UTC)
    record = evidence_record("ev-franchise-verified")
    record["claim_type"] = "FRANCHISE_UNIVERSE_ELIGIBILITY"
    return StageContext(
        lease=StageLease(
            workflow_run_id="workflow-1",
            stage_run_id="candidate-input-1",
            stage_code="INDEPENDENT_SEED",
            input_digest="a" * 64,
            lease_token="lease-token",
            lease_expires_at=now + timedelta(seconds=45),
            attempt=1,
            head=HeadFence(
                workflow_generation=1,
                state_version=1,
                founder_snapshot_id="founder-1",
                area_snapshot_id="area-1",
                evidence_snapshot_id="evidence-1",
                policy_snapshot_id="policy-1",
                index_generation_id="index-1",
                seed_registry_id=seed_registry_id,
            ),
        ),
        project_id="project-1",
        state=VentureState(
            project_id="project-1",
            user_id="user-1",
            state_version=1,
            status=VentureStatus.ANALYZING,
            founder=FounderState(
                target_area_input="수원 아주대 부근",
                own_funds_krw=own_funds_krw,
                borrowing_intent=BorrowingIntent.UNDECIDED,
                cafe_type_preference=CafeTypePreference.OPEN_TO_BOTH,
                operation_mode=OperationMode.DIRECT_FULL_TIME,
            ),
            area=AreaState(
                resolution_status=AreaResolutionStatus.UNRESOLVED,
                coverage_profile=CoverageProfile.N0_NATIONWIDE_FACTS,
            ),
            updated_at=now,
        ),
        dependency_results={
            "AREA_RESOLUTION": {
                "area_resolution": {
                    "resolution_status": "RESOLVED",
                    "selected": {
                        "administrative_code": "4111756000",
                        "display_name": "경기도 수원시 영통구 원천동",
                        "boundary_version": "2026-01",
                    },
                    "evidence_records": [],
                    "missing_fields": ["sales"],
                }
            },
            "EVIDENCE_FREEZE": {
                "evidence_freeze": {
                    "snapshot_id": "evidence-1",
                    "evidence_records": [record],
                    "missing_claim_ids": ["claim:MORE_EVIDENCE"],
                    "franchise_universe": [],
                }
            },
        },
    )


def test_independent_seed_is_filtered_and_pinned_before_proposal() -> None:
    selected_registry = registry(minimum_funds=30_000_000)
    context = candidate_context(seed_registry_id=selected_registry.registry_id)

    output = IndependentSeedStageHandler(selected_registry).execute(context)["independent_seed"]

    assert isinstance(output, dict)
    assert output["seed_registry_id"] == selected_registry.registry_id
    assert output["evidence_snapshot_id"] == "evidence-1"
    proposal_input = output["proposal_input"]
    assert isinstance(proposal_input, dict)
    assert proposal_input["requested_candidate_count"] == 1
    assert proposal_input["area"]["administrative_code"] == "4111756000"
    assert proposal_input["model_seeds"][0]["model_id"] == "owner-model-v1"
    assert proposal_input["model_seeds"][0]["proposal_id"].startswith("proposal-independent-")


def test_default_registry_exposes_three_calculable_operating_models() -> None:
    selected_registry = IndependentSeedRegistry.load_default()
    context = candidate_context(seed_registry_id=selected_registry.registry_id)

    output = IndependentSeedStageHandler(selected_registry).execute(context)["independent_seed"]
    models = output["proposal_input"]["model_seeds"]

    assert [value["display_name"] for value in models] == [
        "소형 포장 중심 개인카페",
        "중소형 균형 개인카페",
        "좌석 중심 개인카페",
    ]
    assert output["proposal_input"]["requested_candidate_count"] == 3
    assert selected_registry.get("independent-small-takeout-v1").finance_profile is not None


def test_independent_seed_does_not_guess_affordability_when_threshold_fails() -> None:
    selected_registry = registry(minimum_funds=80_000_000)
    context = candidate_context(
        seed_registry_id=selected_registry.registry_id,
        own_funds_krw=50_000_000,
    )

    result = IndependentSeedStageHandler(selected_registry).execute(context)

    assert result["stage_control"] == {
        "disposition": "CONTINUE",
        "reason_codes": ["NO_ELIGIBLE_INDEPENDENT_SEED"],
    }
    output = result["independent_seed"]
    assert isinstance(output, dict)
    assert output["proposal_input"]["model_seeds"] == []


def test_independent_seed_rejects_unpinned_registry() -> None:
    selected_registry = registry()
    context = candidate_context(seed_registry_id="different-registry")

    with pytest.raises(ContractValidationError, match="not pinned"):
        IndependentSeedStageHandler(selected_registry).execute(context)


def test_franchise_eligibility_admits_only_verified_supported_brands() -> None:
    context = candidate_context(seed_registry_id="seed-v1")
    freeze = context.dependency_results["EVIDENCE_FREEZE"]["evidence_freeze"]
    freeze["franchise_universe"] = [
        {
            "brand_id": "verified-brand",
            "display_name": "검증 브랜드",
            "individual_franchise_eligibility": "VERIFIED",
            "eligibility_evidence_id": "ev-franchise-verified",
            "disclosure_status": "MISSING",
        },
        {
            "brand_id": "unsupported-brand",
            "display_name": "근거 미수용 브랜드",
            "individual_franchise_eligibility": "VERIFIED",
            "eligibility_evidence_id": "ev-not-accepted",
            "disclosure_status": "AVAILABLE",
        },
        {
            "brand_id": "unknown-brand",
            "display_name": "미확인 브랜드",
            "individual_franchise_eligibility": "UNVERIFIED",
            "eligibility_evidence_id": None,
            "disclosure_status": "STALE",
        },
        {
            "brand_id": "direct-only-brand",
            "display_name": "직영 브랜드",
            "individual_franchise_eligibility": "INELIGIBLE",
            "eligibility_evidence_id": "ev-franchise-verified",
            "disclosure_status": "AVAILABLE",
        },
    ]

    output = FranchiseEligibilityStageHandler().execute(context)["franchise_eligibility"]

    assert isinstance(output, dict)
    proposal_input = output["proposal_input"]
    assert isinstance(proposal_input, dict)
    assert [value["brand_id"] for value in proposal_input["franchise_universe"]] == [
        "verified-brand"
    ]
    admitted = proposal_input["franchise_universe"][0]
    assert admitted["evidence_refs"] == ["ev-franchise-verified"]
    assert admitted["missing_fields"] == [
        "area_availability_hq_confirmation",
        "franchise_disclosure",
    ]
    assert {value["reason_code"] for value in output["excluded_brands"]} == {
        "FRANCHISE_ELIGIBILITY_EVIDENCE_NOT_ACCEPTED",
        "FRANCHISE_ELIGIBILITY_UNVERIFIED",
        "FRANCHISE_INELIGIBLE",
    }


def test_franchise_eligibility_excludes_only_the_conflicting_brand() -> None:
    context = candidate_context(seed_registry_id="seed-v1")
    freeze = context.dependency_results["EVIDENCE_FREEZE"]["evidence_freeze"]
    freeze["franchise_universe"] = [
        {
            "brand_id": "brand-1",
            "display_name": "브랜드",
            "individual_franchise_eligibility": "VERIFIED",
            "eligibility_evidence_id": "ev-franchise-verified",
            "disclosure_status": "AVAILABLE",
        },
        {
            "brand_id": "brand-1",
            "display_name": "브랜드",
            "individual_franchise_eligibility": "UNVERIFIED",
            "eligibility_evidence_id": None,
            "disclosure_status": "AVAILABLE",
        },
    ]

    output = FranchiseEligibilityStageHandler().execute(context)["franchise_eligibility"]

    assert isinstance(output, dict)
    assert output["proposal_input"]["franchise_universe"] == []
    assert output["excluded_brands"] == [
        {
            "brand_id": "brand-1",
            "reason_code": "FRANCHISE_ELIGIBILITY_CONFLICT",
        }
    ]
