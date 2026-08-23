import hashlib
from typing import Any

import rfc8785

from app.candidates.seed_registry import IndependentFinanceProfile, IndependentSeedRegistry
from app.contracts.schema_registry import ContractRegistry, EvidenceContractValidator
from app.domain.models import CaseType
from app.finance.models import (
    INITIAL_COST_CATEGORIES,
    MONTHLY_FIXED_COST_CATEGORIES,
    CostCategory,
    CostLine,
    FinanceInput,
    MoneyRange,
    ValueProvenance,
)
from app.workflows.stage_context import StageContext
from app.workflows.user_cost_evidence import UserCostEvidenceProjector


class CandidateFinanceInputBuilder:
    def __init__(
        self,
        seed_registry: IndependentSeedRegistry | None = None,
        *,
        contracts: EvidenceContractValidator | None = None,
    ) -> None:
        self._seed_registry = seed_registry
        self._contracts = contracts or ContractRegistry()
        self._user_evidence = UserCostEvidenceProjector()

    def evidence_records(
        self,
        *,
        context: StageContext,
        case_type: CaseType,
        source_id: str,
        base_records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        records = [
            *base_records,
            *self._user_evidence.project(
                context=context,
                case_type=case_type,
                source_id=source_id,
            ),
            *self._seed_assumption_evidence(
                context=context,
                case_type=case_type,
                source_id=source_id,
            ),
        ]
        for record in records:
            self._contracts.validate_evidence_record(record)
        return records

    def build(
        self,
        *,
        case_type: CaseType,
        source_id: str,
        proposal_id: str,
        evidence_records: list[dict[str, Any]],
        seed_finance_profile: IndependentFinanceProfile | None,
    ) -> tuple[FinanceInput, list[str], list[str]]:
        conflicts: list[str] = []
        initial = [
            self._cost_line(
                case_type=case_type,
                category=category,
                source_id=source_id,
                proposal_id=proposal_id,
                evidence_records=evidence_records,
                conflicts=conflicts,
                seed_finance_profile=seed_finance_profile,
            )
            for category in sorted(INITIAL_COST_CATEGORIES, key=lambda value: value.value)
        ]
        monthly = [
            self._cost_line(
                case_type=case_type,
                category=category,
                source_id=source_id,
                proposal_id=proposal_id,
                evidence_records=evidence_records,
                conflicts=conflicts,
                seed_finance_profile=seed_finance_profile,
            )
            for category in sorted(MONTHLY_FIXED_COST_CATEGORIES, key=lambda value: value.value)
        ]
        contribution_margin, contribution_margin_ref = self._scalar_value(
            case_type,
            "CONTRIBUTION_MARGIN_BPS",
            source_id,
            proposal_id,
            evidence_records,
            conflicts,
            seed_finance_profile,
        )
        operating_days, operating_days_ref = self._scalar_value(
            case_type,
            "OPERATING_DAYS_PER_MONTH",
            source_id,
            proposal_id,
            evidence_records,
            conflicts,
            seed_finance_profile,
        )
        average_ticket, average_ticket_ref = self._scalar_value(
            case_type,
            "AVERAGE_TICKET_KRW",
            source_id,
            proposal_id,
            evidence_records,
            conflicts,
            seed_finance_profile,
        )
        finance_input = FinanceInput(
            initial_cost_lines=initial,
            monthly_fixed_cost_lines=monthly,
            contribution_margin_bps=contribution_margin,
            operating_days_per_month=operating_days,
            average_ticket_krw=average_ticket,
        )
        cost_refs = {
            line.evidence_ref for line in [*initial, *monthly] if line.evidence_ref is not None
        }
        scalar_refs = {
            value
            for value in (
                contribution_margin_ref,
                operating_days_ref,
                average_ticket_ref,
            )
            if value is not None
        }
        return finance_input, sorted(set(conflicts)), sorted(cost_refs | scalar_refs)

    def seed_finance_profile(
        self, case_type: CaseType, source_id: str
    ) -> IndependentFinanceProfile | None:
        if case_type != CaseType.INDEPENDENT or self._seed_registry is None:
            return None
        seed = self._seed_registry.get(source_id)
        return seed.finance_profile if seed is not None else None

    def _seed_assumption_evidence(
        self,
        *,
        context: StageContext,
        case_type: CaseType,
        source_id: str,
    ) -> list[dict[str, Any]]:
        profile = self.seed_finance_profile(case_type, source_id)
        if profile is None:
            return []

        values: list[tuple[str, dict[str, Any], str | None]] = []
        for category, amount in sorted(profile.cost_ranges.items(), key=lambda item: item[0].value):
            values.append(
                (
                    f"INDEPENDENT_COST_{category.value}",
                    {
                        "kind": "MONEY_RANGE",
                        "currency": "KRW",
                        **amount.model_dump(mode="json"),
                    },
                    "KRW",
                )
            )
        values.extend(
            [
                (
                    "CONTRIBUTION_MARGIN_BPS",
                    {"kind": "INTEGER", "value": profile.contribution_margin_bps},
                    "basis_point",
                ),
                (
                    "OPERATING_DAYS_PER_MONTH",
                    {"kind": "INTEGER", "value": profile.operating_days_per_month},
                    "day/month",
                ),
                (
                    "AVERAGE_TICKET_KRW",
                    {"kind": "INTEGER", "value": profile.average_ticket_krw},
                    "KRW/order",
                ),
            ]
        )
        timestamp = context.state.updated_at.isoformat().replace("+00:00", "Z")
        records: list[dict[str, Any]] = []
        for claim_type, value, unit in values:
            digest = hashlib.sha256(
                rfc8785.dumps(
                    {
                        "seed_registry_id": context.lease.head.seed_registry_id,
                        "source_id": source_id,
                        "claim_type": claim_type,
                        "value": value,
                    }
                )
            ).hexdigest()
            records.append(
                {
                    "schema_version": "2.0.0",
                    "evidence_id": f"seed-assumption-{digest[:40]}",
                    "project_id": context.project_id,
                    "claim_type": claim_type,
                    "value": value,
                    "value_kind": "DECLARED_ASSUMPTION",
                    "unit": unit,
                    "geographic_scope": {
                        "scope_type": "CASE",
                        "scope_id": source_id,
                        "boundary_version": None,
                    },
                    "source": {
                        "title": f"{source_id} 등록 모델 임시 계산값",
                        "source_ref": f"seed://{source_id}/{claim_type}",
                        "authority": "SECONDARY",
                        "source_type": "DATASET",
                        "published_or_data_date": None,
                        "source_observed_at": None,
                        "document_version": context.lease.head.seed_registry_id,
                        "checksum": digest,
                    },
                    "original_anchor": {
                        "anchor_type": "CALCULATION",
                        "locator": f"seed:{source_id}:{claim_type}",
                        "excerpt_hash": None,
                    },
                    "freshness_status": "NOT_APPLICABLE",
                    "conflict_status": "NONE",
                    "retrieved_at": timestamp,
                    "missing_context": ["실제 매물·견적 입력 전 사용하는 등록 모델 임시 범위"],
                    "durable_evidence_refs": [],
                }
            )
        return records

    def _cost_line(
        self,
        *,
        case_type: CaseType,
        category: CostCategory,
        source_id: str,
        proposal_id: str,
        evidence_records: list[dict[str, Any]],
        conflicts: list[str],
        seed_finance_profile: IndependentFinanceProfile | None,
    ) -> CostLine:
        if case_type == CaseType.INDEPENDENT and category == CostCategory.FRANCHISE_INITIAL_FEES:
            return CostLine(
                field_id=category.value,
                category=category,
                amount=MoneyRange(low=0, base=0, high=0),
                provenance=ValueProvenance.DERIVED,
            )
        if any(
            value.get("claim_type") == f"DOCUMENT_CONFLICT_COST_{category.value}"
            for value in evidence_records
        ):
            conflicts.append(f"DOCUMENT_COST_CONFLICT:{category.value}")
            return self._unknown_cost(category)
        matches = self._money_records(
            case_type,
            category.value,
            source_id,
            proposal_id,
            evidence_records,
        )
        if not matches:
            if seed_finance_profile is not None:
                amount = seed_finance_profile.cost_ranges.get(category)
                if amount is not None:
                    return CostLine(
                        field_id=category.value,
                        category=category,
                        amount=amount,
                        provenance=ValueProvenance.ASSUMPTION,
                    )
            return self._unknown_cost(category)
        grounded_or_confirmed = [
            value for value in matches if value.get("value_kind") != "DECLARED_ASSUMPTION"
        ]
        if grounded_or_confirmed:
            matches = grounded_or_confirmed
        user_confirmed = [
            value for value in matches if value.get("value_kind") == "USER_CONFIRMED_FACT"
        ]
        if user_confirmed:
            matches = user_confirmed
        distinct = {
            (
                value["value"].get("low"),
                value["value"].get("base"),
                value["value"].get("high"),
            )
            for value in matches
        }
        if len(distinct) != 1:
            conflicts.append(f"COST_CONFLICT:{category.value}")
            return self._unknown_cost(category)
        selected = min(matches, key=self._evidence_priority)
        typed = selected["value"]
        return CostLine(
            field_id=category.value,
            category=category,
            amount=MoneyRange(
                low=typed.get("low"),
                base=typed.get("base"),
                high=typed.get("high"),
            ),
            provenance=self._provenance(selected),
            evidence_ref=selected["evidence_id"],
        )

    @staticmethod
    def _unknown_cost(category: CostCategory) -> CostLine:
        return CostLine(
            field_id=category.value,
            category=category,
            amount=MoneyRange(low=None, base=None, high=None),
            provenance=ValueProvenance.UNKNOWN,
        )

    def _money_records(
        self,
        case_type: CaseType,
        field: str,
        source_id: str,
        proposal_id: str,
        evidence_records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        claim_types = {
            f"{case_type.value}_COST_{field}",
            f"CAFE_COST_{field}",
            f"COST_{field}",
        }
        return [
            value
            for value in evidence_records
            if value.get("claim_type") in claim_types
            and isinstance(value.get("value"), dict)
            and value["value"].get("kind") == "MONEY_RANGE"
            and self._scope_matches(value, source_id, proposal_id)
        ]

    def _scalar_value(
        self,
        case_type: CaseType,
        field: str,
        source_id: str,
        proposal_id: str,
        evidence_records: list[dict[str, Any]],
        conflicts: list[str],
        seed_finance_profile: IndependentFinanceProfile | None,
    ) -> tuple[int | None, str | None]:
        claim_types = {field, f"CAFE_{field}", f"{case_type.value}_{field}"}
        values = [
            value
            for value in evidence_records
            if value.get("claim_type") in claim_types
            and isinstance(value.get("value"), dict)
            and value["value"].get("kind") == "INTEGER"
            and isinstance(value["value"].get("value"), int)
            and not isinstance(value["value"].get("value"), bool)
            and self._scope_matches(value, source_id, proposal_id)
        ]
        grounded_or_confirmed = [
            value for value in values if value.get("value_kind") != "DECLARED_ASSUMPTION"
        ]
        if grounded_or_confirmed:
            values = grounded_or_confirmed
        distinct = {value["value"]["value"] for value in values}
        if len(distinct) > 1:
            conflicts.append(f"VALUE_CONFLICT:{field}")
            return None, None
        if not values:
            if seed_finance_profile is not None:
                fallback = {
                    "CONTRIBUTION_MARGIN_BPS": seed_finance_profile.contribution_margin_bps,
                    "OPERATING_DAYS_PER_MONTH": seed_finance_profile.operating_days_per_month,
                    "AVERAGE_TICKET_KRW": seed_finance_profile.average_ticket_krw,
                }.get(field)
                if fallback is not None:
                    return fallback, None
            return None, None
        selected = min(values, key=self._evidence_priority)
        return int(selected["value"]["value"]), str(selected["evidence_id"])

    @staticmethod
    def _scope_matches(evidence: dict[str, Any], source_id: str, proposal_id: str) -> bool:
        scope = evidence.get("geographic_scope")
        if not isinstance(scope, dict) or scope.get("scope_type") != "CASE":
            return True
        return scope.get("scope_id") in {source_id, proposal_id}

    @staticmethod
    def _evidence_priority(value: dict[str, Any]) -> tuple[int, str]:
        value_kind = value.get("value_kind")
        authority = value.get("source", {}).get("authority")
        priority = 0 if value_kind == "USER_CONFIRMED_FACT" else 1
        if authority == "VALIDATED_BENCHMARK":
            priority = 2
        if value_kind == "DECLARED_ASSUMPTION":
            priority = 3
        return priority, str(value.get("evidence_id"))

    @staticmethod
    def _provenance(value: dict[str, Any]) -> ValueProvenance:
        if value.get("value_kind") == "USER_CONFIRMED_FACT":
            return ValueProvenance.USER_INPUT
        if value.get("source", {}).get("authority") == "VALIDATED_BENCHMARK":
            return ValueProvenance.BENCHMARK
        if value.get("value_kind") == "DECLARED_ASSUMPTION":
            return ValueProvenance.ASSUMPTION
        return ValueProvenance.FACT
