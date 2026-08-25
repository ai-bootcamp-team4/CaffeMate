from datetime import UTC, datetime
from typing import Any

from app.agents.boundary import validate_agent_boundary
from app.agents.task_factory import AgentTaskFactory
from app.documents.demo import DemoFixtureDocumentRuntime, demo_parser_request
from app.documents.extraction import DocumentExtractionService
from app.documents.models import DocumentRevision, DocumentRevisionStatus, DocumentType
from app.workflows.models import HeadFence


class FailingDelegate:
    def invoke(self, _task: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("recognized demo fixtures must not call the external runtime")


def revision(
    filename: str = "05_demo_property_listing.pdf",
    document_type: DocumentType = DocumentType.PROPERTY_LISTING,
) -> DocumentRevision:
    now = datetime(2026, 8, 24, tzinfo=UTC)
    return DocumentRevision(
        document_id="document-1",
        document_revision_id="revision-1",
        project_id="project-1",
        revision_number=1,
        document_type=document_type,
        original_filename=filename,
        content_type="application/pdf",
        size_bytes=1_024,
        sha256="a" * 64,
        status=DocumentRevisionStatus.SCAN_PENDING,
        failure_codes=[],
        created_at=now,
        updated_at=now,
    )


def test_property_demo_fixture_extracts_editable_claims_without_runtime() -> None:
    parser_request = demo_parser_request(revision())

    assert parser_request is not None
    head = HeadFence(
        workflow_generation=1,
        state_version=1,
        founder_snapshot_id="founder-1",
        area_snapshot_id="area-1",
        evidence_snapshot_id="evidence-1",
        policy_snapshot_id="policy-1",
        index_generation_id="index-1",
        seed_registry_id="seed-1",
    )
    task = AgentTaskFactory(new_invocation_id=lambda: "invocation-1").build_document_extract(
        project_id="project-1",
        document_id="document-1",
        document_revision_id="revision-1",
        document_type="PROPERTY_LISTING",
        checksum="a" * 64,
        head=head,
        parser_blocks=[value.model_dump(mode="json") for value in parser_request.blocks],
        claim_types=[
            "ADDRESS",
            "AREA",
            "FLOOR",
            "LEASE_DEPOSIT",
            "MONTHLY_RENT",
            "MANAGEMENT_FEE",
            "KEY_MONEY",
        ],
        batch_index=0,
    )

    result = DemoFixtureDocumentRuntime(FailingDelegate()).invoke(task)

    assert validate_agent_boundary(task=task, result=result, current_head=head).accepted
    assert result["status"] == "COMPLETE"
    claims = result["payload"]["proposed_claims"]
    assert {value["predicate"] for value in claims} >= {
        "ADDRESS",
        "AREA",
        "LEASE_DEPOSIT",
        "MONTHLY_RENT",
        "MANAGEMENT_FEE",
        "KEY_MONEY",
    }
    assert all(value["extraction_status"] == "REVIEW_REQUIRED" for value in claims)


def test_demo_fixture_requires_matching_document_type() -> None:
    assert demo_parser_request(revision(document_type=DocumentType.FRANCHISE_DISCLOSURE)) is None


def test_franchise_demo_fixture_emits_percentage_royalty_as_numeric_claim() -> None:
    parser_request = demo_parser_request(
        revision(
            filename="02_demo_franchise_disclosure_summary.pdf",
            document_type=DocumentType.FRANCHISE_DISCLOSURE,
        )
    )

    assert parser_request is not None
    head = HeadFence(
        workflow_generation=1,
        state_version=1,
        founder_snapshot_id="founder-1",
        area_snapshot_id="area-1",
        evidence_snapshot_id="evidence-1",
        policy_snapshot_id="policy-1",
        index_generation_id="index-1",
        seed_registry_id="seed-1",
    )
    task = AgentTaskFactory(new_invocation_id=lambda: "invocation-royalty").build_document_extract(
        project_id="project-1",
        document_id="document-1",
        document_revision_id="revision-1",
        document_type="FRANCHISE_DISCLOSURE",
        checksum="a" * 64,
        head=head,
        parser_blocks=[value.model_dump(mode="json") for value in parser_request.blocks],
        claim_types=["ROYALTY"],
        batch_index=0,
    )

    result = DemoFixtureDocumentRuntime(FailingDelegate()).invoke(task)
    royalty = next(
        value
        for value in result["payload"]["proposed_claims"]
        if value["predicate"] == "ROYALTY"
    )

    assert royalty["typed_value"] == {"kind": "DECIMAL", "value": 3.0}
    assert royalty["unit"] == "%"


def test_property_demo_fixture_uses_user_facing_field_labels() -> None:
    form = DocumentExtractionService._build_form(
        form_id="form-1",
        project_id="project-1",
        document_id="document-1",
        document_revision_id="revision-1",
        expected_state_version=1,
        claim_types=["ADDRESS", "AREA", "FLOOR"],
        claims=[],
        unresolved={"ADDRESS", "AREA", "FLOOR"},
        document_risk_flags=set(),
    )

    assert [field.label for field in form.fields] == ["점포 주소", "면적", "층"]
