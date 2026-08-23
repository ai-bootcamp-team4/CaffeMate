from typing import Any

from app.contracts.schema_registry import ContractRegistry
from app.domain.errors import ContractValidationError
from app.domain.models import CafeTypePreference
from app.results.models import AuditStatus, ResultBundlePayload, ResultOutcomeStatus
from app.workflows.models import StageControl
from app.workflows.stage_context import StageContext


class CommitResultStageHandler:
    def __init__(self, *, contracts: ContractRegistry | None = None) -> None:
        self._contracts = contracts or ContractRegistry()

    def execute(self, context: StageContext) -> dict[str, object]:
        audit = self._audit(context)
        audit_status = self._audit_status(audit)
        candidates = audit.get("candidates")
        if not isinstance(candidates, list):
            raise ContractValidationError("COMMIT_RESULT candidates are invalid")
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise ContractValidationError("COMMIT_RESULT candidate is invalid")
            self._contracts.validate_candidate_result(candidate)
            if (
                candidate.get("project_id") != context.project_id
                or candidate.get("state_version") != context.lease.head.state_version
            ):
                raise ContractValidationError(
                    "COMMIT_RESULT candidate crossed the authoritative full head"
                )

        reviewable = [
            candidate
            for candidate in candidates
            if candidate.get("review_status") != "EXCLUDED"
        ]
        reviewable.sort(key=self._rank_key)
        self._validate_candidate_set(candidates, reviewable)
        self._validate_audit_consistency(audit, audit_status, candidates)
        if not reviewable:
            reason_codes = ["NO_REVIEWABLE_CANDIDATES"]
            excluded = candidates[:3]
            try:
                payload = ResultBundlePayload(
                    candidates=excluded,
                    primary_candidate_id=None,
                    audit_status=audit_status,
                    outcome_status=ResultOutcomeStatus.NO_REVIEWABLE_CANDIDATES,
                )
                payload.validate_contracts(
                    project_id=context.project_id,
                    state_version=context.lease.head.state_version,
                    contracts=self._contracts,
                )
            except ValueError as error:
                raise ContractValidationError(str(error)) from error
            return {
                "stage_control": StageControl().model_dump(mode="json"),
                "result_bundle": payload.model_dump(mode="json"),
                "commit_result": {
                    "status": "READY_TO_COMMIT",
                    "reason_codes": reason_codes,
                    "excluded_candidate_ids": sorted(
                        candidate["candidate_id"] for candidate in candidates
                    ),
                },
            }

        selected = self._select_candidates(
            reviewable,
            context.state.founder.cafe_type_preference,
        )
        selected_ids = {candidate["candidate_id"] for candidate in selected}
        try:
            payload = ResultBundlePayload(
                candidates=selected,
                primary_candidate_id=selected[0]["candidate_id"],
                audit_status=audit_status,
            )
            payload.validate_contracts(
                project_id=context.project_id,
                state_version=context.lease.head.state_version,
                contracts=self._contracts,
            )
        except ValueError as error:
            raise ContractValidationError(str(error)) from error
        return {
            "stage_control": StageControl().model_dump(mode="json"),
            "result_bundle": payload.model_dump(mode="json"),
            "commit_result": {
                "status": "READY_TO_COMMIT",
                "included_candidate_ids": [
                    candidate["candidate_id"] for candidate in selected
                ],
                "excluded_candidate_ids": sorted(
                    candidate["candidate_id"]
                    for candidate in candidates
                    if candidate.get("review_status") == "EXCLUDED"
                ),
                "omitted_candidate_ids": sorted(
                    candidate["candidate_id"]
                    for candidate in reviewable
                    if candidate["candidate_id"] not in selected_ids
                ),
                "audit_status": audit_status.value,
            },
        }

    @staticmethod
    def _audit(context: StageContext) -> dict[str, Any]:
        dependency = context.dependency_results.get("CANDIDATE_AUDIT")
        value = dependency.get("candidate_audit") if dependency else None
        if not isinstance(value, dict):
            raise ContractValidationError(
                "COMMIT_RESULT requires CANDIDATE_AUDIT output"
            )
        return value

    @staticmethod
    def _audit_status(audit: dict[str, Any]) -> AuditStatus:
        value = audit.get("status")
        if not isinstance(value, str):
            raise ContractValidationError("COMMIT_RESULT audit status is invalid")
        try:
            return AuditStatus(value)
        except ValueError as error:
            raise ContractValidationError("COMMIT_RESULT audit status is invalid") from error

    @staticmethod
    def _rank_key(candidate: dict[str, Any]) -> int:
        rank = candidate.get("rank")
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
            raise ContractValidationError(
                "COMMIT_RESULT reviewable candidate requires a positive rank"
            )
        return rank

    @staticmethod
    def _select_candidates(
        reviewable: list[dict[str, Any]],
        preference: CafeTypePreference,
    ) -> list[dict[str, Any]]:
        selected = list(reviewable[:3])
        if preference == CafeTypePreference.OPEN_TO_BOTH:
            independent = next(
                (
                    candidate
                    for candidate in reviewable
                    if candidate.get("case_type") == "INDEPENDENT"
                ),
                None,
            )
            franchise = next(
                (
                    candidate
                    for candidate in reviewable
                    if candidate.get("case_type") == "FRANCHISE"
                ),
                None,
            )
            if independent is not None and franchise is not None:
                required_ids = {
                    independent["candidate_id"],
                    franchise["candidate_id"],
                    reviewable[0]["candidate_id"],
                }
                selected = [
                    candidate
                    for candidate in reviewable
                    if candidate["candidate_id"] in required_ids
                ]
                selected_ids = {candidate["candidate_id"] for candidate in selected}
                selected.extend(
                    candidate
                    for candidate in reviewable
                    if candidate["candidate_id"] not in selected_ids
                    and len(selected) < 3
                )

        normalized: list[dict[str, Any]] = []
        for rank, candidate in enumerate(selected[:3], start=1):
            projected = dict(candidate)
            projected["rank"] = rank
            projected["is_primary_next_review"] = rank == 1
            normalized.append(projected)
        return normalized

    @staticmethod
    def _validate_candidate_set(
        candidates: list[dict[str, Any]],
        reviewable: list[dict[str, Any]],
    ) -> None:
        candidate_ids = [candidate["candidate_id"] for candidate in candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ContractValidationError(
                "COMMIT_RESULT candidate identifiers must be unique"
            )
        ranks = [candidate["rank"] for candidate in reviewable]
        if ranks != list(range(1, len(reviewable) + 1)):
            raise ContractValidationError(
                "COMMIT_RESULT candidates require a contiguous rank before truncation"
            )
        primary = [
            candidate
            for candidate in reviewable
            if candidate.get("is_primary_next_review") is True
        ]
        if (
            reviewable
            and (
                len(primary) != 1
                or primary[0].get("rank") != 1
                or primary[0].get("candidate_id") != reviewable[0].get("candidate_id")
            )
        ):
            raise ContractValidationError(
                "COMMIT_RESULT requires exactly rank 1 as the primary candidate"
            )

    @staticmethod
    def _validate_audit_consistency(
        audit: dict[str, Any],
        audit_status: AuditStatus,
        candidates: list[object],
    ) -> None:
        candidate_audits = audit.get("candidate_audits")
        if not isinstance(candidate_audits, list):
            raise ContractValidationError("COMMIT_RESULT audit findings are invalid")
        expected_ids = {
            candidate.get("candidate_id")
            for candidate in candidates
            if isinstance(candidate, dict)
            and isinstance(candidate.get("candidate_id"), str)
        }
        audited_ids = [
            candidate_audit.get("candidate_id")
            for candidate_audit in candidate_audits
            if isinstance(candidate_audit, dict)
            and isinstance(candidate_audit.get("candidate_id"), str)
        ]
        if audit_status != AuditStatus.UNAVAILABLE and (
            len(audited_ids) != len(set(audited_ids))
            or set(audited_ids) != expected_ids
        ):
            raise ContractValidationError(
                "COMMIT_RESULT Candidate Audit coverage is incomplete"
            )
        if audit_status == AuditStatus.UNAVAILABLE and candidate_audits:
            raise ContractValidationError(
                "COMMIT_RESULT unavailable audit cannot contain candidate findings"
            )
        serious = False
        for candidate_audit in candidate_audits:
            if not isinstance(candidate_audit, dict):
                raise ContractValidationError("COMMIT_RESULT candidate audit is invalid")
            if candidate_audit.get("status") == "INVALID_INPUT":
                raise ContractValidationError(
                    "COMMIT_RESULT cannot commit invalid Candidate Audit input"
                )
            if candidate_audit.get("status") == "REQUIRES_HUMAN":
                serious = True
            findings = candidate_audit.get("findings")
            if not isinstance(findings, list):
                raise ContractValidationError("COMMIT_RESULT audit findings are invalid")
            serious = serious or any(
                isinstance(finding, dict)
                and (
                    finding.get("severity") in {"HIGH", "CRITICAL"}
                    or finding.get("disposition") == "REQUIRE_HUMAN"
                )
                for finding in findings
            )
        if serious and audit_status == AuditStatus.PASSED:
            raise ContractValidationError(
                "COMMIT_RESULT cannot hide serious Candidate Audit findings"
            )
