BEGIN;

CREATE TABLE IF NOT EXISTS social_insights_feed (
    insight_id BIGSERIAL PRIMARY KEY,
    original_tweet_id VARCHAR(64) NOT NULL UNIQUE,
    author_id VARCHAR(64) NOT NULL,
    author_handle VARCHAR(100) NOT NULL,
    text_content TEXT NOT NULL,
    engagement_likes INT NOT NULL DEFAULT 0 CHECK (engagement_likes >= 0),
    engagement_retweets INT NOT NULL DEFAULT 0 CHECK (engagement_retweets >= 0),
    sentiment_label VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    conversation_id VARCHAR(64),
    content_text_hash VARCHAR(64),
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_feed_content_text_hash
    ON social_insights_feed (content_text_hash);
CREATE INDEX IF NOT EXISTS idx_feed_ingested_at
    ON social_insights_feed (ingested_at DESC);

CREATE TABLE IF NOT EXISTS service_tokens (
    id SERIAL PRIMARY KEY,
    token_key VARCHAR(100) NOT NULL UNIQUE,
    token_value TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    cooldown_until TIMESTAMPTZ,
    last_error TEXT,
    last_leased_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    max_concurrency INT NOT NULL DEFAULT 1
        CHECK (max_concurrency > 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tokens_leasing
    ON service_tokens (status, cooldown_until);

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

CREATE TABLE IF NOT EXISTS worker_tasks (
    id SERIAL PRIMARY KEY,
    task_type VARCHAR(50) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
    attempts INT NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts INT NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
    next_run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    idempotency_key TEXT NOT NULL UNIQUE,
    priority INT NOT NULL DEFAULT 100,
    delivery_generation INT NOT NULL DEFAULT 0 CHECK (delivery_generation >= 0),
    enqueued_at TIMESTAMPTZ,
    lease_owner VARCHAR(200),
    lease_started_at TIMESTAMPTZ,
    lease_expires_at TIMESTAMPTZ,
    leased_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    last_error TEXT,
    last_failure_class VARCHAR(100),
    result_metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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

CREATE TABLE IF NOT EXISTS executive_briefs_history (
    brief_id BIGSERIAL PRIMARY KEY,
    summary_text TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_briefs_generated_at_desc
    ON executive_briefs_history (generated_at DESC);

CREATE TABLE IF NOT EXISTS worker_dead_letters (
    id BIGSERIAL PRIMARY KEY,
    original_task_id INT NOT NULL,
    task_type VARCHAR(50) NOT NULL,
    payload JSONB NOT NULL,
    failed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT,
    failure_class VARCHAR(100),
    delivery_generation INT NOT NULL DEFAULT 0,
    replayed_at TIMESTAMPTZ,
    replay_task_id INT,
    replay_generation INT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_dead_letters_task_type
    ON worker_dead_letters (task_type);
CREATE INDEX IF NOT EXISTS idx_dead_letters_replay_state
    ON worker_dead_letters (replayed_at, failed_at DESC);

CREATE TABLE IF NOT EXISTS keyword_hourly_rollups (
    keyword VARCHAR(100) NOT NULL,
    window_timestamp TIMESTAMPTZ NOT NULL,
    tweet_count INT NOT NULL DEFAULT 1,
    engagement_likes_sum INT NOT NULL DEFAULT 0,
    PRIMARY KEY (keyword, window_timestamp)
);

CREATE INDEX IF NOT EXISTS idx_rollups_window
    ON keyword_hourly_rollups (window_timestamp DESC);

CREATE TABLE IF NOT EXISTS system_operational_alerts (
    alert_id BIGSERIAL PRIMARY KEY,
    keyword VARCHAR(100) NOT NULL,
    observed_volume INT NOT NULL,
    threshold_limit INT NOT NULL,
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_triggered_at
    ON system_operational_alerts (triggered_at DESC);

COMMIT;
