ALTER TABLE workflow_outbox DROP CONSTRAINT workflow_outbox_status_check;
ALTER TABLE workflow_outbox
    ADD CONSTRAINT workflow_outbox_status_check
    CHECK (status IN ('PENDING', 'PUBLISHING', 'PUBLISHED', 'DEAD_LETTER')),
    ADD COLUMN failure_code TEXT,
    ADD COLUMN failed_at TIMESTAMPTZ;
