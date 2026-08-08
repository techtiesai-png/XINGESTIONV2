BEGIN;

-- Control-plane hardening migration for existing XINGESTIONV2 databases.
-- Apply during a maintenance window: legacy RUNNING rows are returned to
-- PENDING because the pre-migration worker did not own durable leases.

ALTER TABLE worker_tasks
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT,
    ADD COLUMN IF NOT EXISTS priority INT NOT NULL DEFAULT 100,
    ADD COLUMN IF NOT EXISTS delivery_generation INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS enqueued_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS lease_owner VARCHAR(200),
    ADD COLUMN IF NOT EXISTS lease_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS result_metadata JSONB,
    ADD COLUMN IF NOT EXISTS last_failure_class VARCHAR(100);

UPDATE worker_tasks
SET idempotency_key = 'legacy:' || id::text
WHERE idempotency_key IS NULL;

ALTER TABLE worker_tasks
    ALTER COLUMN idempotency_key SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_worker_tasks_idempotency
    ON worker_tasks (idempotency_key);

UPDATE worker_tasks
SET status = 'RETRY_SCHEDULED'
WHERE status = 'RETRYING';

UPDATE worker_tasks
SET status = 'PENDING',
    leased_at = NULL,
    lease_owner = NULL,
    lease_started_at = NULL,
    lease_expires_at = NULL,
    updated_at = NOW()
WHERE status = 'RUNNING';

CREATE INDEX IF NOT EXISTS idx_worker_tasks_dispatch
    ON worker_tasks (status, next_run_at, priority DESC);

CREATE INDEX IF NOT EXISTS idx_worker_tasks_lease_expiry
    ON worker_tasks (lease_expires_at)
    WHERE status = 'RUNNING';

CREATE TABLE IF NOT EXISTS task_outbox (
    id BIGSERIAL PRIMARY KEY,
    task_id INT NOT NULL REFERENCES worker_tasks(id) ON DELETE CASCADE,
    delivery_generation INT NOT NULL,
    event_type VARCHAR(50) NOT NULL DEFAULT 'TASK_READY',
    payload JSONB NOT NULL,
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at TIMESTAMPTZ,
    redis_message_id VARCHAR(100),
    publish_attempts INT NOT NULL DEFAULT 0,
    last_error TEXT,
    UNIQUE (task_id, delivery_generation)
);

CREATE INDEX IF NOT EXISTS idx_task_outbox_ready
    ON task_outbox (available_at, id)
    WHERE published_at IS NULL;

INSERT INTO task_outbox (
    task_id,
    delivery_generation,
    event_type,
    payload,
    available_at,
    created_at
)
SELECT
    id,
    delivery_generation,
    'TASK_READY',
    jsonb_build_object(
        'task_id', id,
        'generation', delivery_generation
    ),
    next_run_at,
    NOW()
FROM worker_tasks
WHERE status IN ('PENDING', 'RETRY_SCHEDULED')
ON CONFLICT (task_id, delivery_generation) DO NOTHING;

ALTER TABLE service_tokens
    ADD COLUMN IF NOT EXISTS max_concurrency INT NOT NULL DEFAULT 1;

ALTER TABLE service_tokens
    DROP CONSTRAINT IF EXISTS chk_service_tokens_max_concurrency;

ALTER TABLE service_tokens
    ADD CONSTRAINT chk_service_tokens_max_concurrency
    CHECK (max_concurrency > 0);

CREATE TABLE IF NOT EXISTS service_token_leases (
    id BIGSERIAL PRIMARY KEY,
    token_id INT NOT NULL REFERENCES service_tokens(id) ON DELETE CASCADE,
    lease_owner VARCHAR(200) NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_expires_at TIMESTAMPTZ NOT NULL,
    UNIQUE (token_id, lease_owner)
);

CREATE INDEX IF NOT EXISTS idx_service_token_leases_active
    ON service_token_leases (token_id, lease_expires_at);

ALTER TABLE worker_dead_letters
    ADD COLUMN IF NOT EXISTS failure_class VARCHAR(100),
    ADD COLUMN IF NOT EXISTS delivery_generation INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS replayed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS replay_task_id INT,
    ADD COLUMN IF NOT EXISTS replay_generation INT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_dead_letters_replay_state
    ON worker_dead_letters (replayed_at, failed_at DESC);

COMMIT;
