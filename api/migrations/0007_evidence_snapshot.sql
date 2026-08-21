CREATE TABLE evidence_records (
    project_id TEXT NOT NULL REFERENCES venture_projects(project_id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL,
    record_json JSONB NOT NULL CHECK (jsonb_typeof(record_json) = 'object'),
    record_digest TEXT NOT NULL CHECK (record_digest ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (project_id, evidence_id)
);

-- statement-break
CREATE TABLE evidence_snapshots (
    evidence_snapshot_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES venture_projects(project_id) ON DELETE CASCADE,
    workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(workflow_run_id) ON DELETE CASCADE,
    source_stage_run_id TEXT NOT NULL REFERENCES stage_runs(stage_run_id) ON DELETE RESTRICT,
    snapshot_json JSONB NOT NULL CHECK (jsonb_typeof(snapshot_json) = 'object'),
    snapshot_digest TEXT NOT NULL CHECK (snapshot_digest ~ '^sha256:[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (workflow_run_id, source_stage_run_id)
);

CREATE INDEX evidence_snapshots_project_created_idx
    ON evidence_snapshots(project_id, created_at DESC, evidence_snapshot_id);

-- statement-break
CREATE TABLE evidence_snapshot_records (
    evidence_snapshot_id TEXT NOT NULL
        REFERENCES evidence_snapshots(evidence_snapshot_id) ON DELETE CASCADE,
    project_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    PRIMARY KEY (evidence_snapshot_id, evidence_id),
    FOREIGN KEY (project_id, evidence_id)
        REFERENCES evidence_records(project_id, evidence_id) ON DELETE RESTRICT
);
