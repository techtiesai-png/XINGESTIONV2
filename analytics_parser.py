from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Literal, Optional

import asyncpg
from pydantic import BaseModel, Field, field_validator


SentimentLabel = Literal["PENDING", "POSITIVE", "NEGATIVE", "NEUTRAL"]

_STOP_WORDS = {
    "this",
    "that",
    "with",
    "from",
    "they",
    "have",
    "your",
    "their",
    "about",
}


class SocialMediaDocument(BaseModel):
    insight_id: Optional[int] = None
    platform: str = Field(default="x", min_length=1, max_length=32)
    original_tweet_id: str = Field(min_length=1, max_length=64)
    author_id: str = Field(min_length=1, max_length=128)
    author_handle: str = Field(min_length=1, max_length=100)
    text_content: str = Field(min_length=1)
    engagement_likes: int = Field(default=0, ge=0)
    engagement_retweets: int = Field(default=0, ge=0)
    sentiment_label: SentimentLabel = "PENDING"
    conversation_id: Optional[str] = Field(default=None, max_length=128)
    source_created_at: Optional[datetime] = None
    captured_at: Optional[datetime] = None
    ingested_at: Optional[datetime] = None
    content_text_hash: Optional[str] = None

    @field_validator("text_content", mode="before")
    @classmethod
    def normalize_text_content(cls, value: object) -> str:
        text = "" if value is None else str(value)
        text = text.replace("\u00a0", " ")
        text = " ".join(text.split())
        for prefix in ("RT @", "Source:", "Metadata:", "Tags:", "Posted via"):
            if text.startswith(prefix):
                text = text[len(prefix) :].strip()
        return text

    @field_validator("author_handle", mode="before")
    @classmethod
    def normalize_author_handle(cls, value: object) -> str:
        handle = "" if value is None else str(value).strip()
        return handle[1:] if handle.startswith("@") else handle

    @field_validator("sentiment_label", mode="before")
    @classmethod
    def normalize_sentiment_label(cls, value: object) -> str:
        label = "PENDING" if value is None else str(value).strip().upper()
        if label not in {"PENDING", "POSITIVE", "NEGATIVE", "NEUTRAL"}:
            raise ValueError(
                "sentiment_label must be one of "
                "PENDING, POSITIVE, NEGATIVE, NEUTRAL"
            )
        return label

    def compute_clean_text_hash(self) -> str:
        clean_text = self.text_content.casefold()
        clean_text = re.sub(r"https?://\S+|@\S+", "", clean_text)
        clean_text = "".join(ch for ch in clean_text if ch.isalnum())
        if not clean_text:
            clean_text = self.text_content.casefold().strip()
        return hashlib.sha256(clean_text.encode("utf-8")).hexdigest()


def extract_keywords(text: str) -> set[str]:
    # Unicode-aware baseline tokenization. Languages without whitespace word
    # boundaries need a dedicated language tokenizer in the analytics layer;
    # ingestion correctness must not depend on that future enrichment.
    tokens = re.findall(r"(?u)\b[^\W_]{2,50}\b", text.casefold())
    return {
        token
        for token in tokens
        if token not in _STOP_WORDS and not token.isdecimal()
    }


async def _rebuild_rollup_bucket(
    conn: asyncpg.Connection,
    *,
    keyword: str,
    window_timestamp: datetime,
) -> None:
    aggregate = await conn.fetchrow(
        """
        SELECT
            COUNT(*)::int AS tweet_count,
            COALESCE(SUM(engagement_likes), 0)::bigint
                AS engagement_likes_sum
        FROM keyword_rollup_contributions
        WHERE keyword = $1
          AND window_timestamp = $2;
        """,
        keyword,
        window_timestamp,
    )
    count = int(aggregate["tweet_count"] or 0)
    if count == 0:
        await conn.execute(
            """
            DELETE FROM keyword_hourly_rollups
            WHERE keyword = $1 AND window_timestamp = $2;
            """,
            keyword,
            window_timestamp,
        )
        return

    await conn.execute(
        """
        INSERT INTO keyword_hourly_rollups (
            keyword,
            window_timestamp,
            tweet_count,
            engagement_likes_sum
        )
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (keyword, window_timestamp) DO UPDATE SET
            tweet_count = EXCLUDED.tweet_count,
            engagement_likes_sum = EXCLUDED.engagement_likes_sum;
        """,
        keyword,
        window_timestamp,
        count,
        int(aggregate["engagement_likes_sum"] or 0),
    )


async def upsert_insight_record(
    pool: asyncpg.Pool,
    data: SocialMediaDocument,
    *,
    observation_key: str | None = None,
    ingestion_task_id: int | None = None,
    task_generation: int | None = None,
    adapter_name: str | None = None,
    adapter_version: str | None = None,
) -> None:
    """Persist a canonical source object plus an idempotent observation.

    Object identity is the platform object ID. Content hashes are retained for
    grouping/search but never merge distinct source objects. Engagement
    counters use observed maxima and are also preserved as observation rows.
    """

    captured_at = data.captured_at or datetime.now(timezone.utc)
    calculated_hash = data.compute_clean_text_hash()
    keywords = extract_keywords(data.text_content)
    observation_key = observation_key or (
        f"{data.platform}:{data.original_tweet_id}:"
        f"{captured_at.isoformat()}"
    )

    async with pool.acquire() as conn:
        async with conn.transaction():
            canonical = await conn.fetchrow(
                """
                INSERT INTO social_insights_feed (
                    platform,
                    original_tweet_id,
                    author_id,
                    author_handle,
                    text_content,
                    engagement_likes,
                    engagement_retweets,
                    conversation_id,
                    sentiment_label,
                    content_text_hash,
                    source_created_at,
                    ingested_at,
                    first_seen_at,
                    last_seen_at,
                    updated_at
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                    $12, $12, $12, $12
                )
                ON CONFLICT (original_tweet_id) DO UPDATE SET
                    platform = EXCLUDED.platform,
                    author_id = EXCLUDED.author_id,
                    author_handle = EXCLUDED.author_handle,
                    text_content = EXCLUDED.text_content,
                    engagement_likes = GREATEST(
                        social_insights_feed.engagement_likes,
                        EXCLUDED.engagement_likes
                    ),
                    engagement_retweets = GREATEST(
                        social_insights_feed.engagement_retweets,
                        EXCLUDED.engagement_retweets
                    ),
                    conversation_id = COALESCE(
                        EXCLUDED.conversation_id,
                        social_insights_feed.conversation_id
                    ),
                    sentiment_label = EXCLUDED.sentiment_label,
                    content_text_hash = EXCLUDED.content_text_hash,
                    source_created_at = COALESCE(
                        social_insights_feed.source_created_at,
                        EXCLUDED.source_created_at
                    ),
                    last_seen_at = GREATEST(
                        social_insights_feed.last_seen_at,
                        EXCLUDED.last_seen_at
                    ),
                    updated_at = NOW()
                RETURNING insight_id, first_seen_at,
                          engagement_likes, engagement_retweets;
                """,
                data.platform,
                data.original_tweet_id,
                data.author_id,
                data.author_handle,
                data.text_content,
                data.engagement_likes,
                data.engagement_retweets,
                data.conversation_id,
                data.sentiment_label,
                calculated_hash,
                data.source_created_at,
                captured_at,
            )
            insight_id = int(canonical["insight_id"])
            first_seen_at = canonical["first_seen_at"]
            canonical_likes = int(canonical["engagement_likes"] or 0)

            await conn.execute(
                """
                INSERT INTO social_ingestion_observations (
                    observation_key,
                    insight_id,
                    captured_at,
                    engagement_likes,
                    engagement_retweets,
                    ingestion_task_id,
                    task_generation,
                    adapter_name,
                    adapter_version
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (observation_key) DO NOTHING;
                """,
                observation_key,
                insight_id,
                captured_at,
                data.engagement_likes,
                data.engagement_retweets,
                ingestion_task_id,
                task_generation,
                adapter_name,
                adapter_version,
            )

            bucket = first_seen_at.replace(
                minute=0,
                second=0,
                microsecond=0,
            )
            old_contributions = await conn.fetch(
                """
                SELECT keyword, window_timestamp
                FROM keyword_rollup_contributions
                WHERE insight_id = $1;
                """,
                insight_id,
            )
            old_buckets = {
                (str(row["keyword"]), row["window_timestamp"])
                for row in old_contributions
            }

            if keywords:
                await conn.executemany(
                    """
                    INSERT INTO keyword_rollup_contributions (
                        insight_id,
                        keyword,
                        window_timestamp,
                        engagement_likes,
                        updated_at
                    )
                    VALUES ($1, $2, $3, $4, NOW())
                    ON CONFLICT (insight_id, keyword) DO UPDATE SET
                        engagement_likes = GREATEST(
                            keyword_rollup_contributions.engagement_likes,
                            EXCLUDED.engagement_likes
                        ),
                        updated_at = NOW();
                    """,
                    [
                        (insight_id, keyword, bucket, canonical_likes)
                        for keyword in sorted(keywords)
                    ],
                )
                await conn.execute(
                    """
                    DELETE FROM keyword_rollup_contributions
                    WHERE insight_id = $1
                      AND NOT (keyword = ANY($2::text[]));
                    """,
                    insight_id,
                    sorted(keywords),
                )
            else:
                await conn.execute(
                    """
                    DELETE FROM keyword_rollup_contributions
                    WHERE insight_id = $1;
                    """,
                    insight_id,
                )

            affected = old_buckets | {
                (keyword, bucket) for keyword in keywords
            }
            for keyword, window_timestamp in sorted(
                affected,
                key=lambda item: (item[1], item[0]),
            ):
                await _rebuild_rollup_bucket(
                    conn,
                    keyword=keyword,
                    window_timestamp=window_timestamp,
                )
