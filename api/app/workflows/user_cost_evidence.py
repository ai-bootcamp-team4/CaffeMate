import hashlib
from datetime import UTC, datetime
from typing import Any, cast

import rfc8785

from app.domain.models import CaseType
from app.finance.models import CostCategory
from app.workflows.stage_context import StageContext


class UserCostEvidenceProjector:
    def project(
        self,
        *,
        context: StageContext,
        case_type: CaseType,
        source_id: str,
    ) -> list[dict[str, Any]]:
        category_map = {
            "LEASE_DEPOSIT": CostCategory.DEPOSIT.value,
            "KEY_MONEY": CostCategory.ACQUISITION_OR_PREMIUM.value,
            "MONTHLY_RENT": CostCategory.MONTHLY_OCCUPANCY.value,
            "MANAGEMENT_FEE": CostCategory.MONTHLY_OCCUPANCY.value,
            "FRANCHISE_FEE": CostCategory.FRANCHISE_INITIAL_FEES.value,
            "EDUCATION_FEE": CostCategory.FRANCHISE_INITIAL_FEES.value,
        }
        grouped: dict[str, list[dict[str, Any]]] = {}
        for claim in context.document_claims:
            if (
                claim.get("case_type") != case_type.value
                or claim.get("source_id") != source_id
                or not isinstance(claim.get("value_json"), int)
                or isinstance(claim.get("value_json"), bool)
            ):
                continue
            claim_type = claim.get("claim_type")
            category = category_map.get(str(claim_type))
            if claim_type == "QUOTE_TOTAL":
                if claim.get("document_type") == "INTERIOR_QUOTE":
                    category = CostCategory.CONSTRUCTION.value
                elif claim.get("document_type") == "EQUIPMENT_QUOTE":
                    category = CostCategory.EQUIPMENT.value
            if category is None:
                continue
            key = f"CONFLICT:{category}" if claim.get("has_open_conflict") is True else category
            grouped.setdefault(key, []).append(claim)

        records: list[dict[str, Any]] = []
        for category, claims in sorted(grouped.items()):
            conflict = category.startswith("CONFLICT:")
            actual_category = category.removeprefix("CONFLICT:") if conflict else category
            records.append(
                self._record(
                    context=context,
                    source_id=source_id,
                    category=actual_category,
                    claims=claims,
                    conflict=conflict,
                )
            )
        return records

    def _record(
        self,
        *,
        context: StageContext,
        source_id: str,
        category: str,
        claims: list[dict[str, Any]],
        conflict: bool,
    ) -> dict[str, Any]:
        claim_ids = sorted(str(claim["claim_id"]) for claim in claims)
        identity = {
            "project_id": context.project_id,
            "source_id": source_id,
            "category": category,
            "claim_ids": claim_ids,
            "conflict": conflict,
        }
        digest = hashlib.sha256(rfc8785.dumps(cast(Any, identity))).hexdigest()
        property_input = all(
            claim.get("input_kind") == "USER_CONFIRMED_PROPERTY_TERMS" for claim in claims
        )
        observed_at = self._observed_at(claims, context.state.updated_at)
        if conflict:
            value: dict[str, Any] = {"kind": "NULL", "value": None}
            value_kind = "UNKNOWN"
            freshness_status = "UNKNOWN"
            missing_context = ["상충하는 사용자 확인 비용 입력이 있어 값을 확정할 수 없습니다."]
        else:
            amount = sum(int(claim["value_json"]) for claim in claims)
            value = {
                "kind": "MONEY_RANGE",
                "currency": "KRW",
                "low": amount,
                "base": amount,
                "high": amount,
            }
            value_kind = "USER_CONFIRMED_FACT"
            freshness_status = "NOT_APPLICABLE"
            missing_context = []
        property_input_id = self._property_input_id(claims) if property_input else None
        return {
            "schema_version": "2.0.0",
            "evidence_id": f"user-cost-{digest[:40]}",
            "project_id": context.project_id,
            "claim_type": (
                f"DOCUMENT_CONFLICT_COST_{category}" if conflict else f"COST_{category}"
            ),
            "value": value,
            "value_kind": value_kind,
            "unit": "KRW",
            "geographic_scope": {
                "scope_type": "PROPERTY" if property_input else "CASE",
                "scope_id": property_input_id if property_input else source_id,
                "boundary_version": None,
            },
            "source": {
                "title": (
                    "사용자가 확인한 점포 조건"
                    if property_input
                    else "사용자가 확인한 문서 비용 입력"
                ),
                "source_ref": self._source_ref(
                    claims,
                    digest,
                    property_input=property_input,
                ),
                "authority": "USER_ARTIFACT",
                "source_type": "USER_FIELD" if property_input else "USER_DOCUMENT",
                "published_or_data_date": None,
                "source_observed_at": observed_at,
                "document_version": self._document_version(claims),
                "checksum": digest,
            },
            "original_anchor": {
                "anchor_type": "USER_FIELD",
                "locator": "claims:" + ",".join(claim_ids),
                "excerpt_hash": None,
            },
            "freshness_status": freshness_status,
            "conflict_status": "CONFIRMED" if conflict else "NONE",
            "retrieved_at": observed_at,
            "missing_context": missing_context,
            "durable_evidence_refs": [],
        }

    @staticmethod
    def _property_input_id(claims: list[dict[str, Any]]) -> str:
        first = str(claims[0]["claim_id"])
        parts = first.split(":", 2)
        if len(parts) < 2 or not parts[1]:
            raise ValueError("Property Evidence claim id is invalid")
        return parts[1]

    @staticmethod
    def _source_ref(claims: list[dict[str, Any]], digest: str, *, property_input: bool) -> str:
        if property_input:
            first = str(claims[0]["claim_id"])
            parts = first.split(":", 2)
            if len(parts) >= 2:
                return f"user-field://property-terms/{parts[1]}"
            return f"user-field://property-terms/{digest[:16]}"
        revisions = sorted(
            {
                str(claim["document_revision_id"])
                for claim in claims
                if claim.get("document_revision_id")
            }
        )
        if len(revisions) == 1:
            return f"user-document://{revisions[0]}"
        return f"user-document://claims/{digest[:16]}"

    @staticmethod
    def _document_version(claims: list[dict[str, Any]]) -> str | None:
        revisions = sorted(
            {
                str(claim["document_revision_id"])
                for claim in claims
                if claim.get("document_revision_id")
            }
        )
        return revisions[0] if len(revisions) == 1 else None

    @classmethod
    def _observed_at(cls, claims: list[dict[str, Any]], fallback: datetime) -> str:
        values = [claim.get("observed_at") for claim in claims if claim.get("observed_at")]
        if not values:
            return cls._iso_timestamp(fallback)
        return max(cls._iso_timestamp(value) for value in values)

    @staticmethod
    def _iso_timestamp(value: object) -> str:
        if isinstance(value, datetime):
            current = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        elif isinstance(value, str) and value:
            current = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if current.tzinfo is None:
                current = current.replace(tzinfo=UTC)
        else:
            raise ValueError("User Evidence observation timestamp is invalid")
        return current.astimezone(UTC).isoformat().replace("+00:00", "Z")
