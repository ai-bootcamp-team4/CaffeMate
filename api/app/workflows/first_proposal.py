import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import rfc8785

from app.domain.models import CafeTypePreference
from app.workflows.models import HeadFence


class FirstProposalStage(StrEnum):
    AREA_RESOLUTION = "AREA_RESOLUTION"
    CLAIM_PLAN = "CLAIM_PLAN"
    EVIDENCE_PLAN = "EVIDENCE_PLAN"
    EVIDENCE_RETRIEVAL = "EVIDENCE_RETRIEVAL"
    EVIDENCE_ASSESS = "EVIDENCE_ASSESS"
    EVIDENCE_FREEZE = "EVIDENCE_FREEZE"
    INDEPENDENT_SEED = "INDEPENDENT_SEED"
    FRANCHISE_ELIGIBILITY = "FRANCHISE_ELIGIBILITY"
    PROPOSE_INDEPENDENT = "PROPOSE_INDEPENDENT"
    PROPOSE_FRANCHISE = "PROPOSE_FRANCHISE"
    CALCULATE_GATE_RANK = "CALCULATE_GATE_RANK"
    CANDIDATE_AUDIT = "CANDIDATE_AUDIT"
    COMMIT_RESULT = "COMMIT_RESULT"


@dataclass(frozen=True)
class StagePlan:
    code: FirstProposalStage
    dependencies: tuple[FirstProposalStage, ...]


def compile_first_proposal_plan(preference: CafeTypePreference) -> tuple[StagePlan, ...]:
    stages = [
        StagePlan(FirstProposalStage.AREA_RESOLUTION, ()),
        StagePlan(FirstProposalStage.CLAIM_PLAN, (FirstProposalStage.AREA_RESOLUTION,)),
        StagePlan(FirstProposalStage.EVIDENCE_PLAN, (FirstProposalStage.CLAIM_PLAN,)),
        StagePlan(
            FirstProposalStage.EVIDENCE_RETRIEVAL,
            (FirstProposalStage.EVIDENCE_PLAN,),
        ),
        StagePlan(
            FirstProposalStage.EVIDENCE_ASSESS,
            (FirstProposalStage.EVIDENCE_RETRIEVAL,),
        ),
        StagePlan(
            FirstProposalStage.EVIDENCE_FREEZE,
            (FirstProposalStage.EVIDENCE_ASSESS,),
        ),
    ]
    proposal_dependencies: list[FirstProposalStage] = []
    if preference in {
        CafeTypePreference.OPEN_TO_BOTH,
        CafeTypePreference.INDEPENDENT_ONLY,
    }:
        stages.extend(
            [
                StagePlan(
                    FirstProposalStage.INDEPENDENT_SEED,
                    (
                        FirstProposalStage.AREA_RESOLUTION,
                        FirstProposalStage.EVIDENCE_FREEZE,
                    ),
                ),
                StagePlan(
                    FirstProposalStage.PROPOSE_INDEPENDENT,
                    (FirstProposalStage.INDEPENDENT_SEED,),
                ),
            ]
        )
        proposal_dependencies.append(FirstProposalStage.PROPOSE_INDEPENDENT)
    if preference in {
        CafeTypePreference.OPEN_TO_BOTH,
        CafeTypePreference.FRANCHISE_ONLY,
    }:
        stages.extend(
            [
                StagePlan(
                    FirstProposalStage.FRANCHISE_ELIGIBILITY,
                    (
                        FirstProposalStage.AREA_RESOLUTION,
                        FirstProposalStage.EVIDENCE_FREEZE,
                    ),
                ),
                StagePlan(
                    FirstProposalStage.PROPOSE_FRANCHISE,
                    (FirstProposalStage.FRANCHISE_ELIGIBILITY,),
                ),
            ]
        )
        proposal_dependencies.append(FirstProposalStage.PROPOSE_FRANCHISE)
    stages.extend(
        [
            StagePlan(
                FirstProposalStage.CALCULATE_GATE_RANK,
                tuple(proposal_dependencies),
            ),
            StagePlan(
                FirstProposalStage.CANDIDATE_AUDIT,
                (FirstProposalStage.CALCULATE_GATE_RANK,),
            ),
            StagePlan(
                FirstProposalStage.COMMIT_RESULT,
                (FirstProposalStage.CANDIDATE_AUDIT,),
            ),
        ]
    )
    return tuple(stages)


def stage_input_digest(
    *,
    workflow_run_id: str,
    stage_code: FirstProposalStage,
    head: HeadFence,
    dependencies: tuple[dict[str, object], ...] = (),
) -> str:
    # A stage dependency set has no semantic ordering. Canonicalize it here so
    # every producer and verifier derives the same digest regardless of SQL or plan order.
    canonical_dependencies = sorted(
        dependencies,
        key=lambda dependency: str(dependency["stage_code"]),
    )
    digest_payload: dict[str, Any] = {
        "workflow_run_id": workflow_run_id,
        "stage_code": stage_code.value,
        "head": head.model_dump(mode="json"),
        "dependencies": canonical_dependencies,
        "contract_version": "1.0.0",
    }
    return hashlib.sha256(rfc8785.dumps(digest_payload)).hexdigest()
