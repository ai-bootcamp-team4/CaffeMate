"""PostgreSQL projection of confirmed selected-case claims for finance resolution."""

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.domain.models import VentureState
from app.finance.case_facts import CaseFactRecord, CaseFactResolution, CaseFactResolver


def load_current_case_fact_resolution(
    connection: Connection,
    *,
    project_id: str,
    state: VentureState,
) -> CaseFactResolution:
    """Resolve confirmed claims for the currently selected case only."""

    if state.active_case_id is None:
        return CaseFactResolution()
    rows = (
        connection.execute(
            text(
                """
                SELECT claim.claim_id, claim.source_id, claim.claim_type,
                       claim.value_json, claim.unit, claim.materiality,
                       claim.document_id, claim.document_revision_id,
                       claim.anchor_json, claim.created_at,
                       document.document_type, revision.original_filename
                FROM venture_claims claim
                JOIN documents document ON document.document_id=claim.document_id
                JOIN document_revisions revision
                  ON revision.document_revision_id=claim.document_revision_id
                WHERE claim.project_id=:project_id
                  AND claim.case_id=:case_id
                  AND claim.status='CONFIRMED'
                  AND document.status='ACTIVE'
                ORDER BY claim.created_at, claim.claim_id
                """
            ),
            {"project_id": project_id, "case_id": state.active_case_id},
        )
        .mappings()
        .all()
    )
    records = [
        CaseFactRecord(
            claim_id=str(row["claim_id"]),
            source_id=str(row["source_id"]),
            claim_type=str(row["claim_type"]),
            value=row["value_json"],
            unit=(str(row["unit"]) if row["unit"] is not None else None),
            materiality=str(row["materiality"]),
            document_type=str(row["document_type"]),
            document_id=str(row["document_id"]),
            document_revision_id=str(row["document_revision_id"]),
            original_filename=(
                str(row["original_filename"])
                if row["original_filename"] is not None
                else None
            ),
            anchor=(dict(row["anchor_json"]) if isinstance(row["anchor_json"], dict) else None),
            created_at=row["created_at"],
        )
        for row in rows
        if isinstance(row["value_json"], (int, float, str, bool))
    ]
    if not records:
        return CaseFactResolution()
    record_by_id = {record.claim_id: record for record in records}
    conflicts = (
        connection.execute(
            text(
                """
                SELECT competing_claim_ids
                FROM document_claim_conflicts
                WHERE project_id=:project_id AND case_id=:case_id AND status='OPEN'
                ORDER BY conflict_id
                """
            ),
            {"project_id": project_id, "case_id": state.active_case_id},
        )
        .scalars()
        .all()
    )
    open_conflict_keys: set[tuple[str, str]] = set()
    for claim_ids in conflicts:
        if not isinstance(claim_ids, list):
            continue
        keys = {
            record_by_id[claim_id].conflict_key
            for claim_id in claim_ids
            if isinstance(claim_id, str) and claim_id in record_by_id
        }
        # Older rows may contain a cross-document-type QUOTE_TOTAL conflict.
        # Only one semantic document-family/claim key may block finance resolution.
        if len(keys) == 1:
            open_conflict_keys.update(keys)
    return CaseFactResolver().resolve(
        records=records,
        open_conflict_keys=open_conflict_keys,
    )
