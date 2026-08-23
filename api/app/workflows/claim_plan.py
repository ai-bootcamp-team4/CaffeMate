from collections.abc import Callable
from datetime import UTC, date, datetime
from enum import StrEnum

from pydantic import Field

from app.domain.errors import ContractValidationError
from app.domain.models import CafeTypePreference, StrictModel
from app.workflows.stage_context import StageContext


class ClaimMateriality(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class GeographicScope(StrictModel):
    scope_type: str
    scope_id: str | None
    boundary_version: str | None


class EvidenceClaim(StrictModel):
    claim_id: str = Field(min_length=1)
    claim_type: str = Field(min_length=1)
    materiality: ClaimMateriality
    geographic_scope: GeographicScope
    required_freshness: str | None


class EvidencePlanningConstraints(StrictModel):
    as_of: date
    max_actions_per_claim: int = Field(ge=1, le=4)
    max_total_actions: int = Field(ge=1, le=20)
    allowed_tools: list[str]


class ClaimPlanOutput(StrictModel):
    claims: list[EvidenceClaim] = Field(min_length=1)
    planning_constraints: EvidencePlanningConstraints
    action_id_pool: list[str] = Field(min_length=1, max_length=20)


class ClaimPlanStageHandler:
    def __init__(self, *, today: Callable[[], date] | None = None) -> None:
        self._today = today or (lambda: datetime.now(UTC).date())

    def execute(self, context: StageContext) -> dict[str, object]:
        area = self._resolved_area(context)
        area_scope = GeographicScope(
            scope_type="ADMINISTRATIVE_AREA",
            scope_id=area["administrative_code"],
            boundary_version=area["boundary_version"],
        )
        national_scope = GeographicScope(
            scope_type="NATIONAL",
            scope_id="KR",
            boundary_version=None,
        )
        claims = [
            self._claim("AREA_PROFILE", ClaimMateriality.HIGH, area_scope, "P365D"),
            self._claim("AREA_CAFE_COMPETITION", ClaimMateriality.HIGH, area_scope, "P180D"),
            self._claim("AREA_BUSINESS_CHURN", ClaimMateriality.HIGH, area_scope, "P365D"),
            self._claim("AREA_DEMAND_SIGNALS", ClaimMateriality.HIGH, area_scope, "P180D"),
        ]
        # Keep claims complete, but expose only connectors that are wired in the
        # deployed FIRST_PROPOSAL retrieval path. Unsupported claims remain
        # explicit missing evidence instead of becoming guaranteed MCP errors.
        allowed_tools = {
            "get_area_profile",
            "list_franchise_universe",
            "retrieve_official_documents",
            "search_cafe_observations",
        }
        preference = context.state.founder.cafe_type_preference
        if preference in {
            CafeTypePreference.OPEN_TO_BOTH,
            CafeTypePreference.INDEPENDENT_ONLY,
        }:
            claims.extend(
                [
                    self._claim(
                        "INDEPENDENT_STARTUP_COST_BENCHMARK",
                        ClaimMateriality.HIGH,
                        national_scope,
                        "P730D",
                    ),
                    self._claim(
                        "INDEPENDENT_OPERATING_COST_BENCHMARK",
                        ClaimMateriality.HIGH,
                        national_scope,
                        "P730D",
                    ),
                ]
            )
        if preference in {
            CafeTypePreference.OPEN_TO_BOTH,
            CafeTypePreference.FRANCHISE_ONLY,
        }:
            claims.extend(
                [
                    self._claim(
                        "FRANCHISE_UNIVERSE_ELIGIBILITY",
                        ClaimMateriality.HIGH,
                        national_scope,
                        "P365D",
                    ),
                    self._claim(
                        "FRANCHISE_DISCLOSURE_AVAILABILITY",
                        ClaimMateriality.HIGH,
                        national_scope,
                        "P365D",
                    ),
                ]
            )
        claims.append(
            self._claim(
                "CAFE_OPENING_REQUIRED_PROCEDURES",
                ClaimMateriality.MEDIUM,
                national_scope,
                "P365D",
            )
        )
        output = ClaimPlanOutput(
            claims=claims,
            planning_constraints=EvidencePlanningConstraints(
                as_of=self._today(),
                max_actions_per_claim=2,
                max_total_actions=20,
                allowed_tools=sorted(allowed_tools),
            ),
            action_id_pool=[f"action-{index:02d}" for index in range(1, 21)],
        )
        return {"claim_plan": output.model_dump(mode="json")}

    @staticmethod
    def _resolved_area(context: StageContext) -> dict[str, str]:
        dependency = context.dependency_results.get("AREA_RESOLUTION")
        resolution = dependency.get("area_resolution") if dependency else None
        if not isinstance(resolution, dict) or resolution.get("resolution_status") != "RESOLVED":
            raise ContractValidationError("CLAIM_PLAN requires a resolved area")
        selected = resolution.get("selected")
        if not isinstance(selected, dict):
            raise ContractValidationError("CLAIM_PLAN requires a selected area")
        administrative_code = selected.get("administrative_code")
        boundary_version = selected.get("boundary_version")
        if not isinstance(administrative_code, str) or not isinstance(boundary_version, str):
            raise ContractValidationError("Selected area identity is invalid")
        return {
            "administrative_code": administrative_code,
            "boundary_version": boundary_version,
        }

    @staticmethod
    def _claim(
        claim_type: str,
        materiality: ClaimMateriality,
        scope: GeographicScope,
        freshness: str,
    ) -> EvidenceClaim:
        return EvidenceClaim(
            claim_id=f"claim:{claim_type}",
            claim_type=claim_type,
            materiality=materiality,
            geographic_scope=scope,
            required_freshness=freshness,
        )
