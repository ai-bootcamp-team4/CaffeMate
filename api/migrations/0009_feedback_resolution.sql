ALTER TABLE feedback_previews
    ADD COLUMN proposal_digest TEXT
        CHECK (proposal_digest IS NULL OR proposal_digest ~ '^sha256:[0-9a-f]{64}$'),
    ADD COLUMN resolution_idempotency_key TEXT,
    ADD COLUMN resolution_request_digest BYTEA,
    ADD COLUMN confirmed_event_id TEXT REFERENCES project_events(event_id),
    ADD COLUMN confirmed_state_version BIGINT CHECK (confirmed_state_version >= 1),
    ADD COLUMN recompute_workflow_run_id TEXT REFERENCES workflow_runs(workflow_run_id),
    ADD COLUMN resolved_at TIMESTAMPTZ;

-- statement-break
CREATE UNIQUE INDEX feedback_previews_resolution_key_idx
    ON feedback_previews(owner_user_id, project_id, resolution_idempotency_key)
    WHERE resolution_idempotency_key IS NOT NULL;
