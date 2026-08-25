"""Build the verified franchise proposal universe from accepted official evidence."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.finance.franchise_disclosure import (
    FranchiseDisclosureResolution,
    resolve_franchise_disclosures,
)
from app.mcp.client import McpCallOutcome

FTC_STARTUP_COST_SOURCE_REF = "https://www.data.go.kr/data/15110265/openapi.do"


def verified_franchise_brand_ids(outcomes: list[McpCallOutcome]) -> list[str]:
    candidates: list[dict[str, Any]] = []
    for outcome in outcomes:
        if outcome.tool_name != "list_franchise_universe":
            continue
        candidates.extend(
            item
            for item in outcome.structured_content.get("data", [])
            if isinstance(item, dict)
            and item.get("individual_franchise_eligibility") == "VERIFIED"
            and isinstance(item.get("brand_id"), str)
        )

    def sort_key(item: dict[str, Any]) -> tuple[bool, int, str]:
        profile = item.get("finance_profile")
        cost_range = (
            profile.get("known_initial_cost_range_krw")
            if isinstance(profile, dict)
            else None
        )
        base_cost = cost_range.get("base") if isinstance(cost_range, dict) else None
        return (
            not isinstance(base_cost, int),
            base_cost if isinstance(base_cost, int) else 0,
            str(item["brand_id"]),
        )

    # Proposal itself can consume at most three franchise candidates. Querying
    # every catalog brand would waste MCP/Evidence capacity without changing
    # the current decision pool.
    return [str(item["brand_id"]) for item in sorted(candidates, key=sort_key)[:3]]


def franchise_disclosure_resolution(
    *,
    outcomes: list[McpCallOutcome],
    evidence_records: list[dict[str, Any]],
) -> FranchiseDisclosureResolution:
    accepted_evidence_ids = {
        str(record["evidence_id"])
        for record in evidence_records
        if isinstance(record.get("evidence_id"), str)
    }
    return resolve_franchise_disclosures(
        structured_results=[outcome.structured_content for outcome in outcomes],
        accepted_evidence_ids=accepted_evidence_ids,
        eligible_brand_ids=set(verified_franchise_brand_ids(outcomes)),
    )


def franchise_universe(
    outcomes: list[McpCallOutcome],
    *,
    evidence_records: list[dict[str, Any]],
    disclosure_resolution: FranchiseDisclosureResolution | None = None,
) -> list[dict[str, Any]]:
    universe: list[dict[str, Any]] = []
    accepted_evidence_ids = {
        record.get("evidence_id")
        for record in evidence_records
        if isinstance(record.get("evidence_id"), str)
    }
    disclosure = disclosure_resolution or FranchiseDisclosureResolution()
    disclosure_by_brand = {value.source_id: value for value in disclosure.overrides}

    for outcome in outcomes:
        if outcome.tool_name != "list_franchise_universe":
            continue
        missing = [
            value
            for value in outcome.structured_content.get("missing_fields", [])
            if isinstance(value, str)
        ]
        for item in outcome.structured_content.get("data", []):
            if not isinstance(item, dict) or item.get(
                "individual_franchise_eligibility"
            ) != "VERIFIED":
                continue
            evidence_id = item.get("eligibility_evidence_id")
            if (
                not isinstance(evidence_id, str)
                or evidence_id not in accepted_evidence_ids
            ):
                continue
            finance_profile = deepcopy(item.get("finance_profile"))
            if not isinstance(finance_profile, dict):
                continue
            finance_refs = {
                ref
                for ref in finance_profile.get("evidence_refs", [])
                if isinstance(ref, str)
            }
            if not finance_refs.issubset(accepted_evidence_ids):
                finance_profile.update(
                    {
                        "coverage": "UNKNOWN",
                        "value_kind": "UNKNOWN",
                        "known_initial_cost_range_krw": None,
                        "evidence_refs": [],
                        "missing_costs": sorted(
                            set(finance_profile.get("missing_costs", []))
                            | {"TOTAL_INITIAL_COST"}
                        ),
                    }
                )
            disclosure_override = disclosure_by_brand.get(str(item["brand_id"]))
            if disclosure_override is not None:
                finance_profile.update(
                    {
                        "coverage": "PARTIAL",
                        "value_kind": "EVIDENCED_FACT",
                        "known_initial_cost_range_krw": disclosure_override.amount.model_dump(
                            mode="json"
                        ),
                        "evidence_refs": [disclosure_override.evidence_ref],
                        "source_refs": [FTC_STARTUP_COST_SOURCE_REF],
                        "scope_note": (
                            "공정거래위원회 신고연도 기준 가맹비·교육비·"
                            "가맹사업자 보증금·기타 초기금액의 공식 합계"
                        ),
                        "missing_costs": sorted(
                            {
                                value
                                for value in finance_profile.get("missing_costs", [])
                                if isinstance(value, str)
                            }
                            - {"TOTAL_INITIAL_COST"}
                        ),
                    }
                )
            universe.append(
                {
                    "proposal_id": f"proposal:{item['brand_id']}",
                    "brand_id": item["brand_id"],
                    "display_name": item["display_name"],
                    "individual_franchise_eligibility": "VERIFIED",
                    "evidence_refs": [evidence_id],
                    "finance_profile": finance_profile,
                    "missing_fields": sorted(set(missing)),
                }
            )

    def sort_key(item: dict[str, Any]) -> tuple[bool, int, str]:
        # 비용 근거가 부족한 조건부 브랜드도 유지하되, 확인된 비용 후보 뒤에 둔다.
        finance_profile = item["finance_profile"]
        cost_range = finance_profile.get("known_initial_cost_range_krw")
        base_cost = cost_range.get("base", 0) if isinstance(cost_range, dict) else 0
        return cost_range is None, base_cost, item["brand_id"]

    return sorted(universe, key=sort_key)