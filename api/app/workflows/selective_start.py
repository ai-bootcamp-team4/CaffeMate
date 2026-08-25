"""사용자 변경은 현재 State와 저장된 실제 조건으로 결과를 한 번 다시 계산한다."""

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.candidates.seed_registry import IndependentSeedRegistry
from app.domain.errors import FeedbackPreconditionError
from app.domain.models import VentureState
from app.finance.case_facts import PropertyContext
from app.finance.labor_benchmark import replay_minimum_wage_references
from app.finance.property_benchmark import replay_property_rent_benchmarks
from app.workflows.models import HeadFence, WorkflowRun
from app.workflows.persistence import persist_completed_first_proposal
from app.workflows.simple_proposal import SimpleProposalBuilder


def start_selective_first_proposal(
    connection: Connection,
    *,
    project_id: str,
    user_id: str,
    state: VentureState,
    source_workflow_run_id: str,
    previous_head: HeadFence,
    now: datetime,
    new_id: Callable[[], str],
    property_context: PropertyContext | None = None,
) -> WorkflowRun:
    """현재 State를 다시 계산하되 사용자가 확정한 점포 조건은 계속 보존한다."""

    source_result = connection.execute(
        text(
            "SELECT result_bundle_id, bundle_json FROM result_bundles "
            "WHERE workflow_run_id=:workflow_run_id AND project_id=:project_id"
        ),
        {
            "workflow_run_id": source_workflow_run_id,
            "project_id": project_id,
        },
    ).mappings().one_or_none()
    if source_result is None:
        raise FeedbackPreconditionError("Recompute requires a committed source result")
    source_bundle = source_result["bundle_json"]
    if isinstance(source_bundle, str):
        source_bundle = json.loads(source_bundle)
    if not isinstance(source_bundle, dict):
        raise FeedbackPreconditionError("Recompute source result is invalid")
    franchise_universe = _franchise_universe(source_bundle)
    property_rent_benchmarks = replay_property_rent_benchmarks(source_bundle)
    minimum_wage_references = replay_minimum_wage_references(source_bundle)

    registry = IndependentSeedRegistry.load_default()
    return persist_completed_first_proposal(
        connection,
        project_id=project_id,
        user_id=user_id,
        state=state,
        policy_snapshot_id=previous_head.policy_snapshot_id,
        seed_registry_id=registry.registry_id,
        builder=SimpleProposalBuilder(registry),
        now=now,
        new_id=new_id,
        source_workflow_run_id=source_workflow_run_id,
        source_result_bundle_id=str(source_result["result_bundle_id"]),
        property_context=property_context,
        property_rent_benchmarks=property_rent_benchmarks,
        minimum_wage_references=minimum_wage_references,
        franchise_universe=franchise_universe,
    )


def _franchise_universe(source_bundle: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = source_bundle.get("candidates")
    if not isinstance(candidates, list):
        return []
    universe: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        franchise = candidate.get("franchise")
        if not isinstance(franchise, dict) or franchise.get("eligibility") != "VERIFIED":
            continue
        brand_id = franchise.get("brand_id")
        profile = franchise.get("finance_profile")
        if not isinstance(brand_id, str) or not isinstance(profile, dict):
            continue
        universe.append(
            {
                "brand_id": brand_id,
                "display_name": candidate.get("display_name", brand_id),
                "individual_franchise_eligibility": "VERIFIED",
                "evidence_refs": franchise.get("eligibility_evidence_refs", []),
                "finance_profile": profile,
            }
        )
    return universe
