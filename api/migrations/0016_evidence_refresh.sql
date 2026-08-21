CREATE TABLE evidence_source_heads (
    project_id TEXT NOT NULL REFERENCES venture_projects(project_id) ON DELETE CASCADE,
    source_ref TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    source_observed_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(project_id, source_ref)
);

-- statement-break
CREATE TABLE evidence_lifecycle (
    project_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'STALE', 'SUPERSEDED', 'CONFLICT')),
    reason_code TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(project_id, evidence_id),
    FOREIGN KEY(project_id, evidence_id)
        REFERENCES evidence_records(project_id, evidence_id) ON DELETE CASCADE
);

-- statement-break
CREATE TABLE result_invalidations (
    result_bundle_id TEXT PRIMARY KEY
        REFERENCES result_bundles(result_bundle_id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES venture_projects(project_id) ON DELETE CASCADE,
    reason_codes JSONB NOT NULL CHECK (jsonb_typeof(reason_codes) = 'array'),
    invalidated_at TIMESTAMPTZ NOT NULL
);

-- statement-break
CREATE TABLE evidence_refreshes (
    refresh_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES venture_projects(project_id) ON DELETE CASCADE,
    request_digest TEXT NOT NULL CHECK (request_digest ~ '^sha256:[0-9a-f]{64}$'),
    source_result_bundle_id TEXT REFERENCES result_bundles(result_bundle_id),
    recompute_workflow_run_id TEXT REFERENCES workflow_runs(workflow_run_id),
    response_json JSONB NOT NULL CHECK (jsonb_typeof(response_json) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(project_id, request_digest)
);
