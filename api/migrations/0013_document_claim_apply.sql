ALTER TABLE document_revisions DROP CONSTRAINT document_revisions_status_check;
ALTER TABLE document_revisions ADD CONSTRAINT document_revisions_status_check CHECK (
    status IN (
        'UPLOAD_PENDING', 'VALIDATING', 'SCAN_PENDING', 'READY_FOR_PARSING',
        'PARSING', 'EXTRACTION_READY', 'APPLIED', 'EXTRACTION_FAILED',
        'QUARANTINED', 'DELETED'
    )
);

-- statement-break
CREATE TABLE venture_claims (
    claim_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES venture_projects(project_id) ON DELETE CASCADE,
    case_id TEXT NOT NULL,
    case_type TEXT NOT NULL CHECK (case_type IN ('INDEPENDENT', 'FRANCHISE')),
    source_id TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    value_json JSONB NOT NULL,
    unit TEXT,
    materiality TEXT NOT NULL CHECK (materiality IN ('HIGH', 'MEDIUM', 'LOW')),
    status TEXT NOT NULL CHECK (status IN ('CONFIRMED', 'SUPERSEDED')),
    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    document_revision_id TEXT NOT NULL
        REFERENCES document_revisions(document_revision_id) ON DELETE CASCADE,
    anchor_json JSONB,
    event_id TEXT NOT NULL REFERENCES project_events(event_id),
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX venture_claims_project_case_type_idx
    ON venture_claims(project_id, case_id, claim_type, created_at DESC);

-- statement-break
CREATE TABLE document_claim_conflicts (
    conflict_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES venture_projects(project_id) ON DELETE CASCADE,
    case_id TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    materiality TEXT NOT NULL CHECK (materiality IN ('HIGH', 'MEDIUM', 'LOW')),
    competing_claim_ids JSONB NOT NULL CHECK (jsonb_array_length(competing_claim_ids) >= 2),
    status TEXT NOT NULL CHECK (status IN ('OPEN', 'RESOLVED')),
    created_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ
);

-- statement-break
CREATE TABLE document_form_applications (
    application_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES venture_projects(project_id) ON DELETE CASCADE,
    owner_user_id TEXT NOT NULL,
    form_id TEXT NOT NULL REFERENCES document_extraction_forms(form_id) ON DELETE CASCADE,
    document_revision_id TEXT NOT NULL
        REFERENCES document_revisions(document_revision_id) ON DELETE CASCADE,
    expected_state_version BIGINT NOT NULL CHECK (expected_state_version >= 1),
    applied_state_version BIGINT NOT NULL CHECK (applied_state_version >= 2),
    form_digest TEXT NOT NULL CHECK (form_digest ~ '^sha256:[0-9a-f]{64}$'),
    idempotency_key TEXT NOT NULL,
    request_digest BYTEA NOT NULL,
    event_id TEXT NOT NULL REFERENCES project_events(event_id),
    recompute_workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(workflow_run_id),
    response_json JSONB NOT NULL CHECK (jsonb_typeof(response_json) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(owner_user_id, project_id, idempotency_key),
    UNIQUE(document_revision_id)
);
