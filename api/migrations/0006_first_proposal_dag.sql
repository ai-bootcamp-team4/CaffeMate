ALTER TABLE stage_runs
    ADD CONSTRAINT stage_runs_workflow_stage_unique
    UNIQUE (workflow_run_id, stage_code);

-- statement-break
CREATE TABLE stage_dependencies (
    stage_run_id TEXT NOT NULL REFERENCES stage_runs(stage_run_id) ON DELETE CASCADE,
    depends_on_stage_run_id TEXT NOT NULL REFERENCES stage_runs(stage_run_id) ON DELETE CASCADE,
    PRIMARY KEY (stage_run_id, depends_on_stage_run_id),
    CHECK (stage_run_id <> depends_on_stage_run_id)
);

CREATE INDEX stage_dependencies_prerequisite_idx
    ON stage_dependencies(depends_on_stage_run_id, stage_run_id);
