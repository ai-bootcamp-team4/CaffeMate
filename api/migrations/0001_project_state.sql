CREATE TABLE venture_projects (
    project_id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    current_state_version BIGINT,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX venture_projects_owner_created_idx
    ON venture_projects(owner_user_id, created_at, project_id);

-- statement-break
CREATE TABLE venture_states (
    project_id TEXT NOT NULL REFERENCES venture_projects(project_id) ON DELETE CASCADE,
    state_version BIGINT NOT NULL CHECK (state_version >= 1),
    state_json JSONB NOT NULL CHECK (jsonb_typeof(state_json) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (project_id, state_version)
);

-- statement-break
ALTER TABLE venture_projects
    ADD CONSTRAINT venture_projects_current_state_fk
    FOREIGN KEY (project_id, current_state_version)
    REFERENCES venture_states(project_id, state_version)
    DEFERRABLE INITIALLY DEFERRED;

-- statement-break
CREATE TABLE project_events (
    event_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES venture_projects(project_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    event_json JSONB NOT NULL CHECK (jsonb_typeof(event_json) = 'object'),
    occurred_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX project_events_project_time_idx
    ON project_events(project_id, occurred_at, event_id);

-- statement-break
CREATE TABLE idempotency_records (
    user_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_digest BYTEA NOT NULL,
    response_project_id TEXT,
    response_state_version BIGINT,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (user_id, operation, idempotency_key)
);
