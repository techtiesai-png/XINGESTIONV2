BEGIN;

ALTER TABLE task_outbox
    ADD COLUMN IF NOT EXISTS claim_token VARCHAR(64),
    ADD COLUMN IF NOT EXISTS claim_expires_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_task_outbox_claimable
    ON task_outbox (available_at, claim_expires_at, id)
    WHERE published_at IS NULL;

COMMIT;
