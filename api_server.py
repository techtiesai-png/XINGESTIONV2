from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Optional

import asyncpg
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from datetime import datetime, timezone

import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("api_server")



DATABASE_DSN = os.getenv("DATABASE_DSN")


class AlertResponse(BaseModel):
    id: int
    target_keyword: str
    event_volume: int
    threshold_velocity: int
    triggered_at: datetime


class TrendResponse(BaseModel):
    topic_name: str
    weight_score: int
    cluster_id: int


class BriefResponse(BaseModel):
    brief_id: int
    summary_text: str
    generated_at: datetime


class SystemHealthResponse(BaseModel):
    total_tokens: int
    active_tokens: int
    cooldown_tokens: int
    pending_tasks: int
    running_tasks: int
    dead_letter_count: int
    timestamp: datetime


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not DATABASE_DSN:
        app.state.pool = None
        yield
        return

    try:
        app.state.pool = await asyncpg.create_pool(
            dsn=DATABASE_DSN,
            min_size=1,
            max_size=10,
            timeout=5.0,
        )
    except Exception:
        app.state.pool = None
        yield
        return

    try:
        yield
    finally:
        pool = getattr(app.state, "pool", None)
        if pool is not None:
            await pool.close()
            app.state.pool = None


app = FastAPI(lifespan=lifespan)


async def _require_pool(request: Request) -> asyncpg.Pool:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    return pool


async def _fetch_all(request: Request, query: str, *args: Any) -> list[asyncpg.Record]:
    pool = await _require_pool(request)
    async with pool.acquire() as conn:
        return await conn.fetch(query, *args)


@app.get("/api/v1/alerts/live", response_model=list[AlertResponse])
async def live_alerts(request: Request):
    query = """
        SELECT
            wt.id AS id,
            COALESCE(si.sentiment_label, 'PENDING') AS target_keyword,
            COALESCE(si.engagement_likes, 0)::int AS event_volume,
            GREATEST(COALESCE(si.engagement_likes, 0), 0)::int AS threshold_velocity,
            COALESCE(si.ingested_at, wt.leased_at, NOW()) AS triggered_at
        FROM worker_tasks wt
        LEFT JOIN social_insights_feed si
            ON si.original_tweet_id = COALESCE(
                wt.payload->>'original_tweet_id',
                wt.payload->>'tweet_id'
            )
        WHERE wt.status IN ('RUNNING', 'RETRYING', 'DEAD_LETTER', 'DONE')
        ORDER BY triggered_at DESC
        LIMIT 100
    """
    try:
        rows = await _fetch_all(request, query)
        return [AlertResponse(**dict(row)) for row in rows]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="failed to load live alerts") from exc


@app.get("/api/v1/trends/spikes", response_model=list[TrendResponse])
async def trend_spikes(request: Request):
    query = """
        WITH recent AS (
            SELECT
                lower(regexp_replace(trim(word), '[^a-zA-Z0-9#]+', '', 'g')) AS topic_name
            FROM social_insights_feed,
                 regexp_split_to_table(text_content, '\\s+') AS word
            WHERE ingested_at >= NOW() - INTERVAL '6 hours'
        ),
        ranked AS (
            SELECT
                topic_name,
                COUNT(*)::int AS weight_score
            FROM recent
            WHERE topic_name <> ''
            GROUP BY topic_name
        ),
        clustered AS (
            SELECT
                topic_name,
                weight_score,
                ROW_NUMBER() OVER (ORDER BY weight_score DESC, topic_name ASC)::int AS cluster_id
            FROM ranked
        )
        SELECT topic_name, weight_score, cluster_id
        FROM clustered
        ORDER BY weight_score DESC, topic_name ASC
        LIMIT 50
    """
    try:
        rows = await _fetch_all(request, query)
        return [TrendResponse(**dict(row)) for row in rows]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="failed to load trend spikes") from exc


@app.get("/api/v1/briefs/latest", response_model=BriefResponse)
async def latest_brief(request: Request):
    query = """
        SELECT
            brief_id,
            summary_text,
            generated_at
        FROM executive_briefs_history
        ORDER BY generated_at DESC
        LIMIT 1
    """
    try:
        rows = await _fetch_all(request, query)
        if not rows:
            return BriefResponse(
                brief_id=0,
                summary_text="",
                generated_at=datetime.utcnow(),
            )
        return BriefResponse(**dict(rows[0]))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="failed to load latest brief") from exc


@app.get("/api/v1/system/health", response_model=SystemHealthResponse)
async def get_system_operational_health():
    """
    Computes real-time scalar telemetry metrics across our asynchronous pipeline infrastructure pools.
    """
    pool = app.state.pool
    try:
        async with pool.acquire() as conn:
            # 1. Aggregate account token lifecycle states natively inside SQL
            token_stats = await conn.fetchrow(
                """
                SELECT 
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'ACTIVE') as active,
                    COUNT(*) FILTER (WHERE status = 'COOLDOWN') as cooldown
                FROM service_tokens;
                """
            )
            
            # 2. Aggregate current execution task tracking statuses
            task_stats = await conn.fetchrow(
                """
                SELECT 
                    COUNT(*) FILTER (WHERE status IN ('PENDING', 'RETRYING')) as pending,
                    COUNT(*) FILTER (WHERE status = 'RUNNING') as running
                FROM worker_tasks;
                """
            )
            
            # 3. Read exact item lengths from the new persistent dead-letter rows table
            dead_letter_count = await conn.fetchval("SELECT COUNT(*) FROM worker_dead_letters;")

        return SystemHealthResponse(
            total_tokens=int(token_stats["total"] or 0),
            active_tokens=int(token_stats["active"] or 0),
            cooldown_tokens=int(token_stats["cooldown"] or 0),
            pending_tasks=int(task_stats["pending"] or 0),
            running_tasks=int(task_stats["running"] or 0),
            dead_letter_count=int(dead_letter_count or 0),
            timestamp=datetime.now(timezone.utc)
        )

    except Exception as db_exc:
        # Gracefully handle database drops by returning an enterprise-compliant HTTP 503 response
        logger.error(f"Telemetry collector failed to query database aggregates: {db_exc}")
        raise HTTPException(
            status_code=503, 
            detail="System telemetry service unavailable due to active database pool connection interruptions."
        )



@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api_server:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=False,
    )
