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
    errors.extend(_validate_evidence_plan(task, result))
    errors.extend(_validate_evidence_coverage_kinds(task, result))
    errors.extend(_validate_money_ranges(result))
    errors.extend(_validate_evidence_assessment_metadata(task, result))
    errors.extend(_validate_proposal_semantics(task, result))
    return BoundaryValidation(accepted=not errors, errors=errors)


def _validate_evidence_plan(
    task: dict[str, Any],
    result: dict[str, Any],
) -> list[BoundaryError]:
    if task["task_type"] != "EVIDENCE_PLAN" or result["status"] != "COMPLETE":
        return []
    task_payload = task["payload"]
    result_payload = result["payload"]
    if not isinstance(task_payload, dict) or not isinstance(result_payload, dict):
        return []
    claims = {
        claim["claim_id"]: claim
        for claim in task_payload.get("claims", [])
        if isinstance(claim, dict) and isinstance(claim.get("claim_id"), str)
    }
    plans = result_payload.get("claim_plans", [])
    plan_claim_ids = [
        plan.get("claim_id") for plan in plans if isinstance(plan, dict)
    ]
    errors: list[BoundaryError] = []
    if len(plan_claim_ids) != len(set(plan_claim_ids)) or set(plan_claim_ids) != set(claims):
        errors.append(
            BoundaryError(
                code="EVIDENCE_PLAN_CLAIM_COVERAGE_INVALID",
                json_pointer="/payload/claim_plans",
                message="Complete plan must cover every input claim exactly once",
            )
        )

    constraints = task_payload.get("planning_constraints", {})
    allowed_tools = set(constraints.get("allowed_tools", []))
    max_total = constraints.get("max_total_actions")
    max_per_claim = constraints.get("max_actions_per_claim")
    catalog = {
        item["tool_name"]: item["tool_version"]
        for item in task.get("available_tool_catalog", [])
        if isinstance(item, dict)
        and isinstance(item.get("tool_name"), str)
        and isinstance(item.get("tool_version"), str)
    }
    actions: list[tuple[dict[str, Any], str, str]] = []
    for plan in plans:
        if not isinstance(plan, dict) or not isinstance(plan.get("claim_id"), str):
            continue
        action_groups = (
            ("support_actions", "SUPPORT"),
            ("counter_actions", "COUNTER"),
        )
        for collection, polarity in action_groups:
            for action in plan.get(collection, []):
                if isinstance(action, dict):
                    actions.append((action, plan["claim_id"], polarity))

    action_ids = [action.get("action_id") for action, _, _ in actions]
    if len(action_ids) != len(set(action_ids)):
        errors.append(
            BoundaryError(
                code="EVIDENCE_PLAN_ACTION_DUPLICATED",
                json_pointer="/payload/claim_plans",
                message="Planned action ids must be globally unique",
            )
        )
    if isinstance(max_total, int) and len(actions) > max_total:
        errors.append(
            BoundaryError(
                code="EVIDENCE_PLAN_ACTION_LIMIT_EXCEEDED",
                json_pointer="/payload/claim_plans",
                message="Planned action count exceeded the backend limit",
            )
        )

    counts: dict[str, int] = {}
    for action, plan_claim_id, expected_polarity in actions:
        counts[plan_claim_id] = counts.get(plan_claim_id, 0) + 1
        tool_name = action.get("tool_name")
        if tool_name not in allowed_tools or catalog.get(tool_name) != action.get("tool_version"):
            errors.append(
                BoundaryError(
                    code="EVIDENCE_PLAN_TOOL_NOT_ALLOWED",
                    json_pointer="/payload/claim_plans",
                    message="Planned tool is not in the pinned allowed catalog",
                )
            )
        claim = claims.get(plan_claim_id)
        if (
            action.get("claim_id") != plan_claim_id
            or action.get("polarity") != expected_polarity
            or claim is None
            or action.get("scope_constraints") != claim.get("geographic_scope")
        ):
            errors.append(
                BoundaryError(
                    code="EVIDENCE_PLAN_ACTION_CONTEXT_MISMATCH",
                    json_pointer="/payload/claim_plans",
                    message="Action claim, polarity, or geographic scope does not match its plan",
                )
            )
    if isinstance(max_per_claim, int) and any(
        count > max_per_claim for count in counts.values()
    ):
        errors.append(
            BoundaryError(
                code="EVIDENCE_PLAN_ACTION_LIMIT_EXCEEDED",
                json_pointer="/payload/claim_plans",
                message="Per-claim action count exceeded the backend limit",
            )
        )
    return errors


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


def _validate_proposal_semantics(
    task: dict[str, Any],
    result: dict[str, Any],
) -> list[BoundaryError]:
    task_type = task["task_type"]
    if task_type not in {"PROPOSE_INDEPENDENT", "PROPOSE_FRANCHISE"}:
        return []
    result_payload = result.get("payload")
    if not isinstance(result_payload, dict):
        return []
    task_payload = task["payload"]
    proposals = result_payload.get("candidate_proposals", [])
    if not isinstance(task_payload, dict) or not isinstance(proposals, list):
        return []
    source_key = "model_seeds" if task_type == "PROPOSE_INDEPENDENT" else "franchise_universe"
    sources = {
        value["proposal_id"]: value
        for value in task_payload.get(source_key, [])
        if isinstance(value, dict) and isinstance(value.get("proposal_id"), str)
    }
    requested_count = task_payload.get("requested_candidate_count")
    proposal_ids = [
        value.get("proposal_id") for value in proposals if isinstance(value, dict)
    ]
    errors: list[BoundaryError] = []
    if len(proposal_ids) != len(set(proposal_ids)):
        errors.append(
            BoundaryError(
                code="PROPOSAL_DUPLICATED",
                json_pointer="/payload/candidate_proposals",
                message="Proposal ids must be unique",
            )
        )
    if isinstance(requested_count, int) and len(proposals) > requested_count:
        errors.append(
            BoundaryError(
                code="PROPOSAL_COUNT_EXCEEDED",
                json_pointer="/payload/candidate_proposals",
                message="Agent returned more candidates than requested",
            )
        )
    evidence_ids = set(_collect_evidence_records(task_payload))
    result_evidence_refs = {
        value for value in result.get("evidence_refs", []) if isinstance(value, str)
    }
    if not result_evidence_refs.issubset(evidence_ids):
        errors.append(
            BoundaryError(
                code="PROPOSAL_EVIDENCE_REFERENCE_INVALID",
                json_pointer="/evidence_refs",
                message="Proposal result evidence refs must point to frozen Evidence records",
            )
        )
    for index, proposal in enumerate(proposals):
        if not isinstance(proposal, dict):
            continue
        path = f"/payload/candidate_proposals/{index}"
        source = sources.get(proposal.get("proposal_id"))
        if source is None:
            continue
        expected_source_id = source.get(
            "model_id" if task_type == "PROPOSE_INDEPENDENT" else "brand_id"
        )
        if proposal.get("seed_or_brand_id") != expected_source_id:
            errors.append(
                BoundaryError(
                    code=(
                        "SEED_REFERENCE_MISMATCH"
                        if task_type == "PROPOSE_INDEPENDENT"
                        else "BRAND_REFERENCE_MISMATCH"
                    ),
                    json_pointer=f"{path}/seed_or_brand_id",
                    message="Proposal source does not match its allocated input",
                )
            )
        if proposal.get("display_name") != source.get("display_name"):
            errors.append(
                BoundaryError(
                    code="PROPOSAL_DISPLAY_NAME_MISMATCH",
                    json_pointer=f"{path}/display_name",
                    message="Proposal display name must preserve its registered source",
                )
            )
        referenced_evidence = {
            value for value in proposal.get("evidence_refs", []) if isinstance(value, str)
        }
        if not referenced_evidence.issubset(evidence_ids):
            errors.append(
                BoundaryError(
                    code="PROPOSAL_EVIDENCE_REFERENCE_INVALID",
                    json_pointer=f"{path}/evidence_refs",
                    message="Proposal evidence refs must point to frozen Evidence records",
                )
            )
        if task_type == "PROPOSE_FRANCHISE":
            required_evidence = {
                value for value in source.get("evidence_refs", []) if isinstance(value, str)
            }
            required_missing = {
                value for value in source.get("missing_fields", []) if isinstance(value, str)
            }
            proposal_missing = {
                value for value in proposal.get("missing_fields", []) if isinstance(value, str)
            }
            if not required_evidence.issubset(referenced_evidence):
                errors.append(
                    BoundaryError(
                        code="FRANCHISE_ELIGIBILITY_EVIDENCE_DROPPED",
                        json_pointer=f"{path}/evidence_refs",
                        message="Franchise proposal dropped its eligibility Evidence",
                    )
                )
            if not required_missing.issubset(proposal_missing):
                errors.append(
                    BoundaryError(
                        code="FRANCHISE_MISSING_CONTEXT_DROPPED",
                        json_pointer=f"{path}/missing_fields",
                        message="Franchise proposal dropped required missing context",
                    )
                )
        else:
            required_assumptions = {
                value for value in source.get("support_refs", []) if isinstance(value, str)
            }
            proposal_assumptions = {
                value for value in proposal.get("assumption_refs", []) if isinstance(value, str)
            }
            if not required_assumptions.issubset(proposal_assumptions):
                errors.append(
                    BoundaryError(
                        code="SEED_ASSUMPTION_REFERENCE_DROPPED",
                        json_pointer=f"{path}/assumption_refs",
                        message="Independent proposal dropped its seed assumptions",
                    )
                )
            errors.extend(_validate_independent_parameters(source, proposal, path))
    return errors


def _validate_independent_parameters(
    source: dict[str, Any],
    proposal: dict[str, Any],
    path: str,
) -> list[BoundaryError]:
    allowed = {
        value["field_path"]: value
        for value in source.get("allowed_parameters", [])
        if isinstance(value, dict) and isinstance(value.get("field_path"), str)
    }
    errors: list[BoundaryError] = []
    for index, parameter in enumerate(proposal.get("adjusted_parameters", [])):
        if not isinstance(parameter, dict):
            continue
        parameter_path = f"{path}/adjusted_parameters/{index}"
        contract = allowed.get(parameter.get("field_path"))
        if contract is None:
            errors.append(
                BoundaryError(
                    code="PARAMETER_FIELD_NOT_ALLOWED",
                    json_pointer=f"{parameter_path}/field_path",
                    message="Adjusted parameter is outside the selected seed contract",
                )
            )
            continue
        typed_value = parameter.get("value")
        if not isinstance(typed_value, dict) or typed_value.get("kind") != contract.get(
            "value_kind"
        ):
            errors.append(
                BoundaryError(
                    code="PARAMETER_VALUE_KIND_INVALID",
                    json_pointer=f"{parameter_path}/value",
                    message="Adjusted parameter kind differs from its seed contract",
                )
            )
        if parameter.get("unit") != contract.get("unit"):
            errors.append(
                BoundaryError(
                    code="PARAMETER_UNIT_INVALID",
                    json_pointer=f"{parameter_path}/unit",
                    message="Adjusted parameter unit differs from its seed contract",
                )
            )
        numeric_values = _typed_numeric_values(typed_value)
        minimum = contract.get("minimum")
        maximum = contract.get("maximum")
        if any(
            (isinstance(minimum, (int, float)) and value < minimum)
            or (isinstance(maximum, (int, float)) and value > maximum)
            for value in numeric_values
        ):
            errors.append(
                BoundaryError(
                    code="PARAMETER_RANGE_INVALID",
                    json_pointer=f"{parameter_path}/value",
                    message="Adjusted parameter is outside its seed range",
                )
            )
    return errors


def _typed_numeric_values(value: object) -> list[float]:
    if not isinstance(value, dict):
        return []
    kind = value.get("kind")
    if kind in {"INTEGER", "DECIMAL"}:
        number = value.get("value")
        return [float(number)] if isinstance(number, (int, float)) else []
    if kind == "MONEY_RANGE":
        return [
            float(value[key])
            for key in ("low", "base", "high")
            if isinstance(value.get(key), (int, float))
        ]
    return []


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
