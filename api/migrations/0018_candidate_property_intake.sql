CREATE TABLE candidate_property_intakes (
    property_input_id TEXT PRIMARY KEY,
    selection_id TEXT NOT NULL REFERENCES candidate_selections(selection_id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES venture_projects(project_id) ON DELETE CASCADE,
    owner_user_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    case_type TEXT NOT NULL CHECK (case_type IN ('INDEPENDENT', 'FRANCHISE')),
    source_id TEXT NOT NULL,
    address TEXT NOT NULL,
    area_sqm DOUBLE PRECISION NOT NULL CHECK (area_sqm > 0 AND area_sqm <= 1000),
    floor TEXT,
    deposit_krw BIGINT NOT NULL CHECK (deposit_krw >= 0),
    monthly_rent_krw BIGINT NOT NULL CHECK (monthly_rent_krw >= 0),
    management_fee_krw BIGINT NOT NULL CHECK (management_fee_krw >= 0),
    key_money_krw BIGINT CHECK (key_money_krw >= 0),
    expected_state_version BIGINT NOT NULL CHECK (expected_state_version >= 1),
    applied_state_version BIGINT NOT NULL CHECK (applied_state_version >= 2),
    idempotency_key TEXT NOT NULL,
    request_digest BYTEA NOT NULL,
    event_id TEXT NOT NULL REFERENCES project_events(event_id),
    recompute_workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(workflow_run_id),
    response_json JSONB NOT NULL CHECK (jsonb_typeof(response_json) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(owner_user_id, project_id, idempotency_key)
);

CREATE INDEX candidate_property_intakes_project_source_created_idx
    ON candidate_property_intakes(project_id, source_id, created_at DESC, property_input_id DESC);
