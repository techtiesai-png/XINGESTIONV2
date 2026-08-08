from __future__ import annotations

import os
import uuid

import asyncpg
import pytest
import redis.asyncio as aioredis

from xingestion.control_plane import RedisStreamQueue, TaskRepository
from xingestion.outbox import DurableOutboxDispatcher


pytestmark = pytest.mark.asyncio


@pytest.fixture
async def resources():
    dsn = os.getenv("DATABASE_DSN")
    redis_url = os.getenv("REDIS_URL")
    if not dsn or not redis_url:
        pytest.skip("DATABASE_DSN and REDIS_URL are required for integration tests")

    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
    redis = aioredis.from_url(redis_url, decode_responses=True)
    try:
        await redis.ping()
        yield pool, redis
    finally:
        await redis.aclose()
        await pool.close()


async def test_outbox_commits_enqueued_before_redis_visibility(resources):
    pool, redis = resources
    suffix = uuid.uuid4().hex
    stream = f"test:xingestion:{suffix}"
    group = f"group:{suffix}"
    consumer = f"consumer:{suffix}"

    task_repo = TaskRepository(pool)
    queue = RedisStreamQueue(redis, stream_name=stream, group_name=group)
    dispatcher = DurableOutboxDispatcher(
        pool,
        redis,
        stream_name=stream,
        claim_seconds=5,
    )
    await queue.ensure_group()

    task_id = await task_repo.create_task(
        task_type="X_KEYWORD_SEARCH",
        payload={"search_keyword": "integration-test"},
        idempotency_key=f"integration:{suffix}",
    )

    claimed = await dispatcher.claim_ready(batch_size=10)
    event = next(item for item in claimed if item.task_id == task_id)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT status, delivery_generation
            FROM worker_tasks
            WHERE id = $1;
            """,
            task_id,
        )
        outbox = await conn.fetchrow(
            """
            SELECT published_at, claim_token
            FROM task_outbox
            WHERE id = $1;
            """,
            event.outbox_id,
        )

    assert row["status"] == "ENQUEUED"
    assert int(row["delivery_generation"]) == event.generation
    assert outbox["published_at"] is None
    assert outbox["claim_token"] == event.claim_token
    assert await redis.xlen(stream) == 0

    await dispatcher.publish_claimed(event)
    deliveries = await queue.read_new(
        consumer_name=consumer,
        count=1,
        block_ms=1000,
    )
    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert delivery.task_id == task_id
    assert delivery.generation == event.generation

    lease = await task_repo.lease_task(
        task_id=delivery.task_id,
        generation=delivery.generation,
        worker_id=consumer,
        lease_seconds=30,
    )
    assert lease is not None
    assert await task_repo.complete_task(lease, result_metadata={"test": True})
    await queue.ack(delivery.message_id)

    # At-least-once Redis delivery is acceptable. A duplicate for the same
    # task generation must not re-open a task already durably completed.
    duplicate_id = await redis.xadd(
        stream,
        {
            "task_id": str(task_id),
            "generation": str(event.generation),
            "event_type": "TASK_READY",
        },
    )
    duplicate = await queue.read_new(
        consumer_name=consumer,
        count=1,
        block_ms=1000,
    )
    assert len(duplicate) == 1
    assert duplicate[0].message_id == str(duplicate_id)
    assert (
        await task_repo.lease_task(
            task_id=task_id,
            generation=event.generation,
            worker_id=consumer,
            lease_seconds=30,
        )
        is None
    )
    assert await task_repo.is_terminal_or_stale(
        task_id=task_id,
        generation=event.generation,
    )
    await queue.ack(duplicate[0].message_id)

    await redis.delete(stream)
