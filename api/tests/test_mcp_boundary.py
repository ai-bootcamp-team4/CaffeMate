from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.mcp.result_validation import validate_mcp_result
from app.mcp.scope import ScopeClaims, ScopeTokenError, ScopeTokenSigner, digest_head
from app.workflows.models import HeadFence

SECRET = "scope-secret-that-is-longer-than-thirty-two-bytes"


class MutableClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 21, 10, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def head() -> HeadFence:
    return HeadFence(
        workflow_generation=1,
        state_version=2,
        founder_snapshot_id="founder-1",
        area_snapshot_id="area-1",
        evidence_snapshot_id="evidence-1",
        policy_snapshot_id="policy-1",
        index_generation_id="index-1",
        seed_registry_id="seed-1",
    )


def signer(clock: MutableClock) -> ScopeTokenSigner:
    return ScopeTokenSigner(
        secret=SECRET,
        issuer="caffemate-control-api",
        audience="caffemate-mcp",
        clock=clock,
        jti_factory=lambda: "jti-1",
    )


def scope(clock: MutableClock | None = None) -> ScopeClaims:
    clock = clock or MutableClock()
    token = signer(clock).issue(
        venture_project_id="project-1",
        workflow_run_id="workflow-1",
        head=head(),
    )
    return signer(clock).verify(token)


def resolve_area_result(
    *,
    project_id: str = "project-1",
    request_id: str = "request-1",
    status: str = "OK",
) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "request_id": request_id,
        "tool_name": "resolve_area",
        "tool_version": "1.0.0",
        "status": status,
        "project_id": project_id,
        "evidence_records": [],
        "missing_fields": [],
        "conflicts": [],
        "source_trace": [],
        "error_codes": [],
        "observed_at": "2026-08-21T10:00:00Z",
        "data": [
            {
                "administrative_code": "4111755000",
                "display_name": "원천동",
                "boundary_version": "2026-01",
                "match_kind": "EXACT",
            }
        ],
    }


def validate_result(
    value: dict[str, object],
    claims: ScopeClaims,
    *,
    meta_tool_version: str | None = "1.0.0",
    is_error: bool = False,
):
    return validate_mcp_result(
        structured_content=value,
        scope=claims,
        expected_request_id="request-1",
        expected_tool_name="resolve_area",
        expected_tool_version="1.0.0",
        meta_tool_version=meta_tool_version,
        is_error=is_error,
    )


def test_scope_token_contains_project_workflow_and_full_head_digest() -> None:
    clock = MutableClock()
    claims = scope(clock)

    assert claims.iss == "caffemate-control-api"
    assert claims.aud == "caffemate-mcp"
    assert claims.venture_project_id == "project-1"
    assert claims.workflow_run_id == "workflow-1"
    assert claims.full_head_digest == digest_head(head())
    assert claims.exp - claims.iat == 300
    assert claims.jti == "jti-1"


def test_scope_token_rejects_ttl_over_five_minutes() -> None:
    clock = MutableClock()
    with pytest.raises(ValueError, match="300"):
        signer(clock).issue(
            venture_project_id="project-1",
            workflow_run_id="workflow-1",
            head=head(),
            ttl_seconds=301,
        )


def test_scope_token_rejects_expiration_and_signature_tampering() -> None:
    clock = MutableClock()
    token = signer(clock).issue(
        venture_project_id="project-1",
        workflow_run_id="workflow-1",
        head=head(),
        ttl_seconds=30,
    )
    clock.now += timedelta(seconds=30)

    with pytest.raises(ScopeTokenError, match="Expired"):
        signer(clock).verify(token)
    with pytest.raises(ScopeTokenError, match="Invalid"):
        signer(MutableClock()).verify(f"{token[:-1]}x")


def test_scope_token_rejects_wrong_audience() -> None:
    clock = MutableClock()
    claims = {
        "iss": "caffemate-control-api",
        "aud": "another-service",
        "venture_project_id": "project-1",
        "workflow_run_id": "workflow-1",
        "full_head_digest": digest_head(head()),
        "jti": "jti-1",
        "iat": int(clock.now.timestamp()),
        "exp": int(clock.now.timestamp()) + 60,
    }
    token = jwt.encode(claims, SECRET, algorithm="HS256")

    with pytest.raises(ScopeTokenError, match="Invalid"):
        signer(clock).verify(token)


def test_head_digest_changes_when_any_authoritative_dimension_changes() -> None:
    changed = head().model_copy(update={"state_version": 3})

    assert digest_head(changed) != digest_head(head())


def test_valid_mcp_result_is_accepted_and_complete() -> None:
    validation = validate_result(resolve_area_result(), scope())

    assert validation.accepted is True
    assert validation.is_complete is True
    assert validation.status == "OK"
    assert validation.errors == []


def test_partial_mcp_result_is_never_marked_complete() -> None:
    validation = validate_result(resolve_area_result(status="PARTIAL"), scope())

    assert validation.accepted is True
    assert validation.is_complete is False
    assert validation.status == "PARTIAL"


def test_cross_project_mcp_result_is_rejected() -> None:
    validation = validate_result(resolve_area_result(project_id="project-2"), scope())

    assert validation.accepted is False
    assert validation.is_complete is False
    assert [error.code for error in validation.errors] == ["MCP_PROJECT_SCOPE_MISMATCH"]


def test_nested_evidence_cannot_cross_project_boundary() -> None:
    result = resolve_area_result()
    result["evidence_records"] = [
        {
            "schema_version": "2.0.0",
            "evidence_id": "evidence-1",
            "project_id": "project-2",
            "claim_type": "AREA_RESOLUTION",
            "value": {"kind": "NULL", "value": None},
            "value_kind": "UNKNOWN",
            "geographic_scope": {
                "scope_type": "NOT_APPLICABLE",
                "scope_id": None,
                "boundary_version": None,
            },
            "source": {
                "title": "No source",
                "source_ref": "missing",
                "authority": "PRIMARY_OFFICIAL",
                "source_type": "API",
                "published_or_data_date": None,
                "source_observed_at": None,
            },
            "original_anchor": {"anchor_type": "API_ROW", "locator": "missing"},
            "freshness_status": "UNKNOWN",
            "conflict_status": "NONE",
            "retrieved_at": "2026-08-21T10:00:00Z",
            "missing_context": ["source unavailable"],
            "durable_evidence_refs": [],
        }
    ]

    validation = validate_result(result, scope())

    assert validation.accepted is False
    assert [error.code for error in validation.errors] == ["MCP_PROJECT_SCOPE_MISMATCH"]


def test_request_and_metadata_version_must_match_call() -> None:
    validation = validate_result(
        resolve_area_result(request_id="another-request"),
        scope(),
        meta_tool_version=None,
    )

    assert validation.accepted is False
    assert {error.code for error in validation.errors} == {
        "MCP_REQUEST_ID_MISMATCH",
        "MCP_TOOL_CONTRACT_MISMATCH",
    }


def test_is_error_and_schema_invalid_results_are_rejected() -> None:
    transport_error = validate_result(resolve_area_result(), scope(), is_error=True)
    invalid = resolve_area_result()
    invalid["data"] = [{"display_name": "missing required fields"}]
    schema_error = validate_result(invalid, scope())

    assert [error.code for error in transport_error.errors] == ["MCP_TRANSPORT_ERROR"]
    assert [error.code for error in schema_error.errors] == ["MCP_TOOL_CONTRACT_MISMATCH"]


def test_domain_error_status_is_valid_but_not_accepted() -> None:
    result = resolve_area_result(status="ERROR")
    result["error_codes"] = ["CONNECTOR_ERROR"]

    validation = validate_result(result, scope())

    assert validation.accepted is False
    assert validation.is_complete is False
    assert [error.code for error in validation.errors] == ["MCP_DOMAIN_ERROR"]
