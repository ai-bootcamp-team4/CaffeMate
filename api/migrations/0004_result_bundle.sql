CREATE TABLE result_bundles (
    result_bundle_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES venture_projects(project_id) ON DELETE CASCADE,
    workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(workflow_run_id) ON DELETE CASCADE,
    workflow_generation BIGINT NOT NULL CHECK (workflow_generation >= 1),
    state_version BIGINT NOT NULL CHECK (state_version >= 1),
    founder_snapshot_id TEXT,
    area_snapshot_id TEXT,
    evidence_snapshot_id TEXT,
    policy_snapshot_id TEXT NOT NULL,
    index_generation_id TEXT,
    seed_registry_id TEXT,
    bundle_json JSONB NOT NULL CHECK (jsonb_typeof(bundle_json) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (workflow_run_id),
    UNIQUE (project_id, result_bundle_id)
);

CREATE INDEX result_bundles_project_created_idx
    ON result_bundles(project_id, created_at DESC, result_bundle_id);

-- statement-break
ALTER TABLE venture_projects
    ADD COLUMN current_result_bundle_id TEXT;

ALTER TABLE venture_projects
    ADD CONSTRAINT venture_projects_current_result_bundle_fk
    FOREIGN KEY (project_id, current_result_bundle_id)
    REFERENCES result_bundles(project_id, result_bundle_id)
    ON DELETE SET NULL (current_result_bundle_id)
    DEFERRABLE INITIALLY DEFERRED;
