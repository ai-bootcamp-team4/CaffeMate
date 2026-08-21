ALTER TABLE venture_projects
    ADD COLUMN workflow_generation BIGINT NOT NULL DEFAULT 0
    CHECK (workflow_generation >= 0);

-- statement-break
ALTER TABLE venture_states
    ADD COLUMN founder_snapshot_id TEXT
    GENERATED ALWAYS AS (
        project_id || ':state:' || state_version::TEXT || ':founder'
    ) STORED,
    ADD COLUMN area_snapshot_id TEXT
    GENERATED ALWAYS AS (
        project_id || ':state:' || state_version::TEXT || ':area'
    ) STORED;

ALTER TABLE venture_states
    ADD CONSTRAINT venture_states_founder_snapshot_length
        CHECK (char_length(founder_snapshot_id) <= 128),
    ADD CONSTRAINT venture_states_area_snapshot_length
        CHECK (char_length(area_snapshot_id) <= 128);

-- statement-break
CREATE TABLE workflow_runs (
    workflow_run_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES venture_projects(project_id) ON DELETE CASCADE,
    owner_user_id TEXT NOT NULL,
    workflow_code TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'QUEUED', 'RUNNING', 'WAITING_FOR_HUMAN', 'SUCCEEDED',
            'PARTIAL', 'FAILED', 'CANCELLED', 'STALE'
        )
    ),
    workflow_generation BIGINT NOT NULL CHECK (workflow_generation >= 1),
    state_version BIGINT NOT NULL CHECK (state_version >= 1),
    founder_snapshot_id TEXT,
    area_snapshot_id TEXT,
    evidence_snapshot_id TEXT,
    policy_snapshot_id TEXT NOT NULL,
    index_generation_id TEXT,
    seed_registry_id TEXT,
    input_digest TEXT NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    cancelled_at TIMESTAMPTZ
);

CREATE INDEX workflow_runs_project_created_idx
    ON workflow_runs(project_id, created_at DESC, workflow_run_id);

-- statement-break
CREATE TABLE stage_runs (
    stage_run_id TEXT PRIMARY KEY,
    workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(workflow_run_id) ON DELETE CASCADE,
    stage_code TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'PENDING', 'READY', 'RUNNING', 'CHECKPOINTED', 'SUCCEEDED',
            'SKIPPED', 'WAITING_FOR_HUMAN', 'TIMED_OUT', 'FAILED', 'CANCELLED'
        )
    ),
    input_digest TEXT NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    lease_token TEXT,
    lease_owner TEXT,
    lease_expires_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    result_json JSONB,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    UNIQUE (workflow_run_id, stage_code, input_digest)
);

-- statement-break
CREATE TABLE workflow_events (
    sequence_id BIGSERIAL PRIMARY KEY,
    workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(workflow_run_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    event_json JSONB NOT NULL CHECK (jsonb_typeof(event_json) = 'object'),
    occurred_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX workflow_events_run_sequence_idx
    ON workflow_events(workflow_run_id, sequence_id);

-- statement-break
CREATE TABLE workflow_idempotency_records (
    user_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_digest BYTEA NOT NULL,
    response_workflow_run_id TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, operation, idempotency_key)
);

-- statement-break
CREATE TABLE workflow_outbox (
    outbox_id BIGSERIAL PRIMARY KEY,
    topic TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    payload_json JSONB NOT NULL CHECK (jsonb_typeof(payload_json) = 'object'),
    payload_digest TEXT NOT NULL CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'PUBLISHED')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    published_at TIMESTAMPTZ,
    UNIQUE (topic, aggregate_id, payload_digest)
);

CREATE INDEX workflow_outbox_pending_idx
    ON workflow_outbox(status, available_at, outbox_id);
