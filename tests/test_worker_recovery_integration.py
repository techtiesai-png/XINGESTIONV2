from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest
import redis.asyncio as aioredis

from xingestion.control_plane import RedisStreamQueue, TaskRepository
from xingestion.lease_guard import TaskLeaseGuard, TaskLeaseLost
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


async def create_leased_delivery(pool, redis, *, suffix: str, worker_id: str):
    stream = f"test:recovery:{suffix}"
    group = f"group:{suffix}"
    task_repo = TaskRepository(pool)
    queue = RedisStreamQueue(redis, stream_name=stream, group_name=group)
    dispatcher = DurableOutboxDispatcher(pool, redis, stream_name=stream)
    await queue.ensure_group()

    task_id = await task_repo.create_task(
        task_type="X_KEYWORD_SEARCH",
        payload={"search_keyword": "lease-recovery"},
        idempotency_key=f"recovery:{suffix}",
    )
    assert await dispatcher.dispatch_ready(batch_size=10) == 1

    deliveries = await queue.read_new(
        consumer_name=worker_id,
        count=1,
        block_ms=1000,
    )
    assert len(deliveries) == 1
    delivery = deliveries[0]
    lease = await task_repo.lease_task(
        task_id=task_id,
        generation=delivery.generation,
        worker_id=worker_id,
        lease_seconds=30,
    )
    assert lease is not None
    return task_repo, queue, delivery, lease


async def force_pending_idle(redis, queue, *, delivery, consumer_name: str) -> None:
    claimed = await redis.xclaim(
        name=queue.stream_name,
        groupname=queue.group_name,
        consumername=consumer_name,
        min_idle_time=0,
        message_ids=[delivery.message_id],
        idle=5_000,
        justid=True,
    )
    assert delivery.message_id in {str(item) for item in claimed}


async def test_heartbeat_renews_db_fence_and_resets_redis_idle(resources):
    pool, redis = resources
    suffix = uuid.uuid4().hex
    worker_a = f"worker-a:{suffix}"
    worker_b = f"worker-b:{suffix}"
    task_repo, queue, delivery, lease = await create_leased_delivery(
        pool,
        redis,
        suffix=suffix,
        worker_id=worker_a,
    )

    await force_pending_idle(
        redis,
        queue,
        delivery=delivery,
        consumer_name=worker_a,
    )

    guard = TaskLeaseGuard(
        pool,
        queue,
        lease_seconds=30,
        heartbeat_seconds=5.0,
    )
    await guard.renew_once(
        task=lease,
        delivery=delivery,
        consumer_name=worker_a,
    )

    # A healthy heartbeat resets Redis idle time, so another consumer cannot
    # immediately XAUTOCLAIM the message even though we artificially aged it.
    reclaimed = await queue.reclaim(
        consumer_name=worker_b,
        min_idle_ms=1_000,
        count=1,
    )
    assert reclaimed == []

    # PostgreSQL remains the stronger execution fence regardless of Redis PEL
    # ownership. Worker B cannot take over while Worker A's lease is valid.
    assert (
        await task_repo.lease_task(
            task_id=lease.id,
            generation=lease.delivery_generation,
            worker_id=worker_b,
            lease_seconds=30,
        )
        is None
    )

    assert await task_repo.complete_task(lease, result_metadata={"heartbeat": True})
    await queue.ack(delivery.message_id)
    await redis.delete(queue.stream_name)


async def test_expired_worker_is_reclaimed_without_stale_completion(resources):
    pool, redis = resources
    suffix = uuid.uuid4().hex
    worker_a = f"worker-a:{suffix}"
    worker_b = f"worker-b:{suffix}"
    task_repo, queue, delivery, stale_lease = await create_leased_delivery(
        pool,
        redis,
        suffix=suffix,
        worker_id=worker_a,
    )

    # Simulate a hard process death: no heartbeat, no ACK, and the durable
    # lease expires. Age the Redis PEL entry deterministically instead of
    # sleeping in CI.
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE worker_tasks
            SET lease_expires_at = NOW() - INTERVAL '1 second'
            WHERE id = $1;
            """,
            stale_lease.id,
        )
    await force_pending_idle(
        redis,
        queue,
        delivery=delivery,
        consumer_name=worker_a,
    )

    reclaimed = await queue.reclaim(
        consumer_name=worker_b,
        min_idle_ms=1_000,
        count=1,
    )
    assert len(reclaimed) == 1
    assert reclaimed[0].message_id == delivery.message_id

    replacement = await task_repo.lease_task(
        task_id=stale_lease.id,
        generation=stale_lease.delivery_generation,
        worker_id=worker_b,
        lease_seconds=30,
    )
    assert replacement is not None
    assert replacement.lease_owner == worker_b

    # The dead/stale owner is fenced out even if it later resumes and tries to
    # commit. Only the replacement lease can transition the task to DONE.
    assert not await task_repo.complete_task(
        stale_lease,
        result_metadata={"owner": "stale"},
    )
    assert await task_repo.complete_task(
        replacement,
        result_metadata={"owner": "replacement"},
    )
    await queue.ack(delivery.message_id)
    await redis.delete(queue.stream_name)


async def test_guard_cancels_operation_after_durable_lease_loss(resources):
    pool, redis = resources
    suffix = uuid.uuid4().hex
    worker_a = f"worker-a:{suffix}"
    task_repo, queue, delivery, lease = await create_leased_delivery(
        pool,
        redis,
        suffix=suffix,
        worker_id=worker_a,
    )

    guard = TaskLeaseGuard(
        pool,
        queue,
        lease_seconds=30,
        heartbeat_seconds=0.05,
    )
    operation_cancelled = asyncio.Event()

    async def long_operation():
        try:
            await asyncio.sleep(10)
        finally:
            operation_cancelled.set()

    guarded = asyncio.create_task(
        guard.run_guarded(
            task=lease,
            delivery=delivery,
            consumer_name=worker_a,
            operation=long_operation(),
        )
    )

    # Simulate another authority winning the fence before the next heartbeat.
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE worker_tasks
            SET lease_owner = $2
            WHERE id = $1;
            """,
            lease.id,
            f"replacement:{suffix}",
        )

    with pytest.raises(TaskLeaseLost):
        await asyncio.wait_for(guarded, timeout=1.0)
    assert operation_cancelled.is_set()

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM worker_tasks WHERE id = $1;", lease.id)
    await redis.delete(queue.stream_name)
