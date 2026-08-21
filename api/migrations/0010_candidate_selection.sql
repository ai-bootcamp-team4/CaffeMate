CREATE TABLE candidate_selections (
    selection_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES venture_projects(project_id) ON DELETE CASCADE,
    owner_user_id TEXT NOT NULL,
    result_bundle_id TEXT NOT NULL REFERENCES result_bundles(result_bundle_id),
    candidate_id TEXT NOT NULL,
    selected_state_version BIGINT NOT NULL CHECK (selected_state_version >= 1),
    event_id TEXT NOT NULL REFERENCES project_events(event_id),
    request_digest BYTEA NOT NULL,
    idempotency_key TEXT NOT NULL,
    candidate_json JSONB NOT NULL CHECK (jsonb_typeof(candidate_json) = 'object'),
    checklist_json JSONB NOT NULL CHECK (jsonb_typeof(checklist_json) = 'array'),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(owner_user_id, project_id, idempotency_key)
);

CREATE INDEX candidate_selections_project_created_idx
    ON candidate_selections(project_id, created_at DESC, selection_id);
