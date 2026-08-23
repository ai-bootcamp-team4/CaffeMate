import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import rfc8785

from app.contracts.schema_registry import (
    ContractRegistry,
    EvidencePlanContractValidator,
)
from app.domain.errors import ContractValidationError
from app.workflows.models import StageControl
from app.workflows.stage_context import StageContext

PLANNER_VERSION = "deterministic-evidence-plan.v1"
_FRESHNESS_PATTERN = re.compile(r"^P([0-9]+)D$")


@dataclass(frozen=True)
class ActionSpec:
    tool_name: str
    typed_arguments: dict[str, Any]
    required_authority: tuple[str, ...]


@dataclass(frozen=True)
class ClaimRule:
    route: str
    builder: Callable[[dict[str, Any], date], tuple[ActionSpec, ActionSpec]]
    stop_condition: str
    abstain_condition: str


class DeterministicEvidencePlanner:
    def __init__(self, *, contracts: EvidencePlanContractValidator | None = None) -> None:
        self._contracts = contracts or ContractRegistry()
        self._rules: dict[str, ClaimRule] = {
            "AREA_PROFILE": self._structured_rule(self._area_profile),
            "AREA_CAFE_COMPETITION": self._structured_rule(self._area_competition),
            "AREA_BUSINESS_CHURN": self._structured_rule(self._area_business_churn),
            "AREA_DEMAND_SIGNALS": self._structured_rule(self._area_demand_signals),
            "INDEPENDENT_STARTUP_COST_BENCHMARK": self._rag_rule(
                self._independent_startup_cost
            ),
            "INDEPENDENT_OPERATING_COST_BENCHMARK": self._rag_rule(
                self._independent_operating_cost
            ),
            "FRANCHISE_UNIVERSE_ELIGIBILITY": self._structured_rule(
                self._franchise_universe
            ),
            "FRANCHISE_DISCLOSURE_AVAILABILITY": self._rag_rule(
                self._franchise_disclosure
            ),
            "CAFE_OPENING_REQUIRED_PROCEDURES": self._rag_rule(
                self._opening_procedures
            ),
            "CAFE_CONTRACT_REQUIRED_CHECKS": self._rag_rule(
                self._contract_required_checks
            ),
        }

    def plan(self, claim_plan: dict[str, Any]) -> dict[str, Any]:
        claims = claim_plan.get("claims")
        constraints = claim_plan.get("planning_constraints")
        action_id_pool = claim_plan.get("action_id_pool")
        if not isinstance(claims, list) or not claims:
            raise ContractValidationError("Deterministic Evidence Plan requires claims")
        if not isinstance(constraints, dict):
            raise ContractValidationError(
                "Deterministic Evidence Plan requires planning constraints"
            )
        if not isinstance(action_id_pool, list) or any(
            not isinstance(value, str) or not value for value in action_id_pool
        ):
            raise ContractValidationError(
                "Deterministic Evidence Plan requires a valid action id pool"
            )
        if len(action_id_pool) != len(set(action_id_pool)):
            raise ContractValidationError("Evidence Plan action id pool is duplicated")

        as_of = self._as_of(constraints)
        allowed_tools = self._allowed_tools(constraints)
        max_per_claim = constraints.get("max_actions_per_claim")
        max_total = constraints.get("max_total_actions")
        if not isinstance(max_per_claim, int) or max_per_claim < 2:
            raise ContractValidationError(
                "Deterministic Evidence Plan requires two actions per claim"
            )
        if not isinstance(max_total, int) or max_total < 1:
            raise ContractValidationError(
                "Deterministic Evidence Plan requires a positive total action limit"
            )

        seen_claim_ids: set[str] = set()
        claim_plans: list[dict[str, Any]] = []
        missing_claim_ids: list[str] = []
        action_index = 0
        for claim in claims:
            if not isinstance(claim, dict):
                raise ContractValidationError("Evidence Claim is invalid")
            claim_id = claim.get("claim_id")
            claim_type = claim.get("claim_type")
            if not isinstance(claim_id, str) or not claim_id:
                raise ContractValidationError("Evidence Claim id is invalid")
            if claim_id in seen_claim_ids:
                raise ContractValidationError("Evidence Claim id is duplicated")
            seen_claim_ids.add(claim_id)
            if not isinstance(claim_type, str) or claim_type not in self._rules:
                raise ContractValidationError(
                    f"Unsupported deterministic Evidence Claim type: {claim_type}"
                )
            rule = self._rules[claim_type]
            support, counter = rule.builder(claim, as_of)
            self._contracts.validate_mcp_tool_input(
                support.tool_name, support.typed_arguments
            )
            self._contracts.validate_mcp_tool_input(
                counter.tool_name, counter.typed_arguments
            )
            if support.tool_name not in allowed_tools or counter.tool_name not in allowed_tools:
                missing_claim_ids.append(claim_id)
                claim_plans.append(
                    {
                        "claim_id": claim_id,
                        "route": rule.route,
                        "support_actions": [],
                        "counter_actions": [],
                        "stop_condition": (
                            "Stop without execution because the required production "
                            "connector is unavailable."
                        ),
                        "abstain_condition": (
                            "Preserve this Claim as missing until an authoritative "
                            "production connector is available."
                        ),
                    }
                )
                continue
            if action_index + 2 > max_total or action_index + 2 > len(action_id_pool):
                raise ContractValidationError(
                    "Evidence Plan supported action budget is exhausted"
                )
            support_action = self._action(
                action_id=action_id_pool[action_index],
                claim=claim,
                polarity="SUPPORT",
                spec=support,
                as_of=as_of,
                allowed_tools=allowed_tools,
            )
            action_index += 1
            counter_action = self._action(
                action_id=action_id_pool[action_index],
                claim=claim,
                polarity="COUNTER",
                spec=counter,
                as_of=as_of,
                allowed_tools=allowed_tools,
            )
            action_index += 1
            claim_plans.append(
                {
                    "claim_id": claim_id,
                    "route": rule.route,
                    "support_actions": [support_action],
                    "counter_actions": [counter_action],
                    "stop_condition": rule.stop_condition,
                    "abstain_condition": rule.abstain_condition,
                }
            )

        self._contracts.validate_evidence_plan_result({"claim_plans": claim_plans})
        plan_body: dict[str, Any] = {
            "status": "COMPLETE",
            "claims": claims,
            "planning_constraints": constraints,
            "claim_plans": claim_plans,
            "missing_claim_ids": missing_claim_ids,
            "reason_codes": (
                ["MCP_CAPABILITY_UNAVAILABLE"] if missing_claim_ids else []
            ),
            "warnings": [],
        }
        plan_digest = hashlib.sha256(
            rfc8785.dumps({"planner_version": PLANNER_VERSION, **plan_body})
        ).hexdigest()
        return {
            **plan_body,
            "planner_trace": {
                "planner_version": PLANNER_VERSION,
                "plan_digest": f"sha256:{plan_digest}",
            },
        }

    def _action(
        self,
        *,
        action_id: str,
        claim: dict[str, Any],
        polarity: str,
        spec: ActionSpec,
        as_of: date,
        allowed_tools: set[str],
    ) -> dict[str, Any]:
        if spec.tool_name not in allowed_tools:
            raise ContractValidationError(
                f"Deterministic Evidence tool is not allowed: {spec.tool_name}"
            )
        self._contracts.validate_mcp_tool_input(spec.tool_name, spec.typed_arguments)
        scope = claim.get("geographic_scope")
        if not isinstance(scope, dict):
            raise ContractValidationError("Evidence Claim geographic scope is invalid")
        return {
            "action_id": action_id,
            "claim_id": claim["claim_id"],
            "polarity": polarity,
            "tool_name": spec.tool_name,
            "tool_version": self._contracts.mcp_tool_version(spec.tool_name),
            "typed_arguments": spec.typed_arguments,
            "required_authority": list(spec.required_authority),
            "date_constraints": {
                "as_of": as_of.isoformat(),
                "max_age_days": self._freshness_days(claim.get("required_freshness")),
            },
            "scope_constraints": scope,
        }

    @staticmethod
    def _structured_rule(
        builder: Callable[[dict[str, Any], date], tuple[ActionSpec, ActionSpec]],
    ) -> ClaimRule:
        return ClaimRule(
            route="MCP_STRUCTURED",
            builder=builder,
            stop_condition="Stop after the bounded structured read completes.",
            abstain_condition=(
                "Abstain when no schema-valid result satisfies scope and freshness."
            ),
        )

    @staticmethod
    def _rag_rule(
        builder: Callable[[dict[str, Any], date], tuple[ActionSpec, ActionSpec]],
    ) -> ClaimRule:
        return ClaimRule(
            route="RAG_OFFICIAL",
            builder=builder,
            stop_condition="Stop after the bounded official support and counter searches complete.",
            abstain_condition=(
                "Abstain when no anchored official source satisfies scope and freshness."
            ),
        )

    @staticmethod
    def _as_of(constraints: dict[str, Any]) -> date:
        value = constraints.get("as_of")
        if not isinstance(value, str):
            raise ContractValidationError("Evidence Plan as_of is invalid")
        try:
            return date.fromisoformat(value)
        except ValueError as error:
            raise ContractValidationError("Evidence Plan as_of is invalid") from error

    @staticmethod
    def _allowed_tools(constraints: dict[str, Any]) -> set[str]:
        values = constraints.get("allowed_tools")
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ContractValidationError("Evidence Plan allowed tools are invalid")
        return set(values)

    @staticmethod
    def _freshness_days(value: Any) -> int | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ContractValidationError("Evidence Claim freshness is invalid")
        match = _FRESHNESS_PATTERN.fullmatch(value)
        if match is None:
            raise ContractValidationError("Evidence Claim freshness is invalid")
        return int(match.group(1))

    @staticmethod
    def _area_identity(claim: dict[str, Any]) -> tuple[str, str]:
        scope = claim.get("geographic_scope")
        if not isinstance(scope, dict) or scope.get("scope_type") != "ADMINISTRATIVE_AREA":
            raise ContractValidationError("Area Evidence Claim scope is invalid")
        code = scope.get("scope_id")
        boundary_version = scope.get("boundary_version")
        if not isinstance(code, str) or not isinstance(boundary_version, str):
            raise ContractValidationError("Area Evidence Claim identity is invalid")
        return code, boundary_version

    @classmethod
    def _area_profile(
        cls, claim: dict[str, Any], as_of: date
    ) -> tuple[ActionSpec, ActionSpec]:
        code, boundary = cls._area_identity(claim)
        action = ActionSpec(
            tool_name="get_area_profile",
            typed_arguments={
                "administrative_code": code,
                "boundary_version": boundary,
                "as_of": as_of.isoformat(),
            },
            required_authority=("PRIMARY_DATA",),
        )
        return action, action

    @classmethod
    def _area_competition(
        cls, claim: dict[str, Any], as_of: date
    ) -> tuple[ActionSpec, ActionSpec]:
        code, boundary = cls._area_identity(claim)
        action = ActionSpec(
            tool_name="search_cafe_observations",
            typed_arguments={
                "administrative_code": code,
                "boundary_version": boundary,
                "as_of": as_of.isoformat(),
                "metrics": ["CAFE_COUNT", "OPEN_COUNT"],
            },
            required_authority=("PRIMARY_DATA",),
        )
        return action, action

    @classmethod
    def _area_business_churn(
        cls, claim: dict[str, Any], as_of: date
    ) -> tuple[ActionSpec, ActionSpec]:
        code, boundary = cls._area_identity(claim)
        action = ActionSpec(
            tool_name="search_cafe_observations",
            typed_arguments={
                "administrative_code": code,
                "boundary_version": boundary,
                "as_of": as_of.isoformat(),
                "metrics": ["OPEN_COUNT", "CLOSE_COUNT", "CLOSURE_RATE"],
            },
            required_authority=("PRIMARY_DATA",),
        )
        return action, action

    @classmethod
    def _area_demand_signals(
        cls, claim: dict[str, Any], as_of: date
    ) -> tuple[ActionSpec, ActionSpec]:
        code, boundary = cls._area_identity(claim)
        action = ActionSpec(
            tool_name="search_cafe_observations",
            typed_arguments={
                "administrative_code": code,
                "boundary_version": boundary,
                "as_of": as_of.isoformat(),
                "metrics": [
                    "ESTIMATED_SALES",
                    "FOOT_TRAFFIC",
                    "RESIDENT_POPULATION",
                    "WORKER_POPULATION",
                    "AGE_DISTRIBUTION",
                    "CONSUMPTION",
                ],
            },
            required_authority=("PRIMARY_DATA",),
        )
        return action, action

    @staticmethod
    def _official_search(
        *, support_query: str, counter_query: str, as_of: date, source_families: list[str]
    ) -> tuple[ActionSpec, ActionSpec]:
        def action(query: str) -> ActionSpec:
            return ActionSpec(
                tool_name="retrieve_official_documents",
                typed_arguments={
                    "query": query,
                    "source_families": source_families,
                    "as_of": as_of.isoformat(),
                    "limit": 10,
                },
                required_authority=("PRIMARY_OFFICIAL", "VALIDATED_BENCHMARK"),
            )

        return action(support_query), action(counter_query)

    @classmethod
    def _independent_startup_cost(
        cls, claim: dict[str, Any], as_of: date
    ) -> tuple[ActionSpec, ActionSpec]:
        del claim
        return cls._official_search(
            support_query=(
                "개인 카페 창업 초기 비용 장비 인테리어 보증금 교육 신고 공식 기준"
            ),
            counter_query=(
                "개인 카페 창업 비용 누락 항목 부가세 철거 전기 증설 예비비 공식 자료"
            ),
            as_of=as_of,
            source_families=["GOVERNMENT_GUIDE", "PUBLIC_DATA"],
        )

    @classmethod
    def _independent_operating_cost(
        cls, claim: dict[str, Any], as_of: date
    ) -> tuple[ActionSpec, ActionSpec]:
        del claim
        return cls._official_search(
            support_query=(
                "개인 카페 운영비 임차료 인건비 원재료 공과금 결제 수수료 공식 기준"
            ),
            counter_query=(
                "개인 카페 운영비 누락 비용 폐기율 유지보수 보험 세금 공식 자료"
            ),
            as_of=as_of,
            source_families=["GOVERNMENT_GUIDE", "PUBLIC_DATA"],
        )

    @staticmethod
    def _franchise_universe(
        claim: dict[str, Any], as_of: date
    ) -> tuple[ActionSpec, ActionSpec]:
        del claim
        action = ActionSpec(
            tool_name="list_franchise_universe",
            typed_arguments={"business_category": "CAFE", "as_of": as_of.isoformat()},
            required_authority=("PRIMARY_OFFICIAL", "COMPANY_OFFICIAL"),
        )
        return action, action

    @classmethod
    def _opening_procedures(
        cls, claim: dict[str, Any], as_of: date
    ) -> tuple[ActionSpec, ActionSpec]:
        del claim
        return cls._official_search(
            support_query=(
                "카페 개업 사업자등록 휴게음식점 영업신고 위생교육 시설 기준 공식 절차"
            ),
            counter_query=(
                "카페 영업신고 지역별 추가 시설 소방 간판 제한 예외 공식 안내"
            ),
            as_of=as_of,
            source_families=["LAW", "GOVERNMENT_GUIDE", "OFFICIAL_PROCEDURE"],
        )

    @classmethod
    def _franchise_disclosure(
        cls, claim: dict[str, Any], as_of: date
    ) -> tuple[ActionSpec, ActionSpec]:
        del claim
        return cls._official_search(
            support_query=(
                "카페 프랜차이즈 정보공개서 가맹금 로열티 필수품목 계약기간 공식 자료"
            ),
            counter_query=(
                "카페 프랜차이즈 정보공개서 변경 등록 오래된 공개서 미확인 계약 조건 공식 자료"
            ),
            as_of=as_of,
            source_families=["FRANCHISE_DISCLOSURE"],
        )

    @classmethod
    def _contract_required_checks(
        cls, claim: dict[str, Any], as_of: date
    ) -> tuple[ActionSpec, ActionSpec]:
        del claim
        return cls._official_search(
            support_query=(
                "카페 창업 상가 임대차 계약 권리금 계약 가맹 계약 필수 확인 공식 기준"
            ),
            counter_query=(
                "상가 임대차 계약 해지 원상복구 갱신 권리금 회수 제한 불리한 조항 공식 자료"
            ),
            as_of=as_of,
            source_families=["LAW", "GOVERNMENT_GUIDE"],
        )


class EvidencePlanStageHandler:
    def __init__(
        self,
        *,
        planner: DeterministicEvidencePlanner | None = None,
    ) -> None:
        self._planner = planner or DeterministicEvidencePlanner()

    def execute(self, context: StageContext) -> dict[str, object]:
        dependency = context.dependency_results.get("CLAIM_PLAN")
        claim_plan = dependency.get("claim_plan") if dependency else None
        if not isinstance(claim_plan, dict):
            raise ContractValidationError("EVIDENCE_PLAN requires a Claim Plan")
        return {
            "stage_control": StageControl().model_dump(mode="json"),
            "evidence_plan": self._planner.plan(claim_plan),
        }
