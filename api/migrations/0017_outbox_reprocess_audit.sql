CREATE TABLE outbox_reprocess_events (
    reprocess_event_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    outbox_id BIGINT NOT NULL REFERENCES workflow_outbox(outbox_id),
    topic TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    previous_failure_code TEXT NOT NULL,
    previous_attempts INTEGER NOT NULL CHECK (previous_attempts >= 0),
    remediation_code TEXT NOT NULL,
    change_reference TEXT NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX outbox_reprocess_events_outbox_idx
    ON outbox_reprocess_events(outbox_id, requested_at DESC);
