"""사용자가 확정한 문서 값은 상태에 반영한 뒤 제안을 한 번 다시 계산한다."""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import uuid4

import rfc8785
from sqlalchemy import Engine, text

from app.agents.boundary import validate_agent_boundary
from app.agents.task_factory import AgentTaskFactory
from app.documents.models import (
    AppliedDocumentClaim,
    ApplyExtractionFormRequest,
    DocumentClaimConflict,
    DocumentExtractionForm,
    DocumentRevisionStatus,
    EditStatus,
    ExtractionField,
    ExtractionFormApplication,
    ExtractionStatus,
    ParserResultRequest,
    UpdateExtractionFormRequest,
)
from app.domain.errors import (
    ContractValidationError,
    DocumentNotFoundError,
    DocumentPreconditionError,
    PersistenceUnavailableError,
)
from app.domain.events import DocumentClaimsApplied
from app.domain.models import VentureState
from app.domain.reducer import reduce_venture_state
from app.workflows.models import HeadFence
from app.workflows.selective_start import start_selective_first_proposal


class DocumentAgentRuntime(Protocol):
    def invoke(self, task: dict[str, Any]) -> dict[str, Any]: ...


CLAIM_TYPES: dict[str, tuple[str, ...]] = {
    "COMMERCIAL_LEASE": (
        "LEASE_DEPOSIT",
        "MONTHLY_RENT",
        "MANAGEMENT_FEE",
        "KEY_MONEY",
        "LEASE_TERM",
        "AREA",
        "FLOOR",
        "TERMINATION_CONDITION",
        "RESTORATION_OBLIGATION",
    ),
    "FRANCHISE_DISCLOSURE": (
        "FRANCHISE_FEE",
        "EDUCATION_FEE",
        "FRANCHISE_DEPOSIT",
        "ROYALTY",
        "MANDATORY_PURCHASE",
        "AVERAGE_ANNUAL_SALES",
        "STORE_COUNT",
        "CLOSURE_COUNT",
    ),
    "FRANCHISE_AGREEMENT": (
        "FRANCHISE_FEE",
        "ROYALTY",
        "MANDATORY_PURCHASE",
        "TERRITORY_PROTECTION",
        "CONTRACT_PERIOD",
        "RENEWAL_CONDITION",
        "TERMINATION_CONDITION",
        "PENALTY",
        "TRANSFER_CONDITION",
    ),
    "INTERIOR_QUOTE": (
        "QUOTE_TOTAL",
        "VAT_STATUS",
        "INCLUDED_WORK",
        "EXCLUDED_WORK",
        "PAYMENT_SCHEDULE",
        "VALID_UNTIL",
    ),
    "EQUIPMENT_QUOTE": (
        "QUOTE_TOTAL",
        "VAT_STATUS",
        "EQUIPMENT_ITEM",
        "WARRANTY",
        "DELIVERY_COST",
        "VALID_UNTIL",
    ),
    "PROPERTY_LISTING": (
        "LEASE_DEPOSIT",
        "MONTHLY_RENT",
        "MANAGEMENT_FEE",
        "KEY_MONEY",
        "AREA",
        "FLOOR",
        "ADDRESS",
    ),
    "LOAN_TERMS": (
        "PRINCIPAL",
        "INTEREST_RATE",
        "LOAN_PERIOD",
        "REPAYMENT_METHOD",
        "COLLATERAL_REQUIREMENT",
    ),
    "BUSINESS_PROCEDURE": ("PROCEDURE_NAME", "AUTHORITY", "REQUIRED_DOCUMENT", "DUE_DATE"),
    "OTHER": ("MATERIAL_FACT",),
}

LABELS = {
    "ADDRESS": "점포 주소",
    "AREA": "면적",
    "FLOOR": "층",
    "LEASE_DEPOSIT": "보증금",
    "MONTHLY_RENT": "월세",
    "MANAGEMENT_FEE": "관리비",
    "KEY_MONEY": "권리금",
    "QUOTE_TOTAL": "견적 총액",
    "ROYALTY": "로열티",
    "MANDATORY_PURCHASE": "필수 구매 조건",
    "PENALTY": "위약 조건",
    "INTEREST_RATE": "금리",
}

HIGH_MATERIALITY = {
    "LEASE_DEPOSIT",
    "MONTHLY_RENT",
    "KEY_MONEY",
    "QUOTE_TOTAL",
    "ROYALTY",
    "MANDATORY_PURCHASE",
    "TERMINATION_CONDITION",
    "PENALTY",
    "PRINCIPAL",
    "INTEREST_RATE",
}


class DocumentExtractionService:
    def __init__(
        self,
        engine: Engine,
        runtime: DocumentAgentRuntime,
        *,
        task_factory: AgentTaskFactory | None = None,
        now: Callable[[], datetime] | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._engine = engine
        self._runtime = runtime
        self._tasks = task_factory or AgentTaskFactory()
        self._now = now or (lambda: datetime.now(UTC))
        self._new_id = new_id or (lambda: str(uuid4()))

    def accept_parser_result(
        self,
        *,
        document_revision_id: str,
        request: ParserResultRequest,
    ) -> DocumentExtractionForm:
        block_values = [block.model_dump(mode="json") for block in request.blocks]
        self._validate_blocks(document_revision_id, block_values)
        block_digest = f"sha256:{hashlib.sha256(rfc8785.dumps(block_values)).hexdigest()}"
        with self._engine.begin() as connection:
            context = (
                connection.execute(
                    text(
                        """
                    SELECT r.document_revision_id, r.document_id, r.project_id,
                           r.declared_sha256, r.status, d.document_type, d.owner_user_id,
                           p.current_state_version,
                           h.workflow_generation, h.state_version,
                           h.founder_snapshot_id, h.area_snapshot_id,
                           h.evidence_snapshot_id, h.policy_snapshot_id,
                           h.index_generation_id, h.seed_registry_id
                    FROM document_revisions r
                    JOIN documents d ON d.document_id=r.document_id
                    JOIN venture_projects p ON p.project_id=r.project_id
                    JOIN project_heads h ON h.project_id=r.project_id
                    WHERE r.document_revision_id=:revision_id
                      AND r.project_id=:project_id AND r.document_id=:document_id
                    FOR UPDATE OF r
                    """
                    ),
                    {
                        "revision_id": document_revision_id,
                        "project_id": request.project_id,
                        "document_id": request.document_id,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if context is None:
                raise DocumentNotFoundError("Document revision does not exist")
            existing_form = connection.execute(
                text(
                    "SELECT form_json FROM document_extraction_forms "
                    "WHERE document_revision_id=:revision_id"
                ),
                {"revision_id": document_revision_id},
            ).scalar_one_or_none()
            existing_blocks = connection.execute(
                text(
                    "SELECT block_digest FROM parser_block_sets "
                    "WHERE document_revision_id=:revision_id"
                ),
                {"revision_id": document_revision_id},
            ).scalar_one_or_none()
            if existing_blocks is not None and existing_blocks != block_digest:
                raise DocumentPreconditionError("Parser result changed for an immutable revision")
            if existing_form is not None:
                return DocumentExtractionForm.model_validate(existing_form)
            if context["status"] not in {
                DocumentRevisionStatus.READY_FOR_PARSING.value,
                DocumentRevisionStatus.PARSING.value,
            }:
                raise DocumentPreconditionError("Document is not ready for parser results")
            if existing_blocks is None:
                connection.execute(
                    text(
                        """
                        INSERT INTO parser_block_sets(
                            document_revision_id, project_id, parser_version, blocks_json,
                            block_digest, prompt_injection_flags, created_at
                        ) VALUES (
                            :revision_id, :project_id, :parser_version,
                            CAST(:blocks AS JSONB), :block_digest,
                            CAST(:flags AS JSONB), :created_at
                        )
                        """
                    ),
                    {
                        "revision_id": document_revision_id,
                        "project_id": request.project_id,
                        "parser_version": request.parser_version,
                        "blocks": json.dumps(block_values, separators=(",", ":")),
                        "block_digest": block_digest,
                        "flags": json.dumps(sorted(set(request.prompt_injection_flags))),
                        "created_at": self._now(),
                    },
                )
            connection.execute(
                text(
                    "UPDATE document_revisions SET status='PARSING', updated_at=:updated_at "
                    "WHERE document_revision_id=:revision_id"
                ),
                {"updated_at": self._now(), "revision_id": document_revision_id},
            )
            head = self._head(context)
            context_value = dict(context)

        claim_types = list(CLAIM_TYPES[context_value["document_type"]])
        tasks: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        claims: list[dict[str, Any]] = []
        unresolved: set[str] = set()
        risk_flags: set[str] = set(request.prompt_injection_flags)
        for batch_index, offset in enumerate(range(0, len(block_values), 12)):
            task = self._tasks.build_document_extract(
                project_id=request.project_id,
                document_id=request.document_id,
                document_revision_id=document_revision_id,
                document_type=context_value["document_type"],
                checksum=context_value["declared_sha256"],
                head=head,
                parser_blocks=block_values[offset : offset + 12],
                claim_types=claim_types,
                batch_index=batch_index,
            )
            result = self._runtime.invoke(task)
            boundary = validate_agent_boundary(task=task, result=result, current_head=head)
            if not boundary.accepted:
                codes = ",".join(error.code for error in boundary.errors)
                raise ContractValidationError(f"DOCUMENT_EXTRACT boundary rejected: {codes}")
            tasks.append(task)
            results.append(result)
            if result["status"] == "COMPLETE" and isinstance(result["payload"], dict):
                payload = result["payload"]
                claims.extend(payload["proposed_claims"])
                unresolved.update(payload["unresolved_fields"])
                risk_flags.update(payload["document_risk_flags"])
            else:
                unresolved.update(claim_types)
                risk_flags.update(result.get("reason_codes", []))
        form = self._build_form(
            form_id=self._new_id(),
            project_id=request.project_id,
            document_id=request.document_id,
            document_revision_id=document_revision_id,
            expected_state_version=context_value["current_state_version"],
            claim_types=claim_types,
            claims=claims,
            unresolved=unresolved,
            document_risk_flags=risk_flags,
        )
        form_digest = self._form_digest(form)
        form = form.model_copy(update={"form_digest": form_digest})
        form_value = form.model_dump(mode="json")
        with self._engine.begin() as connection:
            current = (
                connection.execute(
                    text(
                        """
                    SELECT p.current_state_version, r.status
                    FROM document_revisions r JOIN venture_projects p ON p.project_id=r.project_id
                    WHERE r.document_revision_id=:revision_id
                    FOR UPDATE OF r
                    """
                    ),
                    {"revision_id": document_revision_id},
                )
                .mappings()
                .one()
            )
            if (
                current["status"] != DocumentRevisionStatus.PARSING.value
                or current["current_state_version"] != form.expected_state_version
            ):
                raise DocumentPreconditionError("Document extraction became stale")
            connection.execute(
                text(
                    """
                    INSERT INTO document_extraction_forms(
                        form_id, project_id, owner_user_id, document_id,
                        document_revision_id, expected_state_version, form_json,
                        agent_tasks_json, agent_results_json, form_digest,
                        created_at, updated_at
                    ) VALUES (
                        :form_id, :project_id, :owner_user_id, :document_id,
                        :revision_id, :state_version, CAST(:form_json AS JSONB),
                        CAST(:tasks AS JSONB), CAST(:results AS JSONB), :form_digest,
                        :created_at, :updated_at
                    )
                    ON CONFLICT (document_revision_id) DO NOTHING
                    """
                ),
                {
                    "form_id": form.form_id,
                    "project_id": request.project_id,
                    "owner_user_id": context_value["owner_user_id"],
                    "document_id": request.document_id,
                    "revision_id": document_revision_id,
                    "state_version": form.expected_state_version,
                    "form_json": json.dumps(form_value, separators=(",", ":")),
                    "tasks": json.dumps(tasks, separators=(",", ":")),
                    "results": json.dumps(results, separators=(",", ":")),
                    "form_digest": form_digest,
                    "created_at": self._now(),
                    "updated_at": self._now(),
                },
            )
            connection.execute(
                text(
                    "UPDATE document_revisions SET status='EXTRACTION_READY', updated_at=:now "
                    "WHERE document_revision_id=:revision_id"
                ),
                {"now": self._now(), "revision_id": document_revision_id},
            )
        return form

    def get_form(
        self, *, project_id: str, user_id: str, document_revision_id: str
    ) -> DocumentExtractionForm:
        with self._engine.connect() as connection:
            value = connection.execute(
                text(
                    """
                    SELECT f.form_json FROM document_extraction_forms f
                    WHERE f.project_id=:project_id AND f.owner_user_id=:user_id
                      AND f.document_revision_id=:revision_id
                    """
                ),
                {
                    "project_id": project_id,
                    "user_id": user_id,
                    "revision_id": document_revision_id,
                },
            ).scalar_one_or_none()
        if value is None:
            raise DocumentNotFoundError("Document extraction form does not exist")
        return DocumentExtractionForm.model_validate(value)

    def update_form(
        self,
        *,
        project_id: str,
        user_id: str,
        document_revision_id: str,
        request: UpdateExtractionFormRequest,
    ) -> DocumentExtractionForm:
        edit_ids = [edit.field_id for edit in request.edits]
        if len(edit_ids) != len(set(edit_ids)):
            raise ContractValidationError("Extraction form edit field ids must be unique")
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    text(
                        """
                    SELECT f.form_json, f.expected_state_version,
                           p.current_state_version, r.status
                    FROM document_extraction_forms f
                    JOIN document_revisions r
                      ON r.document_revision_id=f.document_revision_id
                    JOIN venture_projects p ON p.project_id=f.project_id
                    WHERE f.project_id=:project_id AND f.owner_user_id=:user_id
                      AND f.document_revision_id=:revision_id
                    FOR UPDATE OF f
                    """
                    ),
                    {
                        "project_id": project_id,
                        "user_id": user_id,
                        "revision_id": document_revision_id,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise DocumentNotFoundError("Document extraction form does not exist")
            if (
                row["expected_state_version"] != request.expected_state_version
                or row["current_state_version"] != request.expected_state_version
                or row["status"] != DocumentRevisionStatus.EXTRACTION_READY.value
            ):
                raise DocumentPreconditionError("Extraction form State or revision is stale")
            form = DocumentExtractionForm.model_validate(row["form_json"])
            fields = {field.field_id: field for field in form.fields}
            if any(field_id not in fields for field_id in edit_ids):
                raise ContractValidationError("Extraction form edit referenced an unknown field")
            for edit in request.edits:
                field = fields[edit.field_id]
                fields[edit.field_id] = field.model_copy(
                    update={
                        "current_value": edit.value,
                        "edit_status": (
                            EditStatus.CLEARED if edit.value is None else EditStatus.EDITED
                        ),
                    }
                )
            updated_fields = [fields[field.field_id] for field in form.fields]
            updated = form.model_copy(
                update={
                    "fields": updated_fields,
                    "form_status": (
                        "HAS_UNRESOLVED"
                        if any(
                            field.current_value is None
                            and field.extraction_status == ExtractionStatus.UNRESOLVED
                            for field in updated_fields
                        )
                        else "READY"
                    ),
                },
                deep=True,
            )
            digest = self._form_digest(updated)
            updated = updated.model_copy(update={"form_digest": digest})
            value = updated.model_dump(mode="json")
            connection.execute(
                text(
                    """
                    UPDATE document_extraction_forms
                    SET form_json=CAST(:form_json AS JSONB), form_digest=:form_digest,
                        updated_at=:updated_at
                    WHERE document_revision_id=:revision_id
                    """
                ),
                {
                    "form_json": json.dumps(value, separators=(",", ":")),
                    "form_digest": digest,
                    "updated_at": self._now(),
                    "revision_id": document_revision_id,
                },
            )
            return updated

    def apply_form(
        self,
        *,
        project_id: str,
        user_id: str,
        document_revision_id: str,
        idempotency_key: str,
        request: ApplyExtractionFormRequest,
    ) -> ExtractionFormApplication:
        request_value = request.model_dump(mode="json")
        request_digest = hashlib.sha256(rfc8785.dumps(request_value)).digest()
        occurred_at = self._now()
        with self._engine.begin() as connection:
            replay = (
                connection.execute(
                    text(
                        """
                    SELECT request_digest, response_json
                    FROM document_form_applications
                    WHERE owner_user_id=:user_id AND project_id=:project_id
                      AND idempotency_key=:idempotency_key
                    """
                    ),
                    {
                        "user_id": user_id,
                        "project_id": project_id,
                        "idempotency_key": idempotency_key,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if replay is not None:
                if bytes(replay["request_digest"]) != request_digest:
                    raise DocumentPreconditionError(
                        "Document apply idempotency key was reused with another request"
                    )
                return ExtractionFormApplication.model_validate(replay["response_json"])

            row = (
                connection.execute(
                    text(
                        """
                    SELECT f.form_id, f.form_json, f.form_digest,
                           f.expected_state_version, f.document_id,
                           r.status, d.document_type,
                           p.current_state_version, p.current_result_bundle_id,
                           s.state_json,
                           h.workflow_generation, h.state_version,
                           h.founder_snapshot_id, h.area_snapshot_id,
                           h.evidence_snapshot_id, h.policy_snapshot_id,
                           h.index_generation_id, h.seed_registry_id,
                           b.workflow_run_id AS source_workflow_run_id,
                           selected.candidate_json AS selected_candidate_json
                    FROM document_extraction_forms f
                    JOIN document_revisions r
                      ON r.document_revision_id=f.document_revision_id
                    JOIN documents d ON d.document_id=f.document_id
                    JOIN venture_projects p ON p.project_id=f.project_id
                    JOIN venture_states s
                      ON s.project_id=p.project_id
                     AND s.state_version=p.current_state_version
                    JOIN project_heads h ON h.project_id=p.project_id
                    LEFT JOIN result_bundles b
                      ON b.result_bundle_id=p.current_result_bundle_id
                     AND b.project_id=p.project_id
                    LEFT JOIN LATERAL (
                        SELECT candidate_json
                        FROM candidate_selections
                        WHERE project_id=p.project_id
                          AND candidate_id=(s.state_json->>'active_case_id')
                        ORDER BY created_at DESC LIMIT 1
                    ) selected ON TRUE
                    WHERE f.project_id=:project_id AND f.owner_user_id=:user_id
                      AND f.document_revision_id=:revision_id
                    FOR UPDATE OF f, r, p
                    """
                    ),
                    {
                        "project_id": project_id,
                        "user_id": user_id,
                        "revision_id": document_revision_id,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise DocumentNotFoundError("Document extraction form does not exist")
            if (
                row["status"] != DocumentRevisionStatus.EXTRACTION_READY.value
                or row["expected_state_version"] != request.expected_state_version
                or row["current_state_version"] != request.expected_state_version
                or row["form_digest"] != request.expected_form_digest
            ):
                raise DocumentPreconditionError(
                    "Extraction form State, revision, or digest is stale"
                )
            if row["source_workflow_run_id"] is None:
                raise DocumentPreconditionError("Document apply requires a current result")
            state = VentureState.model_validate(row["state_json"])
            if state.active_case_id is None:
                raise DocumentPreconditionError("Document apply requires an active candidate")
            selected_candidate = row["selected_candidate_json"]
            if not isinstance(selected_candidate, dict):
                raise DocumentPreconditionError("Selected candidate scope is unavailable")
            case_type = selected_candidate.get("case_type")
            source_id = selected_candidate.get("source_id")
            if not isinstance(source_id, str) and case_type == "INDEPENDENT":
                independent = selected_candidate.get("independent_model")
                source_id = independent.get("model_id") if isinstance(independent, dict) else None
            if not isinstance(source_id, str) and case_type == "FRANCHISE":
                franchise = selected_candidate.get("franchise")
                source_id = franchise.get("brand_id") if isinstance(franchise, dict) else None
            if case_type not in {"INDEPENDENT", "FRANCHISE"} or not isinstance(source_id, str):
                raise DocumentPreconditionError("Selected candidate scope is invalid")
            form = DocumentExtractionForm.model_validate(row["form_json"])
            claims = self._confirmed_claims(
                form=form,
                document_type=row["document_type"],
                case_id=state.active_case_id,
            )
            application_id = self._new_id()
            event = DocumentClaimsApplied(
                event_id=self._new_id(),
                project_id=project_id,
                user_id=user_id,
                occurred_at=occurred_at,
                application_id=application_id,
                document_id=row["document_id"],
                document_revision_id=document_revision_id,
                expected_state_version=request.expected_state_version,
                active_case_id=state.active_case_id,
                confirmed_claim_ids=[claim.claim_id for claim in claims],
                conflict_ids=[],
            )
            conflicts = self._find_conflicts(
                connection,
                project_id=project_id,
                case_id=state.active_case_id,
                claims=claims,
                occurred_at=occurred_at,
            )
            event = event.model_copy(
                update={"conflict_ids": [conflict.conflict_id for conflict in conflicts]}
            )
            next_state = reduce_venture_state(state, event)
            if next_state is None:
                raise AssertionError("Document apply reducer returned no State")
            event_json = event.model_dump(mode="json")
            connection.execute(
                text(
                    """
                    INSERT INTO project_events(
                        event_id, project_id, event_type, event_json, occurred_at
                    ) VALUES (
                        :event_id, :project_id, :event_type,
                        CAST(:event_json AS JSONB), :occurred_at
                    )
                    """
                ),
                {
                    "event_id": event.event_id,
                    "project_id": project_id,
                    "event_type": event.event_type,
                    "event_json": json.dumps(event_json, separators=(",", ":")),
                    "occurred_at": occurred_at,
                },
            )
            for claim in claims:
                connection.execute(
                    text(
                        """
                        INSERT INTO venture_claims(
                            claim_id, project_id, case_id, case_type, source_id,
                            claim_type, value_json, unit,
                            materiality, status, document_id, document_revision_id,
                            anchor_json, event_id, created_at
                        ) VALUES (
                            :claim_id, :project_id, :case_id, :case_type, :source_id,
                            :claim_type,
                            CAST(:value_json AS JSONB), :unit, :materiality, 'CONFIRMED',
                            :document_id, :revision_id, CAST(:anchor_json AS JSONB),
                            :event_id, :created_at
                        )
                        """
                    ),
                    {
                        "claim_id": claim.claim_id,
                        "project_id": project_id,
                        "case_id": state.active_case_id,
                        "case_type": case_type,
                        "source_id": source_id,
                        "claim_type": claim.claim_type,
                        "value_json": json.dumps(claim.value),
                        "unit": claim.unit,
                        "materiality": claim.materiality,
                        "document_id": row["document_id"],
                        "revision_id": document_revision_id,
                        "anchor_json": (
                            json.dumps(claim.anchor.model_dump(mode="json"))
                            if claim.anchor is not None
                            else None
                        ),
                        "event_id": event.event_id,
                        "created_at": occurred_at,
                    },
                )
            for conflict in conflicts:
                connection.execute(
                    text(
                        """
                        INSERT INTO document_claim_conflicts(
                            conflict_id, project_id, case_id, claim_type, materiality,
                            competing_claim_ids, status, created_at
                        ) VALUES (
                            :conflict_id, :project_id, :case_id, :claim_type, :materiality,
                            CAST(:claim_ids AS JSONB), 'OPEN', :created_at
                        )
                        """
                    ),
                    {
                        "conflict_id": conflict.conflict_id,
                        "project_id": project_id,
                        "case_id": state.active_case_id,
                        "claim_type": conflict.claim_type,
                        "materiality": conflict.materiality,
                        "claim_ids": json.dumps(conflict.competing_claim_ids),
                        "created_at": occurred_at,
                    },
                )
            connection.execute(
                text(
                    """
                    INSERT INTO venture_states(project_id, state_version, state_json, created_at)
                    VALUES (:project_id, :state_version, CAST(:state_json AS JSONB), :created_at)
                    """
                ),
                {
                    "project_id": project_id,
                    "state_version": next_state.state_version,
                    "state_json": json.dumps(
                        next_state.model_dump(mode="json"), separators=(",", ":")
                    ),
                    "created_at": occurred_at,
                },
            )
            connection.execute(
                text(
                    "UPDATE venture_projects SET current_state_version=:version "
                    "WHERE project_id=:project_id"
                ),
                {"version": next_state.state_version, "project_id": project_id},
            )
            previous_head = self._head(row)
            workflow = start_selective_first_proposal(
                connection,
                project_id=project_id,
                user_id=user_id,
                state=next_state,
                source_workflow_run_id=row["source_workflow_run_id"],
                previous_head=previous_head,
                now=occurred_at,
                new_id=self._new_id,
            )
            response = ExtractionFormApplication(
                application_id=application_id,
                project_id=project_id,
                document_revision_id=document_revision_id,
                applied_state_version=next_state.state_version,
                recompute_workflow_run_id=workflow.workflow_run_id,
                claims=claims,
                conflicts=conflicts,
                requires_human_review=any(conflict.materiality == "HIGH" for conflict in conflicts),
            )
            response_json = response.model_dump(mode="json")
            connection.execute(
                text(
                    """
                    INSERT INTO document_form_applications(
                        application_id, project_id, owner_user_id, form_id,
                        document_revision_id, expected_state_version, applied_state_version,
                        form_digest, idempotency_key, request_digest, event_id,
                        recompute_workflow_run_id, response_json, created_at
                    ) VALUES (
                        :application_id, :project_id, :user_id, :form_id,
                        :revision_id, :expected_version, :applied_version,
                        :form_digest, :idempotency_key, :request_digest, :event_id,
                        :workflow_run_id, CAST(:response_json AS JSONB), :created_at
                    )
                    """
                ),
                {
                    "application_id": application_id,
                    "project_id": project_id,
                    "user_id": user_id,
                    "form_id": row["form_id"],
                    "revision_id": document_revision_id,
                    "expected_version": request.expected_state_version,
                    "applied_version": next_state.state_version,
                    "form_digest": request.expected_form_digest,
                    "idempotency_key": idempotency_key,
                    "request_digest": request_digest,
                    "event_id": event.event_id,
                    "workflow_run_id": workflow.workflow_run_id,
                    "response_json": json.dumps(response_json, separators=(",", ":")),
                    "created_at": occurred_at,
                },
            )
            form = form.model_copy(
                update={"form_status": "APPLIED", "applied_state_version": next_state.state_version}
            )
            connection.execute(
                text(
                    """
                    UPDATE document_extraction_forms
                    SET form_json=CAST(:form_json AS JSONB), updated_at=:updated_at
                    WHERE form_id=:form_id
                    """
                ),
                {
                    "form_json": json.dumps(form.model_dump(mode="json"), separators=(",", ":")),
                    "updated_at": occurred_at,
                    "form_id": row["form_id"],
                },
            )
            connection.execute(
                text(
                    "UPDATE document_revisions SET status='APPLIED', updated_at=:updated_at "
                    "WHERE document_revision_id=:revision_id"
                ),
                {"updated_at": occurred_at, "revision_id": document_revision_id},
            )
            return response

    def _confirmed_claims(
        self, *, form: DocumentExtractionForm, document_type: str, case_id: str
    ) -> list[AppliedDocumentClaim]:
        claims: list[AppliedDocumentClaim] = []
        for field in form.fields:
            if field.current_value is None:
                continue
            digest = hashlib.sha256(
                rfc8785.dumps(
                    {
                        "revision": form.document_revision_id,
                        "field": field.field_id,
                        "case": case_id,
                        "type": document_type,
                        "value": field.current_value,
                        "unit": field.unit,
                    }
                )
            ).hexdigest()
            claims.append(
                AppliedDocumentClaim(
                    claim_id=f"document-claim-{digest[:32]}",
                    claim_type=field.claim_type,
                    value=field.current_value,
                    unit=field.unit,
                    materiality=field.materiality,
                    document_revision_id=form.document_revision_id,
                    anchor=field.anchor,
                )
            )
        return claims

    def _find_conflicts(
        self,
        connection: Any,
        *,
        project_id: str,
        case_id: str,
        claims: list[AppliedDocumentClaim],
        occurred_at: datetime,
    ) -> list[DocumentClaimConflict]:
        del occurred_at
        conflicts: list[DocumentClaimConflict] = []
        for claim in claims:
            rows = (
                connection.execute(
                    text(
                        """
                    SELECT claim_id, value_json, materiality
                    FROM venture_claims
                    WHERE project_id=:project_id AND case_id=:case_id
                      AND claim_type=:claim_type AND status='CONFIRMED'
                    ORDER BY created_at, claim_id
                    """
                    ),
                    {
                        "project_id": project_id,
                        "case_id": case_id,
                        "claim_type": claim.claim_type,
                    },
                )
                .mappings()
                .all()
            )
            competing = [row for row in rows if row["value_json"] != claim.value]
            if not competing:
                continue
            ids = sorted({row["claim_id"] for row in competing} | {claim.claim_id})
            digest = hashlib.sha256(rfc8785.dumps(ids)).hexdigest()
            conflicts.append(
                DocumentClaimConflict(
                    conflict_id=f"document-conflict-{digest[:32]}",
                    claim_type=claim.claim_type,
                    materiality=(
                        "HIGH"
                        if claim.materiality == "HIGH"
                        or any(row["materiality"] == "HIGH" for row in competing)
                        else "MEDIUM"
                    ),
                    competing_claim_ids=ids,
                )
            )
        return conflicts

    @staticmethod
    def _form_digest(form: DocumentExtractionForm) -> str:
        value = form.model_dump(mode="json", exclude={"form_digest", "applied_state_version"})
        return f"sha256:{hashlib.sha256(rfc8785.dumps(value)).hexdigest()}"

    @staticmethod
    def _validate_blocks(revision_id: str, blocks: list[dict[str, Any]]) -> None:
        ids: set[str] = set()
        for block in blocks:
            block_id = block["block_id"]
            if block_id in ids:
                raise ContractValidationError("Parser block ids must be unique")
            ids.add(block_id)
            if block["anchor"]["document_revision_id"] != revision_id:
                raise ContractValidationError("Parser block crossed document revision")

    @staticmethod
    def _head(row: Any) -> HeadFence:
        return HeadFence(
            workflow_generation=row["workflow_generation"],
            state_version=row["state_version"],
            founder_snapshot_id=row["founder_snapshot_id"],
            area_snapshot_id=row["area_snapshot_id"],
            evidence_snapshot_id=row["evidence_snapshot_id"],
            policy_snapshot_id=row["policy_snapshot_id"],
            index_generation_id=row["index_generation_id"],
            seed_registry_id=row["seed_registry_id"],
        )

    @classmethod
    def _build_form(
        cls,
        *,
        form_id: str,
        project_id: str,
        document_id: str,
        document_revision_id: str,
        expected_state_version: int,
        claim_types: list[str],
        claims: list[dict[str, Any]],
        unresolved: set[str],
        document_risk_flags: set[str],
    ) -> DocumentExtractionForm:
        fields: list[ExtractionField] = []
        produced_types: set[str] = set()
        for claim in claims:
            claim_type = claim["predicate"]
            produced_types.add(claim_type)
            typed = claim["typed_value"]
            warnings = sorted(set(claim["risk_flags"]) | document_risk_flags)
            status = (
                ExtractionStatus.REVIEW_REQUIRED
                if warnings or claim["extraction_status"] != "PROPOSED"
                else ExtractionStatus.AUTO_FILLED
            )
            fields.append(
                ExtractionField(
                    field_id=claim["claim_id"],
                    claim_type=claim_type,
                    label=LABELS.get(claim_type, claim_type),
                    raw_value_text=claim["raw_value_text"],
                    extracted_value=cls._editable_value(typed),
                    current_value=cls._editable_value(typed),
                    unit=claim["unit"] or claim["currency"],
                    materiality="HIGH" if claim_type in HIGH_MATERIALITY else "MEDIUM",
                    extraction_status=status,
                    edit_status=EditStatus.UNCHANGED,
                    anchor=claim["anchor"],
                    warnings=warnings,
                )
            )
        for claim_type in claim_types:
            if claim_type in produced_types:
                continue
            warnings = sorted(document_risk_flags)
            if claim_type in unresolved:
                warnings.append("UNRESOLVED_BY_DOCUMENT_ANALYST")
            fields.append(
                ExtractionField(
                    field_id=f"unresolved-{claim_type.lower()}",
                    claim_type=claim_type,
                    label=LABELS.get(claim_type, claim_type),
                    raw_value_text=None,
                    extracted_value=None,
                    current_value=None,
                    unit=None,
                    materiality="HIGH" if claim_type in HIGH_MATERIALITY else "MEDIUM",
                    extraction_status=ExtractionStatus.UNRESOLVED,
                    anchor=None,
                    warnings=sorted(set(warnings)),
                )
            )
        return DocumentExtractionForm(
            form_id=form_id,
            project_id=project_id,
            document_id=document_id,
            document_revision_id=document_revision_id,
            expected_state_version=expected_state_version,
            form_status=(
                "HAS_UNRESOLVED"
                if any(field.extraction_status != ExtractionStatus.AUTO_FILLED for field in fields)
                else "READY"
            ),
            fields=fields,
        )

    @staticmethod
    def _editable_value(typed: dict[str, Any]) -> str | int | float | bool | None:
        if typed["kind"] == "MONEY_RANGE":
            return typed.get("base") or typed.get("low") or typed.get("high")
        return cast(str | int | float | bool | None, typed.get("value"))


class UnavailableDocumentExtractionService:
    def accept_parser_result(self, **_: Any) -> DocumentExtractionForm:
        raise PersistenceUnavailableError("Document extraction is unavailable")

    def get_form(self, **_: Any) -> DocumentExtractionForm:
        raise PersistenceUnavailableError("Document extraction is unavailable")

    def update_form(self, **_: Any) -> DocumentExtractionForm:
        raise PersistenceUnavailableError("Document extraction is unavailable")

    def apply_form(self, **_: Any) -> ExtractionFormApplication:
        raise PersistenceUnavailableError("Document extraction is unavailable")
