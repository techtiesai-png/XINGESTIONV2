from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import signal
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import redis.asyncio as aioredis
import asyncpg
import httpx
from twikit import Client

# Import custom validation filters and analytical storage upserts
from analytics_parser import SocialMediaDocument, upsert_insight_record


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_payload["exception_trace"] = self.formatException(record.exc_info)
        return json.dumps(log_payload)


# Initialize standard output streams and attach our JSON formatter natively
log_handler = logging.StreamHandler()
log_handler.setFormatter(JSONFormatter())
logger = logging.getLogger("worker_core")
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)
# Prevent duplicate logging bubbles
logger.propagate = False

DATABASE_DSN = os.getenv(
    "DATABASE_DSN",
    "postgresql://app_user:app_password@localhost:5432/appdb",
)

WORKER_CONCURRENCY = int(os.getenv("WORKER_CONCURRENCY", "4"))
IDLE_SLEEP_SECONDS = float(os.getenv("IDLE_SLEEP_SECONDS", "0.5"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "15.0"))
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "300"))
MAX_POOL_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", "20"))
MIN_POOL_SIZE = int(os.getenv("DB_POOL_MIN_SIZE", "2"))


@dataclass(slots=True)
class TokenLease:
    id: int
    token_key: str
    token_value: str


@dataclass(slots=True)
class WorkerTask:
    id: int
    task_type: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def backoff_delay_seconds(attempt: int, base: float = 1.0, cap: float = 60.0) -> float:
    jitter = random.uniform(0.0, 0.25)
    return min(cap, base * (2 ** max(0, attempt - 1))) + jitter


class TokenRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def checkout_token(self) -> Optional[TokenLease]:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    WITH candidate AS (
                        SELECT id
                        FROM service_tokens
                        WHERE status = 'ACTIVE'
                          AND (cooldown_until IS NULL OR cooldown_until <= NOW())
                        ORDER BY last_leased_at NULLS FIRST, id ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE service_tokens st
                    SET last_leased_at = NOW()
                    FROM candidate
                    WHERE st.id = candidate.id
                    RETURNING st.id, st.token_key, st.token_value
                    """
                )
                if row is None:
                    return None
                return TokenLease(
                    id=row["id"],
                    token_key=row["token_key"],
                    token_value=row["token_value"],
                )

    async def mark_token_active(self, token_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE service_tokens
                SET status = 'ACTIVE',
                    cooldown_until = NULL,
                    last_error = NULL,
                    last_used_at = NOW()
                WHERE id = $1
                """,
                token_id,
            )

    async def mark_token_cooldown(self, token_id: int, error_message: str) -> None:
        cooldown_until = utcnow() + timedelta(seconds=COOLDOWN_SECONDS)
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE service_tokens
                SET status = 'COOLDOWN',
                    cooldown_until = $2,
                    last_error = $3
                WHERE id = $1
                """,
                token_id,
                cooldown_until,
                error_message[:2000],
            )


class TaskRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        # FIX: Ensure we use the explicit, asynchronous Redis connection factory method natively
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.redis = aioredis.from_url(redis_url, decode_responses=True)

    async def checkout_task(self, worker_id: str) -> Optional[WorkerTask]:
        """
        Pulls and pops active keyword tasks from a high-speed Redis memory stack in microseconds.
        """
        try:
            # Atomically pop an item from the left side of the Redis list queue
            task_raw = await self.redis.lpop("queue:x_tasks")  # type: ignore
            if not isinstance(task_raw, str):
                return None

            task_data = json.loads(task_raw)
            # Map back to a clean WorkerTask configuration entity layer
            return WorkerTask(
                id=int(task_data["id"]),
                task_type=str(task_data["task_type"]),
                payload=dict(task_data["payload"]),
                attempts=int(task_data.get("attempts", 0)),
                max_attempts=int(task_data.get("max_attempts", 5))
            )
        except Exception as exc:
            logger.error(f"Redis Queue Checkout error: {exc}")
            return None

    async def complete_task(self, task_id: int) -> None:
        """
        Logs completed status to the permanent database for historical analytics.
        """
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE worker_tasks SET status = 'DONE', leased_at = NULL WHERE id = $1;", task_id)

    async def retry_task(self, task_id: int, attempts: int, error_message: str) -> None:
        """
        Pushes a failed task back onto the right side of the Redis queue after updating DB metrics.
        """
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE worker_tasks SET status = 'RETRYING', attempts = attempts + 1, last_error = $2 WHERE id = $1;",
                task_id, error_message[:2000]
            )
            row = await conn.fetchrow("SELECT id, task_type, payload, attempts, max_attempts FROM worker_tasks WHERE id = $1;", task_id)

            if row:
                task_payload = {
                    "id": row["id"],
                    "task_type": row["task_type"],
                    "payload": json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"],
                    "attempts": row["attempts"],
                    "max_attempts": row["max_attempts"]
                }
                # Re-queue back to Redis memory pool for an automated retry run
                # FIX: This is now properly awaited with the async Redis client
                # Append the type ignore flag to clear the static type-stub warning
                await self.redis.rpush("queue:x_tasks", json.dumps(task_payload))  # type: ignore

    async def dead_letter_task(self, task_id: int, error_message: str) -> None:
        """
        Archives failed items to permanent storage when attempts exhaust.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow("SELECT task_type, payload FROM worker_tasks WHERE id = $1 FOR UPDATE;", task_id)
                if row:
                    await conn.execute(
                        "INSERT INTO worker_dead_letters (original_task_id, task_type, payload, last_error) VALUES ($1, $2, $3, $4);",
                        task_id, row["task_type"], row["payload"], error_message[:2000]
                    )
                await conn.execute("UPDATE worker_tasks SET status = 'DEAD_LETTER', last_error = $2 WHERE id = $1;", task_id, error_message[:2000])


class HttpWorker:
    def __init__(self, timeout_seconds: float = REQUEST_TIMEOUT_SECONDS):
        self.timeout_seconds = timeout_seconds
        proxy_string = os.getenv("HTTP_PROXY") or None
        self.proxy = httpx.Proxy(url=proxy_string) if proxy_string else None

    async def _execute_tier1_http(self, token: TokenLease, search_query: str) -> list[dict[str, Any]]:
        """
        TIER 1: Raw Asynchronous HTTP Protocol Emulation (Primary Speed Layer).
        """
        client = Client(language="en-US")
        try:
            cookies_dict = json.loads(token.token_value)
            client.set_cookies(cookies_dict)
        except Exception as json_exc:
            raise ValueError(f"Token ID {token.id} contains a corrupted cookie string: {json_exc}")

        if self.proxy:
            v1_layer = getattr(client, "v1", None)
            if v1_layer and hasattr(v1_layer, "client"):
                v1_layer.client.proxies = {"all://": str(self.proxy.url)}
            v2_layer = getattr(client, "v2", None)
            if v2_layer and hasattr(v2_layer, "client"):
                v2_layer.client.proxies = {"all://": str(self.proxy.url)}

        tweets_result = await client.search_tweet(query=search_query, product="Latest", count=20)

        collected = []
        for tweet in tweets_result:
            collected.append({
                "original_tweet_id": str(tweet.id),
                "author_id": str(tweet.user.id),
                "author_handle": str(tweet.user.screen_name),
                "text_content": str(tweet.text),
                "engagement_likes": int(tweet.favorite_count or 0),
                "engagement_retweets": int(tweet.retweet_count or 0),
                "conversation_id": str(getattr(tweet, "conversation_id", None) or tweet.id),
                "sentiment_label": "NEUTRAL"
            })
        return collected

    async def _execute_tier3_browser(self, search_query: str) -> list[dict[str, Any]]:
        """
        TIER 3: Headless Chromium Browser Session (The Nuclear Fallback Option).
        """
        logger.warning(f"CRITICAL FALLBACK: Initializing Tier 3 Browser Automation for query: [{search_query}]")
        from playwright.async_api import async_playwright

        collected_tweets = []
        async with async_playwright() as p:
            browser_args = {}
            if self.proxy:
                browser_args["proxy"] = {"server": str(self.proxy.url)}
            browser = await p.chromium.launch(headless=True, **browser_args)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            target_url = f"x.com{search_query}&f=live"
            await page.goto(target_url, timeout=30000)
            await page.wait_for_timeout(3000)
            tweet_elements = await page.locator('article[data-testid="tweet"]').all()

            for element in tweet_elements[:15]:
                try:
                    text = await element.locator('div[data-testid="tweetText"]').inner_text()
                    generated_seed = hashlib.md5(text.encode('utf-8')).hexdigest()[:16]
                    collected_tweets.append({
                        "original_tweet_id": f"brw_{generated_seed}",
                        "author_id": "unknown_browser_node",
                        "author_handle": "anonymous_stream",
                        "text_content": str(text),
                        "engagement_likes": 0,
                        "engagement_retweets": 0,
                        "conversation_id": f"brw_{generated_seed}",
                        "sentiment_label": "NEUTRAL"
                    })
                except Exception:
                    continue
            await browser.close()
        return collected_tweets

    async def execute(self, token_repo: TokenRepository, token: TokenLease, task: WorkerTask) -> dict[str, Any]:
        """
        Master Ingestion Orchestrator managing our unified 3-Tier Fallback Machine loops.
        """
        search_query = task.payload.get("search_keyword")
        if not search_query:
            raise ValueError(f"Task {task.id} payload is missing 'search_keyword' parameters.")

        # --- HIGH-SPEED RUNTIME MOCK SHORT-CIRCUIT GUARD ---
        if os.getenv("MOCK_MODE", "false").lower() == "true":
            logger.info(f"MOCK ACTIVE: Short-circuiting extraction layers for keyword: [{search_query}]")
            # Return synthetic payload frames directly to satisfy parsing validators
            return {
                "results": [
                    {
                        "original_tweet_id": f"mock_tw_{task.id}_{i}",
                        "author_id": f"mock_auth_{i}",
                        "author_handle": f"mock_handle_{i}",
                        "text_content": f"Automated simulation testing payload content text tracking key phrase: {search_query}",
                        "engagement_likes": 100 * i,
                        "engagement_retweets": 25 * i,
                        "conversation_id": f"mock_conv_{task.id}",
                        "sentiment_label": "NEUTRAL"
                    }
                    for i in range(1, 4)  # Generates 3 mock tweets per keyword cleanly
                ]
            }

        # --- RUN TIER 1 (REAL PRODUCTION PIPELINE) ---
        try:
            logger.info(f"Executing Ingestion Core Loop via Tier 1 Protocol Engine using token: {token.id}")
            results = await self._execute_tier1_http(token, search_query)
            return {"results": results}
        except httpx.HTTPStatusError as auth_exc:
            status_code = auth_exc.response.status_code
            logger.error(f"Tier 1 Rejected: Platform HTTP Error {status_code}. Shifting states...")

            # --- RUN TIER 2 (ACCOUNT FAILOVER RESILIENCY) ---
            logger.warning(f"TIER 2 ACTIVATE: Benching token {token.id} into cooldown and checking out failover token...")
            await token_repo.mark_token_cooldown(token.id, f"tier1_http_error_{status_code}")
            failover_token = await token_repo.checkout_token()

            if failover_token:
                try:
                    logger.info(f"Retrying Tier 1 operations using fallback account token identifier: {failover_token.id}")
                    results = await self._execute_tier1_http(failover_token, search_query)
                    await token_repo.mark_token_active(failover_token.id)
                    return {"results": results}
                except Exception as tier2_exc:
                    logger.error(f"Tier 2 Account failover session also collapsed: {tier2_exc}")
                    await token_repo.mark_token_cooldown(failover_token.id, f"tier2_failover_error:{tier2_exc}")

            # --- RUN TIER 3 (THE NUCLEAR BROWSER OPTION) ---
            try:
                results = await self._execute_tier3_browser(search_query)
                return {"results": results}
            except Exception as tier3_exc:
                logger.critical(f"FATAL SYSTEM FAILURE: Tier 3 Browser automation engine completely blocked: {tier3_exc}")
                raise RuntimeError(f"All 3 Ingestion Tiers exhausted for Task {task.id}: {tier3_exc}")

        except Exception as general_network_exc:
            logger.warning(f"Transient infrastructure connection drops caught in master loop: {general_network_exc}")
            raise


async def token_cooldown_recovery_loop(token_repo: TokenRepository, shutdown_event: asyncio.Event) -> None:
    """
    Independent background daemon that automatically scans the database every 30 seconds
    and restores benched COOLDOWN accounts back to ACTIVE once their timer expires [1.1].
    """
    logger.info("Automated Token Cooldown Recovery Loop initialized successfully.")
    while not shutdown_event.is_set():
        try:
            async with token_repo.pool.acquire() as conn:
                result = await conn.execute(
                    """
                    UPDATE service_tokens
                    SET status = 'ACTIVE',
                        cooldown_until = NULL,
                        last_error = NULL,
                        updated_at = NOW()
                    WHERE status = 'COOLDOWN'
                    AND cooldown_until <= NOW();
                    """
                )
                if "UPDATE 0" not in result:
                    logger.info(f"System Recovery Manager: Re-activated expired tokens. Database return: {result}")
        except Exception as exc:
            logger.error(f"System Recovery Manager critical error: {exc}")
        await asyncio.sleep(30.0)


async def process_task(
    task_repo: TaskRepository,
    token_repo: TokenRepository,
    http_worker: HttpWorker,
    task: WorkerTask,
) -> None:
    token = await token_repo.checkout_token()
    if token is None:
        raise RuntimeError("No ACTIVE token available")

    try:
        response_json = await http_worker.execute(token_repo, token, task)
        results_list = response_json.get("results", [])
        logger.info(f"Ingestion Worker: Extracted {len(results_list)} real data documents for task={task.id}")

        for tweet_data in results_list:
            try:
                validated_document = SocialMediaDocument(**tweet_data)
                await upsert_insight_record(token_repo.pool, validated_document)
            except Exception as parse_error:
                logger.warning(f"Skipping malformed text record frame inside task {task.id}: {parse_error}")

        await token_repo.mark_token_active(token.id)
        await task_repo.complete_task(task.id)
        logger.info("task=%s status=done token=%s", task.id, token.id)

    except (httpx.ConnectError, httpx.TimeoutException) as network_exc:
        error_text = f"network_infrastructure_failure:{network_exc}"
        logger.warning(f"TRANSPORT ALERT: Proxy connection drop captured: {network_exc}")
        if task.attempts + 1 >= task.max_attempts:
            await task_repo.dead_letter_task(task.id, error_text)
        else:
            await task_repo.retry_task(task.id, task.attempts, error_text)
        raise

    except Exception as exc:
        error_text = f"unexpected_error:{exc}"
        logger.exception("Unexpected exception triggered on task=%s token=%s", task.id, token.id)
        await token_repo.mark_token_cooldown(token.id, error_text)
        if task.attempts + 1 >= task.max_attempts:
            await task_repo.dead_letter_task(task.id, error_text)
        else:
            await task_repo.retry_task(task.id, task.attempts, error_text)
        raise


async def worker_loop(
    worker_id: str,
    task_repo: TaskRepository,
    token_repo: TokenRepository,
    http_worker: HttpWorker,
    shutdown_event: asyncio.Event,
) -> None:
    while not shutdown_event.is_set():
        try:
            task = await task_repo.checkout_task(worker_id)
            if task is None:
                await asyncio.sleep(IDLE_SLEEP_SECONDS)
                continue
            try:
                await process_task(task_repo, token_repo, http_worker, task)
            except Exception:
                await asyncio.sleep(IDLE_SLEEP_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("worker=%s loop_error=%s", worker_id, exc)
            await asyncio.sleep(IDLE_SLEEP_SECONDS)


async def main() -> None:
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    # Active running task tracking matrix to protect in-flight transactions
    active_worker_tasks: set[asyncio.Task[None]] = set()

    def _request_shutdown() -> None:
        """
        Intercepts OS kill signals. Switches the shutdown flag immediately,
        halting new queue pops, while allowing active workers to finish gracefully.
        """
        logger.warning({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "WARNING",
            "message": "TERMINATION SIGNAL DETECTED: Initializing 10-second graceful shutdown window..."
        })
        shutdown_event.set()

    # Register native Linux operating system process trackers
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                loop.add_signal_handler(sig, _request_shutdown)
            except NotImplementedError:
                pass

    try:
        pool = await asyncpg.create_pool(
            dsn=DATABASE_DSN,
            min_size=MIN_POOL_SIZE,
            max_size=MAX_POOL_SIZE,
            timeout=5.0
        )
    except Exception as pool_exc:
        logger.error({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "CRITICAL",
            "message": f"Database pool connection failed. Falling back to mock standby: {pool_exc}"
        })
        await shutdown_event.wait()
        return

    token_repo = TokenRepository(pool)
    task_repo = TaskRepository(pool)
    http_worker = HttpWorker(timeout_seconds=REQUEST_TIMEOUT_SECONDS)

    # Spawn your background token cooldown manager daemon thread
    recovery_task = asyncio.create_task(
        token_cooldown_recovery_loop(token_repo, shutdown_event)
    )

    # Instantiate the parallel asynchronous execution worker paths
    for idx in range(WORKER_CONCURRENCY):
        w_task = asyncio.create_task(
            worker_loop(
                worker_id=f"worker-{idx + 1}",
                task_repo=task_repo,
                token_repo=token_repo,
                http_worker=http_worker,
                shutdown_event=shutdown_event,
            )
        )
        active_worker_tasks.add(w_task)

    logger.info({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "INFO",
        "message": f"All ingestion pipelines online. Active workers count: {WORKER_CONCURRENCY}"
    })

    # Wait indefinitely until a shutdown event signal breaks the main loop
    await shutdown_event.wait()

    # --- ENTERPRISE GRACEFUL SHUTDOWN TRIAZING PHASE ---
    logger.info({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "INFO",
        "message": "Pipeline halted. Awaiting flight completions for active data rows..."
    })

    # Cancel the token cooldown restorer daemon immediately (non-destructive)
    recovery_task.cancel()

    # Give active worker loops exactly 10.0 seconds to cleanly complete transaction commits
    shutdown_timeout_horizon = 10.0
    try:
        if active_worker_tasks:
            # Wait for all running worker loops to finish processing their current 20-tweet batch
            await asyncio.wait(active_worker_tasks, timeout=shutdown_timeout_horizon)
    except Exception as shutdown_exc:
        logger.error({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "ERROR",
            "message": f"Exception encountered during graceful cancellation cycle: {shutdown_exc}"
        })
    finally:
        # Force terminate any worker loops that blew past the 10-second safety window
        for w_task in active_worker_tasks:
            if not w_task.done():
                w_task.cancel()

        # Cleanly resolve all outstanding task closures and clear tracebacks
        await asyncio.gather(recovery_task, *active_worker_tasks, return_exceptions=True)

        # Safely disconnect from our relational storage engines without dropping locks or connections
        await pool.close()

        logger.info({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": "INFO",
            "message": "COMPLIANCE DISCONNECT: All database connections closed cleanly. Engine shutdown complete."
        })


if __name__ == "__main__":
    asyncio.run(main())