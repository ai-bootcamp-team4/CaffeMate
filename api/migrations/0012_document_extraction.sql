ALTER TABLE document_revisions DROP CONSTRAINT document_revisions_status_check;
ALTER TABLE document_revisions ADD CONSTRAINT document_revisions_status_check CHECK (
    status IN (
        'UPLOAD_PENDING', 'VALIDATING', 'SCAN_PENDING', 'READY_FOR_PARSING',
        'PARSING', 'EXTRACTION_READY', 'EXTRACTION_FAILED', 'QUARANTINED', 'DELETED'
    )
);

-- statement-break
CREATE TABLE parser_block_sets (
    document_revision_id TEXT PRIMARY KEY
        REFERENCES document_revisions(document_revision_id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES venture_projects(project_id) ON DELETE CASCADE,
    parser_version TEXT NOT NULL,
    blocks_json JSONB NOT NULL CHECK (jsonb_typeof(blocks_json) = 'array'),
    block_digest TEXT NOT NULL CHECK (block_digest ~ '^sha256:[0-9a-f]{64}$'),
    prompt_injection_flags JSONB NOT NULL DEFAULT '[]'::JSONB
        CHECK (jsonb_typeof(prompt_injection_flags) = 'array'),
    created_at TIMESTAMPTZ NOT NULL
);

-- statement-break
CREATE TABLE document_extraction_forms (
    form_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES venture_projects(project_id) ON DELETE CASCADE,
    owner_user_id TEXT NOT NULL,
    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    document_revision_id TEXT NOT NULL
        REFERENCES document_revisions(document_revision_id) ON DELETE CASCADE,
    expected_state_version BIGINT NOT NULL CHECK (expected_state_version >= 1),
    form_json JSONB NOT NULL CHECK (jsonb_typeof(form_json) = 'object'),
    agent_tasks_json JSONB NOT NULL CHECK (jsonb_typeof(agent_tasks_json) = 'array'),
    agent_results_json JSONB NOT NULL CHECK (jsonb_typeof(agent_results_json) = 'array'),
    form_digest TEXT NOT NULL CHECK (form_digest ~ '^sha256:[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE(document_revision_id)
);
