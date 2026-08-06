from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Literal, Optional

import asyncpg
from pydantic import BaseModel, Field, field_validator


SentimentLabel = Literal["PENDING", "POSITIVE", "NEGATIVE", "NEUTRAL"]


class SocialMediaDocument(BaseModel):
    insight_id: Optional[int] = None
    original_tweet_id: str = Field(min_length=1, max_length=64)
    author_id: str = Field(min_length=1, max_length=64)
    author_handle: str = Field(min_length=1, max_length=100)
    text_content: str = Field(min_length=1)
    engagement_likes: int = Field(default=0, ge=0)
    engagement_retweets: int = Field(default=0, ge=0)
    sentiment_label: SentimentLabel = "PENDING"
    conversation_id: Optional[str] = Field(default=None, max_length=64)
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
            raise ValueError("sentiment_label must be one of PENDING, POSITIVE, NEGATIVE, NEUTRAL")
        return label

    def compute_clean_text_hash(self) -> str:
        """
        Normalizes the incoming text block by stripping spaces, punctuation,
        and casing to create a stable content fingerprint.
        """
        clean_text = self.text_content.lower()
        clean_text = re.sub(r"http\S+|@\S+", "", clean_text)
        clean_text = re.sub(r"[^a-z0-9]", "", clean_text)
        if not clean_text:
            clean_text = self.text_content.lower().strip()
        return hashlib.sha256(clean_text.encode("utf-8")).hexdigest()


async def upsert_insight_record(pool: asyncpg.Pool, data: SocialMediaDocument) -> None:
    """
    Saves or updates a text record inside the analytical store.
    Uses a content hash to merge near-duplicate text entries.
    """
    calculated_hash = data.compute_clean_text_hash()

    async with pool.acquire() as conn:
        existing_phrase_row = await conn.fetchrow(
            """
            SELECT original_tweet_id
            FROM social_insights_feed
            WHERE content_text_hash = $1
            LIMIT 1;
            """,
            calculated_hash,
        )

        if existing_phrase_row:
            target_id = existing_phrase_row["original_tweet_id"]
            await conn.execute(
                """
                UPDATE social_insights_feed
                SET engagement_likes = engagement_likes + $1,
                    engagement_retweets = engagement_retweets + $2,
                    updated_at = NOW()
                WHERE original_tweet_id = $3;
                """,
                data.engagement_likes,
                data.engagement_retweets,
                target_id,
            )
        else:
            await conn.execute(
                """
                INSERT INTO social_insights_feed (
                    original_tweet_id,
                    author_id,
                    author_handle,
                    text_content,
                    engagement_likes,
                    engagement_retweets,
                    conversation_id,
                    sentiment_label,
                    content_text_hash,
                    ingested_at,
                    updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), NOW())
                ON CONFLICT (original_tweet_id) DO UPDATE SET
                    author_id = EXCLUDED.author_id,
                    author_handle = EXCLUDED.author_handle,
                    text_content = EXCLUDED.text_content,
                    engagement_likes = GREATEST(social_insights_feed.engagement_likes, EXCLUDED.engagement_likes),
                    engagement_retweets = GREATEST(social_insights_feed.engagement_retweets, EXCLUDED.engagement_retweets),
                    conversation_id = COALESCE(EXCLUDED.conversation_id, social_insights_feed.conversation_id),
                    sentiment_label = EXCLUDED.sentiment_label,
                    content_text_hash = EXCLUDED.content_text_hash,
                    ingested_at = NOW(),
                    updated_at = NOW();
                """,
                data.original_tweet_id,
                data.author_id,
                data.author_handle,
                data.text_content,
                data.engagement_likes,
                data.engagement_retweets,
                data.conversation_id,
                data.sentiment_label,
                calculated_hash,
            )

        # --- HIGH-VELOCITY PRE-AGGREGATION PIPELINE INJECTION ---
        # Truncate the ingestion timestamp to the exact hour mark for high-speed tracking
        truncated_hour = (
            data.ingested_at.replace(minute=0, second=0, microsecond=0)
            if data.ingested_at
            else datetime.now().replace(minute=0, second=0, microsecond=0)
        )
        
        # Split text content into basic word tokens to track frequency metrics natively inside DB
        extracted_words = set(re.findall(r"\b[a-z]{4,15}\b", data.text_content.lower()))
        common_stop_words = {"this", "that", "with", "from", "they", "have", "your", "their", "about"}
        filtered_keywords = [word for word in extracted_words if word not in common_stop_words]

        # Bulk upsert the frequencies into our fast keyword analytics rollups table
        for word in filtered_keywords:
            await conn.execute(
                """
                INSERT INTO keyword_hourly_rollups (keyword, window_timestamp, tweet_count, engagement_likes_sum)
                VALUES ($1, $2, 1, $3)
                ON CONFLICT (keyword, window_timestamp) DO UPDATE SET
                    tweet_count = keyword_hourly_rollups.tweet_count + 1,
                    engagement_likes_sum = keyword_hourly_rollups.engagement_likes_sum + EXCLUDED.engagement_likes_sum;
                """,
                word,
                truncated_hour,
                data.engagement_likes
            )
