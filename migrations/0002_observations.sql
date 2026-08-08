BEGIN;

ALTER TABLE social_insights_feed
    ADD COLUMN IF NOT EXISTS platform VARCHAR(32) NOT NULL DEFAULT 'x',
    ADD COLUMN IF NOT EXISTS source_created_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;

UPDATE social_insights_feed
SET first_seen_at = COALESCE(first_seen_at, ingested_at),
    last_seen_at = COALESCE(last_seen_at, updated_at, ingested_at)
WHERE first_seen_at IS NULL OR last_seen_at IS NULL;

ALTER TABLE social_insights_feed
    ALTER COLUMN first_seen_at SET NOT NULL,
    ALTER COLUMN last_seen_at SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_feed_platform_object
    ON social_insights_feed (platform, original_tweet_id);
CREATE INDEX IF NOT EXISTS idx_feed_last_seen_at
    ON social_insights_feed (last_seen_at DESC);

CREATE TABLE IF NOT EXISTS social_ingestion_observations (
    observation_id BIGSERIAL PRIMARY KEY,
    observation_key TEXT NOT NULL UNIQUE,
    insight_id BIGINT NOT NULL
        REFERENCES social_insights_feed(insight_id) ON DELETE CASCADE,
    captured_at TIMESTAMPTZ NOT NULL,
    engagement_likes INT NOT NULL DEFAULT 0
        CHECK (engagement_likes >= 0),
    engagement_retweets INT NOT NULL DEFAULT 0
        CHECK (engagement_retweets >= 0),
    ingestion_task_id INT REFERENCES worker_tasks(id) ON DELETE SET NULL,
    task_generation INT,
    adapter_name VARCHAR(100),
    adapter_version VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_observations_insight_time
    ON social_ingestion_observations (insight_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_observations_task
    ON social_ingestion_observations (ingestion_task_id, task_generation);

CREATE TABLE IF NOT EXISTS keyword_rollup_contributions (
    insight_id BIGINT NOT NULL
        REFERENCES social_insights_feed(insight_id) ON DELETE CASCADE,
    keyword VARCHAR(100) NOT NULL,
    window_timestamp TIMESTAMPTZ NOT NULL,
    engagement_likes INT NOT NULL DEFAULT 0
        CHECK (engagement_likes >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (insight_id, keyword)
);

CREATE INDEX IF NOT EXISTS idx_keyword_contributions_bucket
    ON keyword_rollup_contributions (
        keyword,
        window_timestamp,
        engagement_likes
    );

-- Existing rollups were produced by non-idempotent observation increments.
-- Rebuild them from canonical objects so historical values stop carrying
-- artificial repeat-observation inflation.
TRUNCATE TABLE keyword_hourly_rollups;

INSERT INTO keyword_rollup_contributions (
    insight_id,
    keyword,
    window_timestamp,
    engagement_likes,
    updated_at
)
SELECT DISTINCT
    feed.insight_id,
    token.keyword,
    date_trunc('hour', feed.first_seen_at),
    feed.engagement_likes,
    NOW()
FROM social_insights_feed feed
CROSS JOIN LATERAL (
    SELECT lower(match[1]) AS keyword
    FROM regexp_matches(
        feed.text_content,
        '([[:alnum:]]{2,50})',
        'g'
    ) AS match
) token
WHERE token.keyword NOT IN (
    'this', 'that', 'with', 'from', 'they',
    'have', 'your', 'their', 'about'
)
ON CONFLICT (insight_id, keyword) DO UPDATE SET
    engagement_likes = GREATEST(
        keyword_rollup_contributions.engagement_likes,
        EXCLUDED.engagement_likes
    ),
    updated_at = NOW();

INSERT INTO keyword_hourly_rollups (
    keyword,
    window_timestamp,
    tweet_count,
    engagement_likes_sum
)
SELECT
    keyword,
    window_timestamp,
    COUNT(*)::int,
    COALESCE(SUM(engagement_likes), 0)::int
FROM keyword_rollup_contributions
GROUP BY keyword, window_timestamp
ON CONFLICT (keyword, window_timestamp) DO UPDATE SET
    tweet_count = EXCLUDED.tweet_count,
    engagement_likes_sum = EXCLUDED.engagement_likes_sum;

COMMIT;
