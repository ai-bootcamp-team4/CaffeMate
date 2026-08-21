from typing import Any

from pydantic import Field

from app.contracts.schema_registry import AgentContractValidator, ContractRegistry
from app.domain.errors import ContractValidationError
from app.domain.models import StrictModel
from app.workflows.models import HeadFence


class BoundaryError(StrictModel):
    code: str = Field(min_length=1)
    json_pointer: str
    message: str = Field(min_length=1)


class BoundaryValidation(StrictModel):
    accepted: bool
    errors: list[BoundaryError]


_ECHO_FIELDS = (
    "task_id",
    "invocation_id",
    "agent_name",
    "task_type",
    "workflow_run_id",
    "stage_run_id",
    "venture_project_id",
    "input_digest",
    "output_schema_id",
)


def validate_agent_boundary(
    *,
    task: dict[str, Any],
    result: dict[str, Any],
    current_head: HeadFence,
    contracts: AgentContractValidator | None = None,
) -> BoundaryValidation:
    validator = contracts or ContractRegistry()
    schema_error = _validate_contracts(validator, task, result)
    if schema_error is not None:
        return BoundaryValidation(accepted=False, errors=[schema_error])

    errors: list[BoundaryError] = []
    mismatched_echoes = [field for field in _ECHO_FIELDS if task[field] != result[field]]
    if mismatched_echoes:
        errors.append(
            BoundaryError(
                code="TASK_ECHO_MISMATCH",
                json_pointer="/",
                message=f"Result changed task identity fields: {', '.join(mismatched_echoes)}",
            )
        )
    if task["head_fence"] != result["head_fence_seen"]:
        errors.append(
            BoundaryError(
                code="FENCE_ECHO_MISMATCH",
                json_pointer="/head_fence_seen",
                message="Result did not echo the task full head fence",
            )
        )
    if task["head_fence"] != current_head.model_dump(mode="json"):
        errors.append(
            BoundaryError(
                code="CURRENT_HEAD_MISMATCH",
                json_pointer="/head_fence",
                message="Task full head is stale against the authoritative current head",
            )
        )

    errors.extend(_validate_allocated_ids(task, result))
    errors.extend(_validate_supported_references(task, result))
    errors.extend(_validate_evidence_coverage_kinds(task, result))
    errors.extend(_validate_money_ranges(result))
    errors.extend(_validate_evidence_assessment_metadata(task, result))
    return BoundaryValidation(accepted=not errors, errors=errors)


def _validate_contracts(
    contracts: AgentContractValidator,
    task: dict[str, Any],
    result: dict[str, Any],
) -> BoundaryError | None:
    try:
        contracts.validate_agent_task(task)
        contracts.validate_agent_task_result(result)
    except ContractValidationError as error:
        return BoundaryError(
            code="CONTRACT_SCHEMA_INVALID",
            json_pointer="/",
            message=str(error),
        )
    return None


def _validate_allocated_ids(
    task: dict[str, Any],
    result: dict[str, Any],
) -> list[BoundaryError]:
    task_type = task["task_type"]
    task_payload = task["payload"]
    result_payload = result["payload"] or {}
    allocation_keys = {
        "INTENT_DELTA": ("operation_id_pool", "op_id"),
        "EVIDENCE_PLAN": ("action_id_pool", "action_id"),
        "PROPOSE_INDEPENDENT": ("proposal_id", "proposal_id"),
        "PROPOSE_FRANCHISE": ("proposal_id", "proposal_id"),
        "DOCUMENT_EXTRACT": ("claim_id_pool", "claim_id"),
    }
    keys = allocation_keys.get(task_type)
    if keys is None:
        return []
    pool_key, output_key = keys
    allocated = _collect_named_strings(task_payload, {pool_key})
    produced = _collect_named_strings(result_payload, {output_key})
    unsupported = produced - allocated
    if not unsupported:
        return []
    return [
        BoundaryError(
            code="UNALLOCATED_OUTPUT_ID",
            json_pointer="/payload",
            message=f"Output used unallocated ids: {', '.join(sorted(unsupported))}",
        )
    ]


def _validate_supported_references(
    task: dict[str, Any],
    result: dict[str, Any],
) -> list[BoundaryError]:
    task_payload = task["payload"]
    result_payload = result["payload"] or {}
    evidence_records = _collect_evidence_records(task_payload)
    supported_candidate_refs = (
        set(evidence_records)
        if task["task_type"] == "EVIDENCE_ASSESS"
        else _collect_named_strings(
            task_payload,
            {
                "candidate_id",
                "candidate_ids",
                "candidate_ref",
                "candidate_refs",
                "current_candidate_refs",
            },
        )
    )
    supported_assumption_refs = _collect_named_strings(
        task_payload,
        {"assumption_refs", "support_refs"},
    )
    supported_assumption_refs.update(
        evidence_id
        for evidence_id, evidence in evidence_records.items()
        if evidence.get("value_kind") in {"DECLARED_ASSUMPTION", "UNKNOWN"}
    )
    checks = (
        (
            "evidence",
            _collect_named_strings(
                task_payload,
                {"evidence_id", "evidence_ids", "evidence_refs", "support_refs"},
            ),
            _collect_named_strings(
                result,
                {"evidence_refs", "support_refs"},
            ),
        ),
        (
            "claim",
            _collect_named_strings(
                task_payload,
                {"claim_id", "claim_ids", "claim_id_pool", "claim_refs"},
            ),
            _collect_named_strings(
                result,
                {"claim_id", "claim_ids", "missing_claim_ids", "claim_refs"},
            ),
        ),
        (
            "candidate",
            supported_candidate_refs,
            _collect_named_strings(
                result_payload,
                {"candidate_id", "candidate_ids", "candidate_ref", "candidate_refs"},
            ),
        ),
        (
            "assumption",
            supported_assumption_refs,
            _collect_named_strings(result_payload, {"assumption_refs"}),
        ),
    )
    errors: list[BoundaryError] = []
    for reference_kind, supported, referenced in checks:
        unsupported = referenced - supported
        if unsupported:
            errors.append(
                BoundaryError(
                    code="UNSUPPORTED_REFERENCE",
                    json_pointer="/payload",
                    message=(
                        f"Output used unsupported {reference_kind} refs: "
                        f"{', '.join(sorted(unsupported))}"
                    ),
                )
            )
    return errors


def _validate_evidence_coverage_kinds(
    task: dict[str, Any],
    result: dict[str, Any],
) -> list[BoundaryError]:
    evidence_records = _collect_evidence_records(task["payload"])
    coverage_refs = _collect_named_strings(result, {"evidence_refs", "support_refs"})
    if task["task_type"] == "EVIDENCE_ASSESS":
        result_payload = result.get("payload") or {}
        for assessment in result_payload.get("assessments", []):
            if not isinstance(assessment, dict):
                continue
            if assessment.get("relation") not in {"SUPPORTS", "CONTRADICTS"}:
                continue
            candidate_ref = assessment.get("candidate_ref")
            if isinstance(candidate_ref, str):
                coverage_refs.add(candidate_ref)

    forbidden = sorted(
        reference
        for reference in coverage_refs
        if evidence_records.get(reference, {}).get("value_kind")
        in {"DECLARED_ASSUMPTION", "UNKNOWN"}
    )
    if not forbidden:
        return []
    return [
        BoundaryError(
            code="ASSUMPTION_USED_AS_EVIDENCE",
            json_pointer="/payload",
            message=(
                "Assumption or unknown ids were used as evidence coverage: "
                f"{', '.join(forbidden)}"
            ),
        )
    ]


def _validate_money_ranges(result: dict[str, Any]) -> list[BoundaryError]:
    invalid_paths = _find_non_monotonic_money_ranges(result.get("payload") or {}, "/payload")
    if not invalid_paths:
        return []
    return [
        BoundaryError(
            code="MONEY_RANGE_NON_MONOTONIC",
            json_pointer=invalid_paths[0],
            message="Known money range must satisfy low <= base <= high",
        )
    ]


def _validate_evidence_assessment_metadata(
    task: dict[str, Any],
    result: dict[str, Any],
) -> list[BoundaryError]:
    if task["task_type"] != "EVIDENCE_ASSESS" or result.get("payload") is None:
        return []

    task_payload = task["payload"]
    result_payload = result["payload"]
    claims = {
        claim["claim_id"]: claim
        for claim in task_payload.get("claims", [])
        if isinstance(claim, dict) and isinstance(claim.get("claim_id"), str)
    }
    evidence_records = _collect_evidence_records(task_payload)
    errors: list[BoundaryError] = []
    for index, assessment in enumerate(result_payload.get("assessments", [])):
        if not isinstance(assessment, dict):
            continue
        claim = claims.get(assessment.get("claim_id"))
        candidate_ref = assessment.get("candidate_ref")
        evidence = evidence_records.get(candidate_ref) if isinstance(candidate_ref, str) else None
        if claim is None or evidence is None:
            continue

        if assessment.get("scope_status") == "MATCH" and _scopes_definitely_mismatch(
            claim.get("geographic_scope"),
            evidence.get("geographic_scope"),
        ):
            errors.append(
                BoundaryError(
                    code="EVIDENCE_SCOPE_OR_DATE_INVALID",
                    json_pointer=f"/payload/assessments/{index}/scope_status",
                    message="MATCH contradicts the structured geographic scope ids",
                )
            )

        evidence_freshness = evidence.get("freshness_status")
        if assessment.get("freshness_status") == "FRESH" and evidence_freshness != "FRESH":
            errors.append(
                BoundaryError(
                    code="EVIDENCE_SCOPE_OR_DATE_INVALID",
                    json_pointer=f"/payload/assessments/{index}/freshness_status",
                    message=(
                        "FRESH contradicts EvidenceRecord "
                        f"freshness_status={evidence_freshness}"
                    ),
                )
            )

        if (
            assessment.get("date_status") == "MATCH"
            and claim.get("required_freshness") is not None
            and evidence_freshness != "FRESH"
        ):
            errors.append(
                BoundaryError(
                    code="EVIDENCE_SCOPE_OR_DATE_INVALID",
                    json_pointer=f"/payload/assessments/{index}/date_status",
                    message=(
                        "MATCH is not allowed when the claim requires freshness "
                        "and the EvidenceRecord is not FRESH"
                    ),
                )
            )
    return errors


def _collect_evidence_records(value: Any) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if isinstance(value, dict):
        evidence_id = value.get("evidence_id")
        value_kind = value.get("value_kind")
        if isinstance(evidence_id, str) and isinstance(value_kind, str):
            records[evidence_id] = value
        for child in value.values():
            records.update(_collect_evidence_records(child))
    elif isinstance(value, list):
        for child in value:
            records.update(_collect_evidence_records(child))
    return records


def _find_non_monotonic_money_ranges(value: Any, path: str) -> list[str]:
    invalid: list[str] = []
    if isinstance(value, dict):
        if value.get("kind") == "MONEY_RANGE":
            low = value.get("low")
            base = value.get("base")
            high = value.get("high")
            if (
                isinstance(low, int | float)
                and not isinstance(low, bool)
                and isinstance(base, int | float)
                and not isinstance(base, bool)
                and isinstance(high, int | float)
                and not isinstance(high, bool)
            ):
                if not (low <= base <= high):
                    invalid.append(path)
        for key, child in value.items():
            invalid.extend(_find_non_monotonic_money_ranges(child, f"{path}/{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            invalid.extend(_find_non_monotonic_money_ranges(child, f"{path}/{index}"))
    return invalid


def _scopes_definitely_mismatch(expected: Any, actual: Any) -> bool:
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return False
    expected_type = expected.get("scope_type")
    actual_type = actual.get("scope_type")
    expected_id = expected.get("scope_id")
    actual_id = actual.get("scope_id")
    return (
        isinstance(expected_type, str)
        and expected_type == actual_type
        and isinstance(expected_id, str)
        and isinstance(actual_id, str)
        and expected_id != actual_id
    )


def _collect_named_strings(value: Any, keys: set[str]) -> set[str]:
    collected: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in keys:
                if isinstance(child, str):
                    collected.add(child)
                elif isinstance(child, list):
                    collected.update(item for item in child if isinstance(item, str))
            collected.update(_collect_named_strings(child, keys))
    elif isinstance(value, list):
        for child in value:
            collected.update(_collect_named_strings(child, keys))
    return collected
