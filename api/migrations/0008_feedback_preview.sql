CREATE TABLE feedback_previews (
    preview_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES venture_projects(project_id) ON DELETE CASCADE,
    owner_user_id TEXT NOT NULL,
    result_bundle_id TEXT NOT NULL REFERENCES result_bundles(result_bundle_id),
    source_workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(workflow_run_id),
    base_state_version BIGINT NOT NULL CHECK (base_state_version >= 1),
    head_json JSONB NOT NULL CHECK (jsonb_typeof(head_json) = 'object'),
    idempotency_key TEXT NOT NULL,
    request_digest BYTEA NOT NULL,
    user_input TEXT NOT NULL CHECK (char_length(user_input) BETWEEN 1 AND 8000),
    task_json JSONB NOT NULL CHECK (jsonb_typeof(task_json) = 'object'),
    agent_result_json JSONB CHECK (
        agent_result_json IS NULL OR jsonb_typeof(agent_result_json) = 'object'
    ),
    proposal_json JSONB CHECK (
        proposal_json IS NULL OR jsonb_typeof(proposal_json) = 'object'
    ),
    status TEXT NOT NULL CHECK (
        status IN (
            'PROCESSING', 'REVIEW_REQUIRED', 'CLARIFICATION_REQUIRED',
            'NOOP', 'UNSUPPORTED', 'EXPIRED', 'CONFIRMED', 'CANCELLED'
        )
    ),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE(owner_user_id, project_id, idempotency_key)
);

CREATE INDEX feedback_previews_project_created_idx
    ON feedback_previews(project_id, created_at, preview_id);
