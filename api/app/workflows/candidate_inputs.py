import hashlib
from typing import Any

import rfc8785

from app.candidates.seed_registry import IndependentSeedRegistry
from app.domain.errors import ContractValidationError
from app.workflows.models import StageControl
from app.workflows.stage_context import StageContext


class CandidateInputProjection:
    @staticmethod
    def founder(context: StageContext) -> dict[str, Any]:
        value = context.state.founder
        return {
            "target_area_input": value.target_area_input,
            "own_funds_krw": value.own_funds_krw,
            "borrowing_intent": value.borrowing_intent.value,
            "cafe_type_preference": value.cafe_type_preference.value,
            "operation_mode": value.operation_mode.value,
            "preferences": value.preferences,
            "avoidances": value.avoidances,
            "max_loss_krw": value.max_loss_krw,
        }

    @staticmethod
    def area(context: StageContext) -> dict[str, Any]:
        dependency = context.dependency_results.get("AREA_RESOLUTION")
        resolution = dependency.get("area_resolution") if dependency else None
        if not isinstance(resolution, dict) or resolution.get("resolution_status") != "RESOLVED":
            raise ContractValidationError("Candidate input requires a resolved area")
        selected = resolution.get("selected")
        if not isinstance(selected, dict):
            raise ContractValidationError("Candidate input requires a selected area")
        for field in ("administrative_code", "display_name", "boundary_version"):
            if not isinstance(selected.get(field), str):
                raise ContractValidationError("Resolved area identity is invalid")
        resolution_evidence_ids: set[str] = set()
        for value in resolution.get("evidence_records", []):
            if not isinstance(value, dict):
                continue
            evidence_id = value.get("evidence_id")
            if isinstance(evidence_id, str):
                resolution_evidence_ids.add(evidence_id)
        evidence_ids = sorted(resolution_evidence_ids | set(context.state.area.evidence_ids))
        return {
            "resolution_status": "RESOLVED",
            "administrative_code": selected.get("administrative_code"),
            "display_name": selected.get("display_name"),
            "boundary_version": selected.get("boundary_version"),
            "coverage_profile": context.state.area.coverage_profile.value,
            "evidence_ids": evidence_ids,
            "unavailable_fields": sorted(
                set(context.state.area.unavailable_fields)
                | {
                    value
                    for value in resolution.get("missing_fields", [])
                    if isinstance(value, str)
                }
            ),
        }

    @staticmethod
    def freeze(context: StageContext) -> dict[str, Any]:
        dependency = context.dependency_results.get("EVIDENCE_FREEZE")
        value = dependency.get("evidence_freeze") if dependency else None
        if not isinstance(value, dict):
            raise ContractValidationError("Candidate input requires frozen Evidence")
        if value.get("snapshot_id") != context.lease.head.evidence_snapshot_id:
            raise ContractValidationError("Candidate input Evidence Snapshot is not pinned")
        records = value.get("evidence_records")
        if not isinstance(records, list):
            raise ContractValidationError("Frozen Evidence records are invalid")
        return value

    @staticmethod
    def proposal_id(context: StageContext, kind: str, source_id: str) -> str:
        digest = hashlib.sha256(
            rfc8785.dumps(
                {
                    "workflow_run_id": context.lease.workflow_run_id,
                    "kind": kind,
                    "source_id": source_id,
                }
            )
        ).hexdigest()
        return f"proposal-{kind.lower()}-{digest[:32]}"


class IndependentSeedStageHandler:
    def __init__(self, registry: IndependentSeedRegistry) -> None:
        self._registry = registry

    def execute(self, context: StageContext) -> dict[str, object]:
        if context.lease.head.seed_registry_id != self._registry.registry_id:
            raise ContractValidationError("Independent seed registry is not pinned")
        freeze = CandidateInputProjection.freeze(context)
        model_seeds = [
            {
                "proposal_id": CandidateInputProjection.proposal_id(
                    context, "independent", model.model_id
                ),
                "model_id": model.model_id,
                "display_name": model.display_name,
                "allowed_parameters": [
                    value.model_dump(mode="json") for value in model.allowed_parameters
                ],
                "support_refs": model.support_refs,
            }
            for model in self._registry.select(context.state.founder)
        ]
        reason_codes = [] if model_seeds else ["NO_ELIGIBLE_INDEPENDENT_SEED"]
        return {
            "stage_control": StageControl(reason_codes=reason_codes).model_dump(mode="json"),
            "independent_seed": {
                "seed_registry_id": self._registry.registry_id,
                "evidence_snapshot_id": freeze["snapshot_id"],
                "proposal_input": {
                    "founder": CandidateInputProjection.founder(context),
                    "area": CandidateInputProjection.area(context),
                    "evidence_records": freeze["evidence_records"],
                    "claim_id_pool": freeze.get("missing_claim_ids", []),
                    "model_seeds": model_seeds,
                    "requested_candidate_count": min(3, len(model_seeds)),
                },
                "reason_codes": reason_codes,
            },
        }


class FranchiseEligibilityStageHandler:
    def execute(self, context: StageContext) -> dict[str, object]:
        freeze = CandidateInputProjection.freeze(context)
        accepted_ids = {
            value.get("evidence_id")
            for value in freeze["evidence_records"]
            if isinstance(value, dict) and isinstance(value.get("evidence_id"), str)
        }
        source_universe = freeze.get("franchise_universe", [])
        if not isinstance(source_universe, list):
            raise ContractValidationError("Frozen franchise universe is invalid")
        by_brand: dict[str, dict[str, Any]] = {}
        conflicting_brand_ids: set[str] = set()
        for value in source_universe:
            if not isinstance(value, dict) or not isinstance(value.get("brand_id"), str):
                raise ContractValidationError("Frozen franchise brand is invalid")
            brand_id = value["brand_id"]
            previous = by_brand.get(brand_id)
            if previous is not None and previous != value:
                conflicting_brand_ids.add(brand_id)
                continue
            by_brand[brand_id] = value

        eligible: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for brand_id in sorted(by_brand):
            value = by_brand[brand_id]
            eligibility = value.get("individual_franchise_eligibility")
            evidence_id = value.get("eligibility_evidence_id")
            if brand_id in conflicting_brand_ids:
                excluded.append(
                    {
                        "brand_id": brand_id,
                        "reason_code": "FRANCHISE_ELIGIBILITY_CONFLICT",
                    }
                )
                continue
            evidence_is_accepted = (
                isinstance(evidence_id, str) and evidence_id in accepted_ids
            )
            if eligibility == "INELIGIBLE" and evidence_is_accepted:
                excluded.append({"brand_id": brand_id, "reason_code": "FRANCHISE_INELIGIBLE"})
                continue
            if eligibility != "VERIFIED" or not isinstance(evidence_id, str):
                excluded.append(
                    {"brand_id": brand_id, "reason_code": "FRANCHISE_ELIGIBILITY_UNVERIFIED"}
                )
                continue
            if not evidence_is_accepted:
                excluded.append(
                    {
                        "brand_id": brand_id,
                        "reason_code": "FRANCHISE_ELIGIBILITY_EVIDENCE_NOT_ACCEPTED",
                    }
                )
                continue
            disclosure_status = value.get("disclosure_status")
            missing_fields = ["area_availability_hq_confirmation"]
            if disclosure_status == "MISSING":
                missing_fields.append("franchise_disclosure")
            elif disclosure_status == "STALE":
                missing_fields.append("franchise_disclosure_freshness")
            elif disclosure_status != "AVAILABLE":
                raise ContractValidationError("Frozen franchise disclosure status is invalid")
            eligible.append(
                {
                    "proposal_id": CandidateInputProjection.proposal_id(
                        context, "franchise", brand_id
                    ),
                    "brand_id": brand_id,
                    "display_name": value.get("display_name"),
                    "individual_franchise_eligibility": "VERIFIED",
                    "evidence_refs": [evidence_id],
                    "missing_fields": sorted(missing_fields),
                }
            )
        reason_codes = [] if eligible else ["NO_VERIFIED_FRANCHISE_CANDIDATE"]
        return {
            "stage_control": StageControl(reason_codes=reason_codes).model_dump(mode="json"),
            "franchise_eligibility": {
                "evidence_snapshot_id": freeze["snapshot_id"],
                "proposal_input": {
                    "founder": CandidateInputProjection.founder(context),
                    "area": CandidateInputProjection.area(context),
                    "evidence_records": freeze["evidence_records"],
                    "claim_id_pool": freeze.get("missing_claim_ids", []),
                    "franchise_universe": eligible,
                    "requested_candidate_count": min(3, len(eligible)),
                },
                "excluded_brands": excluded,
                "reason_codes": reason_codes,
            },
        }
