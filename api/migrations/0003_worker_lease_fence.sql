CREATE TABLE project_heads (
    project_id TEXT PRIMARY KEY REFERENCES venture_projects(project_id) ON DELETE CASCADE,
    workflow_generation BIGINT NOT NULL CHECK (workflow_generation >= 0),
    state_version BIGINT NOT NULL CHECK (state_version >= 1),
    founder_snapshot_id TEXT,
    area_snapshot_id TEXT,
    evidence_snapshot_id TEXT,
    policy_snapshot_id TEXT,
    index_generation_id TEXT,
    seed_registry_id TEXT,
    updated_at TIMESTAMPTZ NOT NULL
);

-- statement-break
ALTER TABLE stage_runs RENAME COLUMN lease_token TO lease_token_digest;

-- statement-break
ALTER TABLE workflow_outbox DROP CONSTRAINT workflow_outbox_status_check;
ALTER TABLE workflow_outbox
    ADD CONSTRAINT workflow_outbox_status_check
    CHECK (status IN ('PENDING', 'PUBLISHING', 'PUBLISHED')),
    ADD COLUMN claim_token_digest TEXT,
    ADD COLUMN claim_expires_at TIMESTAMPTZ,
    ADD COLUMN publisher_id TEXT,
    ADD COLUMN pubsub_message_id TEXT;
