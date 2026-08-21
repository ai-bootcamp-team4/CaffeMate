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
            raise ContractValidationError(
                "EVIDENCE_FREEZE requires Evidence Assessment results"
            )
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
        digests: dict[str, bytes] = {}
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
                digest = hashlib.sha256(rfc8785.dumps(value)).digest()
                if evidence_id in digests and digests[evidence_id] != digest:
                    raise ContractValidationError(
                        "Evidence id refers to conflicting immutable records"
                    )
                records[evidence_id] = value
                digests[evidence_id] = digest
        return records

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
        return accepted

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
                raise ContractValidationError(
                    "Evidence conflict references unaccepted Evidence"
                )
            conflicts.append(value)
        return conflicts

    @staticmethod
    def _missing_claim_ids(assessment: dict[str, Any]) -> list[str]:
        values = assessment.get("missing_claim_ids", [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ContractValidationError("Missing Evidence claims are invalid")
        return sorted(set(values))
