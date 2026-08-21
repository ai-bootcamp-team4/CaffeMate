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
            _collect_named_strings(
                task_payload,
                {
                    "candidate_id",
                    "candidate_ids",
                    "candidate_ref",
                    "candidate_refs",
                    "current_candidate_refs",
                },
            ),
            _collect_named_strings(
                result_payload,
                {"candidate_id", "candidate_ids", "candidate_ref", "candidate_refs"},
            ),
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
