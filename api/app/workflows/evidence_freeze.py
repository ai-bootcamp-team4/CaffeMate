import hashlib
from typing import Any

import rfc8785
from pydantic import Field

from app.contracts.schema_registry import ContractRegistry, EvidenceContractValidator
from app.domain.errors import ContractValidationError
from app.domain.models import StrictModel
from app.workflows.models import StageControl
from app.workflows.stage_context import StageContext


class EvidenceFreezeOutput(StrictModel):
    snapshot_id: str = Field(min_length=1, max_length=128)
    snapshot_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    schema_version: str
    project_id: str = Field(min_length=1)
    workflow_run_id: str = Field(min_length=1)
    source_stage_run_id: str = Field(min_length=1)
    evidence_records: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    missing_claim_ids: list[str]
    reason_codes: list[str]
    retrieval_completeness: str | None
    franchise_universe: list[dict[str, Any]]


class EvidenceFreezeStageHandler:
    def __init__(
        self,
        *,
        contracts: EvidenceContractValidator | None = None,
    ) -> None:
        self._contracts = contracts or ContractRegistry()

    def execute(self, context: StageContext) -> dict[str, object]:
        assessment = self._assessment(context)
        records = self._raw_records(assessment, project_id=context.project_id)
        accepted_ids = self._accepted_evidence_ids(assessment, records)
        accepted_records = [records[evidence_id] for evidence_id in sorted(accepted_ids)]
        conflicts = self._validated_conflicts(assessment, accepted_ids)
        missing_claim_ids = self._missing_claim_ids(assessment)
        franchise_universe = self._franchise_universe(assessment)
        snapshot_body = {
            "schema_version": "1.0.0",
            "project_id": context.project_id,
            "workflow_run_id": context.lease.workflow_run_id,
            "source_stage_run_id": context.lease.stage_run_id,
            "evidence_records": accepted_records,
            "conflicts": conflicts,
            "missing_claim_ids": missing_claim_ids,
            "reason_codes": assessment.get("reason_codes", []),
            "retrieval_completeness": assessment.get("retrieval_completeness"),
            "franchise_universe": franchise_universe,
        }
        digest = hashlib.sha256(rfc8785.dumps(snapshot_body)).hexdigest()
        snapshot_id = f"evidence-{digest[:40]}"
        return {
            "stage_control": StageControl().model_dump(mode="json"),
            "evidence_freeze": {
                "snapshot_id": snapshot_id,
                "snapshot_digest": f"sha256:{digest}",
                **snapshot_body,
            },
        }

    @staticmethod
    def _assessment(context: StageContext) -> dict[str, Any]:
        dependency = context.dependency_results.get("EVIDENCE_ASSESS")
        value = dependency.get("evidence_assessment") if dependency else None
        if not isinstance(value, dict):
            raise ContractValidationError("EVIDENCE_FREEZE requires Evidence Assessment results")
        if not isinstance(value.get("claims"), list) or not value["claims"]:
            raise ContractValidationError("Evidence Assessment claims are missing")
        if not isinstance(value.get("executed_actions"), list):
            raise ContractValidationError("Evidence Assessment actions are invalid")
        return value

    def _raw_records(
        self,
        assessment: dict[str, Any],
        *,
        project_id: str,
    ) -> dict[str, dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        immutable_digests: dict[str, bytes] = {}
        canonical_records: dict[str, bytes] = {}
        for action in assessment["executed_actions"]:
            if not isinstance(action, dict):
                raise ContractValidationError("Executed Evidence action is invalid")
            structured = action.get("structured_result")
            if not isinstance(structured, dict):
                raise ContractValidationError("Executed Evidence result is invalid")
            values = structured.get("evidence_records")
            if not isinstance(values, list):
                raise ContractValidationError("Evidence records are missing")
            for value in values:
                if not isinstance(value, dict):
                    raise ContractValidationError("Evidence record is invalid")
                self._contracts.validate_evidence_record(value)
                if value["project_id"] != project_id:
                    raise ContractValidationError("Evidence record crossed project scope")
                evidence_id = value["evidence_id"]
                immutable_digest = hashlib.sha256(
                    self._immutable_record_bytes(value)
                ).digest()
                canonical_record = rfc8785.dumps(value)
                if (
                    evidence_id in immutable_digests
                    and immutable_digests[evidence_id] != immutable_digest
                ):
                    raise ContractValidationError(
                        "Evidence id refers to conflicting immutable records"
                    )
                if (
                    evidence_id not in canonical_records
                    or canonical_record < canonical_records[evidence_id]
                ):
                    records[evidence_id] = value
                    canonical_records[evidence_id] = canonical_record
                immutable_digests[evidence_id] = immutable_digest
        return records

    @staticmethod
    def _immutable_record_bytes(value: dict[str, Any]) -> bytes:
        """Exclude per-call observation times from immutable Evidence identity.

        The same source row or RAG chunk can be returned by support and counter
        searches milliseconds apart. Its content, source version and checksum are
        immutable; retrieval timestamps describe the calls, not different facts.
        """
        stable = dict(value)
        stable.pop("retrieved_at", None)
        source = stable.get("source")
        if isinstance(source, dict):
            stable["source"] = {
                key: item for key, item in source.items() if key != "source_observed_at"
            }
        return rfc8785.dumps(stable)

    @staticmethod
    def _accepted_evidence_ids(
        assessment: dict[str, Any],
        records: dict[str, dict[str, Any]],
    ) -> set[str]:
        accepted: set[str] = set()
        values = assessment.get("assessments", [])
        if not isinstance(values, list):
            raise ContractValidationError("Evidence assessments are invalid")
        for value in values:
            if not isinstance(value, dict):
                raise ContractValidationError("Evidence assessment is invalid")
            candidate_ref = value.get("candidate_ref")
            if not isinstance(candidate_ref, str) or candidate_ref not in records:
                raise ContractValidationError("Assessment references unknown Evidence")
            if (
                value.get("relation") in {"SUPPORTS", "CONTRADICTS"}
                and value.get("scope_status") == "MATCH"
                and value.get("date_status") == "MATCH"
                and value.get("freshness_status") in {"FRESH", "NOT_APPLICABLE"}
                and value.get("anchor_status") == "VALID"
                and value.get("authority_status") == "ACCEPTABLE"
            ):
                accepted.add(candidate_ref)
        return EvidenceFreezeStageHandler._include_structured_metric_siblings(
            assessment,
            records,
            accepted,
        )

    @staticmethod
    def _include_structured_metric_siblings(
        assessment: dict[str, Any],
        records: dict[str, dict[str, Any]],
        accepted_ids: set[str],
    ) -> set[str]:
        """Keep trusted sibling metrics from an assessed structured read.

        The assessment task intentionally receives one representative Evidence
        record per physical MCP result to keep Agent latency bounded. When that
        representative is accepted, the complete retrieval result still contains
        other metrics from the same Claim-scoped official dataset action. Those
        siblings are deterministic facts, not additional semantic assertions, so
        they may enter the snapshot without another model call when every guard
        below holds. RAG, web, user-document, stale and conflicting records never
        use this expansion path.
        """
        expanded = set(accepted_ids)
        for action in assessment["executed_actions"]:
            if not isinstance(action, dict):
                raise ContractValidationError("Executed Evidence action is invalid")
            structured = action.get("structured_result")
            values = structured.get("evidence_records") if isinstance(structured, dict) else None
            if not isinstance(values, list):
                raise ContractValidationError("Evidence records are missing")

            action_records = [
                records[value["evidence_id"]]
                for value in values
                if isinstance(value, dict)
                and isinstance(value.get("evidence_id"), str)
                and value["evidence_id"] in records
            ]
            representatives = [
                record
                for record in action_records
                if record["evidence_id"] in accepted_ids
                and EvidenceFreezeStageHandler._is_expandable_structured_metric(record)
            ]
            if not representatives:
                continue

            for record in action_records:
                if not EvidenceFreezeStageHandler._is_expandable_structured_metric(record):
                    continue
                if any(
                    record.get("claim_type") == representative.get("claim_type")
                    and record.get("geographic_scope")
                    == representative.get("geographic_scope")
                    for representative in representatives
                ):
                    expanded.add(record["evidence_id"])
        return expanded

    @staticmethod
    def _is_expandable_structured_metric(record: dict[str, Any]) -> bool:
        source = record.get("source")
        anchor = record.get("original_anchor")
        return (
            isinstance(record.get("metric"), str)
            and bool(record["metric"])
            and record.get("value_kind") in {"EVIDENCED_FACT", "DERIVED_RESULT"}
            and record.get("freshness_status") in {"FRESH", "NOT_APPLICABLE"}
            and record.get("conflict_status") in {"NONE", "RESOLVED"}
            and isinstance(source, dict)
            and source.get("authority") == "PRIMARY_DATA"
            and source.get("source_type") == "DATASET"
            and isinstance(source.get("source_ref"), str)
            and bool(source["source_ref"])
            and isinstance(source.get("checksum"), str)
            and bool(source["checksum"])
            and isinstance(anchor, dict)
            and anchor.get("anchor_type") in {"DATASET_ROW", "CALCULATION"}
            and isinstance(anchor.get("locator"), str)
            and bool(anchor["locator"])
        )

    @staticmethod
    def _validated_conflicts(
        assessment: dict[str, Any],
        accepted_ids: set[str],
    ) -> list[dict[str, Any]]:
        values = assessment.get("conflict_proposals", [])
        if not isinstance(values, list):
            raise ContractValidationError("Evidence conflicts are invalid")
        conflicts: list[dict[str, Any]] = []
        for value in values:
            if not isinstance(value, dict):
                raise ContractValidationError("Evidence conflict is invalid")
            refs = value.get("candidate_refs")
            if not isinstance(refs, list) or not set(refs).issubset(accepted_ids):
                raise ContractValidationError("Evidence conflict references unaccepted Evidence")
            conflicts.append(value)
        return conflicts

    @staticmethod
    def _missing_claim_ids(assessment: dict[str, Any]) -> list[str]:
        values = assessment.get("missing_claim_ids", [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ContractValidationError("Missing Evidence claims are invalid")
        return sorted(set(values))

    @staticmethod
    def _franchise_universe(assessment: dict[str, Any]) -> list[dict[str, Any]]:
        universe: list[dict[str, Any]] = []
        for action in assessment["executed_actions"]:
            if not isinstance(action, dict) or action.get("tool_name") != "list_franchise_universe":
                continue
            structured = action.get("structured_result")
            data = structured.get("data") if isinstance(structured, dict) else None
            if not isinstance(data, list):
                raise ContractValidationError("Franchise universe result is invalid")
            if any(not isinstance(value, dict) for value in data):
                raise ContractValidationError("Franchise universe brand is invalid")
            universe.extend(dict(value) for value in data)
        return universe
