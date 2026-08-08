from __future__ import annotations

import asyncio
import json
import logging
import random
import signal
from datetime import datetime, timezone
from typing import Any

import asyncpg
import redis.asyncio as aioredis
from pydantic import ValidationError

from analytics_parser import SocialMediaDocument, upsert_insight_record
from xingestion.collectors import (
    AuthenticationFailure,
    CollectionBatch,
    CollectionError,
    CollectionRequest,
    MockSearchAdapter,
    PermanentTaskFailure,
    PlaywrightSearchAdapter,
    RateLimited,
    SourceAdapter,
    TwikitSearchAdapter,
)
from xingestion.config import Settings
from xingestion.control_plane import (
    RedisStreamQueue,
    StreamDelivery,
    TaskLease,
    TaskRepository,
    TokenLease,
    TokenRepository,
)
from xingestion.lease_guard import TaskLeaseGuard, TaskLeaseLost


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "worker_id",
            "task_id",
            "task_generation",
            "token_id",
            "adapter",
            "failure_class",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception_trace"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logger = logging.getLogger("worker_core")
logger.setLevel(logging.INFO)
logger.handlers.clear()
logger.addHandler(handler)
logger.propagate = False


def backoff_delay_seconds(
    attempt: int,
    *,
    base: float = 1.0,
    cap: float = 300.0,
) -> float:
    jitter = random.uniform(0.0, 0.25)
    return min(cap, base * (2 ** max(0, attempt))) + jitter


class IngestionExecutor:
    def __init__(
        self,
        *,
        settings: Settings,
        token_repo: TokenRepository,
        primary_adapter: SourceAdapter,
        recovery_adapter: SourceAdapter | None,
    ) -> None:
        self.settings = settings
        self.token_repo = token_repo
        self.primary_adapter = primary_adapter
        self.recovery_adapter = recovery_adapter

    async def _collect_with_session(
        self,
        *,
        adapter: SourceAdapter,
        request: CollectionRequest,
        worker_id: str,
    ) -> tuple[CollectionBatch, TokenLease | None]:
        lease: TokenLease | None = None
        if adapter.requires_session:
            lease = await self.token_repo.checkout_token(
                lease_owner=worker_id,
                lease_seconds=self.settings.token_lease_seconds,
            )
            if lease is None:
                raise AuthenticationFailure(
                    "no ACTIVE session is currently available"
                )

        try:
            batch = await adapter.collect(request, session=lease)
        except CollectionError as exc:
            if lease is not None:
                if exc.session_fault:
                    await self.token_repo.mark_cooldown(
                        lease,
                        cooldown_seconds=self.settings.token_cooldown_seconds,
                        error_message=f"{exc.failure_class}:{str(exc)}",
                    )
                else:
                    await self.token_repo.release_lease(lease)
            raise
        except Exception:
            if lease is not None:
                await self.token_repo.release_lease(lease)
            raise
        else:
            if lease is not None:
                await self.token_repo.record_success(lease)
            return batch, lease

    async def collect(
        self,
        *,
        task: TaskLease,
        worker_id: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if task.task_type != "X_KEYWORD_SEARCH":
            raise PermanentTaskFailure(
                f"unsupported task_type={task.task_type}"
            )

        query = str(task.payload.get("search_keyword") or "").strip()
        if not query:
            raise PermanentTaskFailure(
                "task payload is missing search_keyword"
            )

        requested_pages = int(
            task.payload.get(
                "max_pages",
                self.settings.collector_max_pages,
            )
        )
        max_pages = max(
            1,
            min(requested_pages, self.settings.collector_max_pages),
        )
        requested_page_size = int(
            task.payload.get(
                "page_size",
                self.settings.collector_page_size,
            )
        )
        page_size = max(
            1,
            min(requested_page_size, self.settings.collector_page_size, 20),
        )

        all_items: list[dict[str, Any]] = []
        cursor = task.payload.get("cursor")
        adapters_used: list[dict[str, Any]] = []

        for page_number in range(max_pages):
            request = CollectionRequest(
                query=query,
                cursor=str(cursor) if cursor else None,
                page_size=page_size,
            )

            try:
                batch, _lease = await self._collect_with_session(
                    adapter=self.primary_adapter,
                    request=request,
                    worker_id=worker_id,
                )
            except (AuthenticationFailure, RateLimited) as primary_exc:
                logger.warning(
                    "primary collector session failure; attempting one session failover",
                    extra={
                        "worker_id": worker_id,
                        "task_id": task.id,
                        "task_generation": task.delivery_generation,
                        "adapter": self.primary_adapter.name,
                        "failure_class": primary_exc.failure_class,
                    },
                )
                try:
                    batch, _lease = await self._collect_with_session(
                        adapter=self.primary_adapter,
                        request=request,
                        worker_id=f"{worker_id}:failover",
                    )
                except CollectionError:
                    if self.recovery_adapter is None:
                        raise
                    batch, _lease = await self._collect_with_session(
                        adapter=self.recovery_adapter,
                        request=request,
                        worker_id=f"{worker_id}:recovery",
                    )
            except CollectionError:
                if self.recovery_adapter is None:
                    raise
                batch, _lease = await self._collect_with_session(
                    adapter=self.recovery_adapter,
                    request=request,
                    worker_id=f"{worker_id}:recovery",
                )

            for item in batch.items:
                item.setdefault("_collector_adapter", batch.adapter_name)
                item.setdefault(
                    "_collector_adapter_version",
                    batch.adapter_version,
                )
            all_items.extend(batch.items)
            adapters_used.append(
                {
                    "page": page_number + 1,
                    "name": batch.adapter_name,
                    "version": batch.adapter_version,
                    "count": len(batch.items),
                    "metadata": batch.metadata,
                }
            )
            cursor = batch.next_cursor
            if not cursor:
                break

        return all_items, {
            "query": query,
            "pages": len(adapters_used),
            "items_collected": len(all_items),
            "adapters": adapters_used,
            "next_cursor": cursor,
        }


async def persist_documents(
    pool: asyncpg.Pool,
    *,
    task: TaskLease,
    items: list[dict[str, Any]],
    worker_id: str,
) -> tuple[int, int]:
    stored = 0
    rejected = 0
    for item in items:
        try:
            document = SocialMediaDocument(**item)
        except ValidationError as exc:
            rejected += 1
            logger.warning(
                "record rejected during validation: %s",
                exc,
                extra={
                    "worker_id": worker_id,
                    "task_id": task.id,
                    "task_generation": task.delivery_generation,
                },
            )
            continue

        adapter_name = item.get("_collector_adapter")
        adapter_version = item.get("_collector_adapter_version")
        await upsert_insight_record(
            pool,
            document,
            observation_key=(
                f"task:{task.id}:generation:{task.delivery_generation}:"
                f"{document.platform}:{document.original_tweet_id}"
            ),
            ingestion_task_id=task.id,
            task_generation=task.delivery_generation,
            adapter_name=str(adapter_name) if adapter_name else None,
            adapter_version=(
                str(adapter_version) if adapter_version else None
            ),
        )
        stored += 1
    return stored, rejected


async def transition_failure(
    *,
    task_repo: TaskRepository,
    task: TaskLease,
    error: Exception,
) -> bool:
    if isinstance(error, CollectionError):
        failure_class = error.failure_class
        retryable = error.retryable
        retry_after = error.retry_after_seconds
    else:
        failure_class = "unexpected_error"
        retryable = True
        retry_after = None

    error_message = f"{failure_class}:{error}"
    exhausted = task.attempts + 1 >= task.max_attempts
    if not retryable or exhausted:
        return await task_repo.dead_letter_task(
            task,
            error_message=error_message,
            failure_class=failure_class,
        )

    delay = (
        retry_after
        if retry_after is not None
        else backoff_delay_seconds(task.attempts)
    )
    return await task_repo.schedule_retry(
        task,
        error_message=error_message,
        delay_seconds=delay,
        failure_class=failure_class,
    )


async def _execute_leased_work(
    *,
    task: TaskLease,
    worker_id: str,
    executor: IngestionExecutor,
    pool: asyncpg.Pool,
) -> tuple[dict[str, Any], int, int]:
    items, collection_metadata = await executor.collect(
        task=task,
        worker_id=worker_id,
    )
    stored, rejected = await persist_documents(
        pool,
        task=task,
        items=items,
        worker_id=worker_id,
    )
    return collection_metadata, stored, rejected


async def handle_delivery(
    *,
    delivery: StreamDelivery,
    worker_id: str,
    queue: RedisStreamQueue,
    task_repo: TaskRepository,
    executor: IngestionExecutor,
    pool: asyncpg.Pool,
    settings: Settings,
) -> None:
    task = await task_repo.lease_task(
        task_id=delivery.task_id,
        generation=delivery.generation,
        worker_id=worker_id,
        lease_seconds=settings.task_lease_seconds,
    )
    if task is None:
        if await task_repo.is_terminal_or_stale(
            task_id=delivery.task_id,
            generation=delivery.generation,
        ):
            await queue.ack(delivery.message_id)
        return

    lease_guard = TaskLeaseGuard(
        pool,
        queue,
        lease_seconds=settings.task_lease_seconds,
        heartbeat_seconds=settings.task_heartbeat_seconds,
    )

    try:
        collection_metadata, stored, rejected = await lease_guard.run_guarded(
            task=task,
            delivery=delivery,
            consumer_name=worker_id,
            operation=_execute_leased_work(
                task=task,
                worker_id=worker_id,
                executor=executor,
                pool=pool,
            ),
        )

        completed = await task_repo.complete_task(
            task,
            result_metadata={
                **collection_metadata,
                "items_stored": stored,
                "items_rejected": rejected,
            },
        )
        if completed:
            await queue.ack(delivery.message_id)
            logger.info(
                "task completed",
                extra={
                    "worker_id": worker_id,
                    "task_id": task.id,
                    "task_generation": task.delivery_generation,
                },
            )
    except TaskLeaseLost as exc:
        # Another owner or terminal state has won the durable fence. Do not
        # mutate task state or ACK a message this worker no longer owns.
        logger.warning(
            "task lease lost; abandoning local execution: %s",
            exc,
            extra={
                "worker_id": worker_id,
                "task_id": task.id,
                "task_generation": task.delivery_generation,
                "failure_class": "task_lease_lost",
            },
        )
    except asyncio.CancelledError:
        await lease_guard.release_for_recovery(task)
        raise
    except Exception as exc:
        transitioned = await transition_failure(
            task_repo=task_repo,
            task=task,
            error=exc,
        )
        if transitioned:
            await queue.ack(delivery.message_id)
        logger.exception(
            "task execution failed",
            extra={
                "worker_id": worker_id,
                "task_id": task.id,
                "task_generation": task.delivery_generation,
                "failure_class": getattr(
                    exc,
                    "failure_class",
                    "unexpected_error",
                ),
            },
        )


async def worker_loop(
    *,
    worker_id: str,
    queue: RedisStreamQueue,
    task_repo: TaskRepository,
    executor: IngestionExecutor,
    pool: asyncpg.Pool,
    settings: Settings,
    shutdown_event: asyncio.Event,
) -> None:
    while not shutdown_event.is_set():
        try:
            deliveries = await queue.read_new(
                consumer_name=worker_id,
                count=1,
                block_ms=settings.task_read_block_ms,
            )
            if not deliveries:
                deliveries = await queue.reclaim(
                    consumer_name=worker_id,
                    min_idle_ms=settings.task_reclaim_idle_ms,
                    count=1,
                )
            if not deliveries:
                continue

            await handle_delivery(
                delivery=deliveries[0],
                worker_id=worker_id,
                queue=queue,
                task_repo=task_repo,
                executor=executor,
                pool=pool,
                settings=settings,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "worker loop error",
                extra={"worker_id": worker_id},
            )
            await asyncio.sleep(settings.idle_sleep_seconds)


async def token_recovery_loop(
    *,
    token_repo: TokenRepository,
    shutdown_event: asyncio.Event,
) -> None:
    while not shutdown_event.is_set():
        try:
            recovered = await token_repo.recover_expired_cooldowns()
            if recovered:
                logger.info("recovered %s cooled-down sessions", recovered)
        except Exception:
            logger.exception("token cooldown recovery failed")
        try:
            await asyncio.wait_for(
                shutdown_event.wait(),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            pass


async def main() -> None:
    settings = Settings.from_env()
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_shutdown() -> None:
        shutdown_event.set()

    for signal_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, signal_name, None)
        if sig is not None:
            try:
                loop.add_signal_handler(sig, request_shutdown)
            except NotImplementedError:
                pass

    pool = await asyncpg.create_pool(
        dsn=settings.database_dsn,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        timeout=5.0,
    )
    redis = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
    )
    await redis.ping()

    task_repo = TaskRepository(pool)
    token_repo = TokenRepository(pool)
    queue = RedisStreamQueue(
        redis,
        stream_name=settings.task_stream,
        group_name=settings.task_consumer_group,
    )
    await queue.ensure_group()

    if settings.mock_mode:
        primary_adapter: SourceAdapter = MockSearchAdapter()
    else:
        primary_adapter = TwikitSearchAdapter(
            proxy=settings.http_proxy,
            timeout_seconds=settings.request_timeout_seconds,
        )

    recovery_adapter: SourceAdapter | None = None
    if settings.browser_recovery_enabled and not settings.mock_mode:
        recovery_adapter = PlaywrightSearchAdapter(
            proxy=settings.http_proxy,
            timeout_seconds=max(
                settings.request_timeout_seconds,
                30.0,
            ),
        )

    executor = IngestionExecutor(
        settings=settings,
        token_repo=token_repo,
        primary_adapter=primary_adapter,
        recovery_adapter=recovery_adapter,
    )

    recovery_task = asyncio.create_task(
        token_recovery_loop(
            token_repo=token_repo,
            shutdown_event=shutdown_event,
        ),
        name="token-recovery",
    )
    workers = [
        asyncio.create_task(
            worker_loop(
                worker_id=f"worker-{index + 1}",
                queue=queue,
                task_repo=task_repo,
                executor=executor,
                pool=pool,
                settings=settings,
                shutdown_event=shutdown_event,
            ),
            name=f"worker-{index + 1}",
        )
        for index in range(settings.worker_concurrency)
    ]

    logger.info(
        "ingestion worker online",
        extra={"worker_id": f"pool:{settings.worker_concurrency}"},
    )

    try:
        await shutdown_event.wait()
    finally:
        recovery_task.cancel()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(
            recovery_task,
            *workers,
            return_exceptions=True,
        )
        await primary_adapter.close()
        if recovery_adapter is not None:
            await recovery_adapter.close()
        await redis.aclose()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
