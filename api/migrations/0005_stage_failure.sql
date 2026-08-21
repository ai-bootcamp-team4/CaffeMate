ALTER TABLE stage_runs
    ADD COLUMN failure_json JSONB
    CHECK (failure_json IS NULL OR jsonb_typeof(failure_json) = 'object');
