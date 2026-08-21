CREATE TABLE documents (
    document_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES venture_projects(project_id) ON DELETE CASCADE,
    owner_user_id TEXT NOT NULL,
    active_case_id TEXT NOT NULL,
    document_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'DELETED')),
    current_revision_number INTEGER NOT NULL CHECK (current_revision_number >= 1),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

-- statement-break
CREATE TABLE document_revisions (
    document_revision_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES venture_projects(project_id) ON DELETE CASCADE,
    revision_number INTEGER NOT NULL CHECK (revision_number >= 1),
    object_path TEXT NOT NULL UNIQUE,
    original_filename TEXT NOT NULL,
    declared_content_type TEXT NOT NULL,
    declared_size_bytes BIGINT NOT NULL CHECK (declared_size_bytes > 0),
    declared_sha256 TEXT NOT NULL CHECK (declared_sha256 ~ '^[0-9a-f]{64}$'),
    observed_content_type TEXT,
    observed_size_bytes BIGINT CHECK (observed_size_bytes > 0),
    observed_sha256 TEXT CHECK (observed_sha256 IS NULL OR observed_sha256 ~ '^[0-9a-f]{64}$'),
    status TEXT NOT NULL CHECK (
        status IN (
            'UPLOAD_PENDING', 'VALIDATING', 'SCAN_PENDING',
            'READY_FOR_PARSING', 'QUARANTINED', 'DELETED'
        )
    ),
    failure_codes JSONB NOT NULL DEFAULT '[]'::JSONB CHECK (jsonb_typeof(failure_codes) = 'array'),
    idempotency_key TEXT NOT NULL,
    request_digest BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    UNIQUE(document_id, revision_number),
    UNIQUE(project_id, idempotency_key)
);

CREATE INDEX document_revisions_project_created_idx
    ON document_revisions(project_id, created_at DESC, document_revision_id);
