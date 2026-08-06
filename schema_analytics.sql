BEGIN;

-- 1. Analytical Feed Core Table
CREATE TABLE IF NOT EXISTS social_insights_feed (
    insight_id BIGSERIAL PRIMARY KEY,
    original_tweet_id VARCHAR(64) NOT NULL UNIQUE,
    author_id VARCHAR(64) NOT NULL,
    author_handle VARCHAR(100) NOT NULL,
    text_content TEXT NOT NULL,
    engagement_likes INT DEFAULT 0,
    engagement_retweets INT DEFAULT 0,
    sentiment_label VARCHAR(20) DEFAULT 'PENDING',
    conversation_id VARCHAR(64),
    content_text_hash VARCHAR(64),
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_feed_content_text_hash ON social_insights_feed (content_text_hash);
CREATE INDEX IF NOT EXISTS idx_feed_ingested_at ON social_insights_feed (ingested_at DESC);

-- 2. Hardened Service Token Pool Table (Updated for Tier 2 Tracking)
CREATE TABLE IF NOT EXISTS service_tokens (
    id SERIAL PRIMARY KEY,
    token_key VARCHAR(100) NOT NULL UNIQUE,
    token_value TEXT NOT NULL,
    status VARCHAR(20) DEFAULT 'ACTIVE', -- ACTIVE, COOLDOWN, REVOKED
    cooldown_until TIMESTAMPTZ,
    last_error TEXT,
    last_leased_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tokens_leasing ON service_tokens (status, cooldown_until);

-- 3. High-Velocity Worker Task Queue Table
CREATE TABLE IF NOT EXISTS worker_tasks (
    id SERIAL PRIMARY KEY,
    task_type VARCHAR(50) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(20) DEFAULT 'PENDING', -- PENDING, RUNNING, RETRYING, DONE, DEAD_LETTER
    attempts INT DEFAULT 0,
    max_attempts INT DEFAULT 5,
    next_run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    leased_at TIMESTAMPTZ,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_queue ON worker_tasks (status, next_run_at ASC);

-- 4. Hourly AI Summary Briefs History Table
CREATE TABLE IF NOT EXISTS executive_briefs_history (
    brief_id BIGSERIAL PRIMARY KEY,
    summary_text TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_briefs_generated_at_desc ON executive_briefs_history (generated_at DESC);

-- 5. Persistent Dead Letter Archive Table (Updated for Hardened Recovery)
CREATE TABLE IF NOT EXISTS worker_dead_letters (
    id BIGSERIAL PRIMARY KEY,
    original_task_id INT NOT NULL,
    task_type VARCHAR(50) NOT NULL,
    payload JSONB NOT NULL,
    failed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_dead_letters_task_type ON worker_dead_letters (task_type);

-- 6. Keyword Hourly Rollups for Low-Overhead Anomaly Scanning
CREATE TABLE IF NOT EXISTS keyword_hourly_rollups (
    keyword VARCHAR(100) NOT NULL,
    window_timestamp TIMESTAMPTZ NOT NULL,
    tweet_count INT DEFAULT 1,
    engagement_likes_sum INT DEFAULT 0,
    PRIMARY KEY (keyword, window_timestamp)
);

CREATE INDEX IF NOT EXISTS idx_rollups_window ON keyword_hourly_rollups (window_timestamp DESC);

-- 7. Operational System Alerts Table
CREATE TABLE IF NOT EXISTS system_operational_alerts (
    alert_id BIGSERIAL PRIMARY KEY,
    keyword VARCHAR(100) NOT NULL,
    observed_volume INT NOT NULL,
    threshold_limit INT NOT NULL,
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_triggered_at ON system_operational_alerts (triggered_at DESC);

COMMIT;
