ALTER TABLE workflow_runs
    ADD COLUMN source_workflow_run_id TEXT REFERENCES workflow_runs(workflow_run_id),
    ADD COLUMN source_result_bundle_id TEXT REFERENCES result_bundles(result_bundle_id);

-- statement-break
CREATE TABLE result_decision_deltas (
    result_bundle_id TEXT PRIMARY KEY
        REFERENCES result_bundles(result_bundle_id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES venture_projects(project_id) ON DELETE CASCADE,
    previous_result_bundle_id TEXT NOT NULL
        REFERENCES result_bundles(result_bundle_id) ON DELETE CASCADE,
    delta_json JSONB NOT NULL CHECK (jsonb_typeof(delta_json) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(project_id, result_bundle_id)
);
