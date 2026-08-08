from __future__ import annotations

import os
import uuid

import asyncpg
import pytest
import redis.asyncio as aioredis

from xingestion.control_plane import RedisStreamQueue, TaskRepository
from xingestion.outbox import DurableOutboxDispatcher
from xingestion.replay import DeadLetterReplayService, ReplaySelector


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


async def make_runtime(pool, redis, suffix: str):
    stream = f"test:retry:{suffix}"
    group = f"group:{suffix}"
    task_repo = TaskRepository(pool)
    queue = RedisStreamQueue(redis, stream_name=stream, group_name=group)
    dispatcher = DurableOutboxDispatcher(pool, redis, stream_name=stream)
    await queue.ensure_group()
    return task_repo, queue, dispatcher


async def dispatch_and_lease(
    task_repo,
    queue,
    dispatcher,
    *,
    task_id: int,
    worker_id: str,
):
    assert await dispatcher.dispatch_ready(batch_size=10) >= 1
    deliveries = await queue.read_new(
        consumer_name=worker_id,
        count=1,
        block_ms=1000,
    )
    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert delivery.task_id == task_id
    lease = await task_repo.lease_task(
        task_id=task_id,
        generation=delivery.generation,
        worker_id=worker_id,
        lease_seconds=30,
    )
    assert lease is not None
    return delivery, lease


async def test_transient_retry_rolls_generation_and_rejects_stale_delivery(resources):
    pool, redis = resources
    suffix = uuid.uuid4().hex
    task_repo, queue, dispatcher = await make_runtime(pool, redis, suffix)
    worker = f"worker:{suffix}"

    task_id = await task_repo.create_task(
        task_type="X_KEYWORD_SEARCH",
        payload={"search_keyword": "retry-success"},
        idempotency_key=f"retry-success:{suffix}",
        max_attempts=3,
    )
    original_delivery, original_lease = await dispatch_and_lease(
        task_repo,
        queue,
        dispatcher,
        task_id=task_id,
        worker_id=worker,
    )
    assert original_lease.delivery_generation == 0

    assert await task_repo.schedule_retry(
        original_lease,
        error_message="transient_network_failure:test",
        delay_seconds=0,
        failure_class="transient_network_failure",
    )
    await queue.ack(original_delivery.message_id)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT status, attempts, delivery_generation
            FROM worker_tasks WHERE id = $1;
            """,
            task_id,
        )
    assert row["status"] == "RETRY_SCHEDULED"
    assert int(row["attempts"]) == 1
    assert int(row["delivery_generation"]) == 1

    stale_id = await redis.xadd(
        queue.stream_name,
        {"task_id": str(task_id), "generation": "0", "event_type": "TASK_READY"},
    )
    stale_deliveries = await queue.read_new(
        consumer_name=worker,
        count=1,
        block_ms=1000,
    )
    assert len(stale_deliveries) == 1
    stale = stale_deliveries[0]
    assert stale.message_id == str(stale_id)
    assert (
        await task_repo.lease_task(
            task_id=task_id,
            generation=0,
            worker_id=worker,
            lease_seconds=30,
        )
        is None
    )
    assert await task_repo.is_terminal_or_stale(task_id=task_id, generation=0)
    await queue.ack(stale.message_id)

    retry_delivery, retry_lease = await dispatch_and_lease(
        task_repo,
        queue,
        dispatcher,
        task_id=task_id,
        worker_id=worker,
    )
    assert retry_delivery.generation == 1
    assert retry_lease.attempts == 1
    assert await task_repo.complete_task(
        retry_lease,
        result_metadata={"retried": True},
    )
    await queue.ack(retry_delivery.message_id)

    async with pool.acquire() as conn:
        final = await conn.fetchrow(
            "SELECT status, attempts, delivery_generation FROM worker_tasks WHERE id = $1;",
            task_id,
        )
    assert final["status"] == "DONE"
    assert int(final["attempts"]) == 1
    assert int(final["delivery_generation"]) == 1
    await redis.delete(queue.stream_name)


async def test_retry_exhaustion_dead_letters_once(resources):
    pool, redis = resources
    suffix = uuid.uuid4().hex
    task_repo, queue, dispatcher = await make_runtime(pool, redis, suffix)
    worker = f"worker:{suffix}"

    task_id = await task_repo.create_task(
        task_type="X_KEYWORD_SEARCH",
        payload={"search_keyword": "retry-exhaust"},
        idempotency_key=f"retry-exhaust:{suffix}",
        max_attempts=2,
    )
    delivery0, lease0 = await dispatch_and_lease(
        task_repo,
        queue,
        dispatcher,
        task_id=task_id,
        worker_id=worker,
    )
    assert await task_repo.schedule_retry(
        lease0,
        error_message="transient_network_failure:first",
        delay_seconds=0,
        failure_class="transient_network_failure",
    )
    await queue.ack(delivery0.message_id)

    delivery1, lease1 = await dispatch_and_lease(
        task_repo,
        queue,
        dispatcher,
        task_id=task_id,
        worker_id=worker,
    )
    assert lease1.attempts == 1
    assert await task_repo.dead_letter_task(
        lease1,
        error_message="transient_network_failure:exhausted",
        failure_class="transient_network_failure",
    )
    await queue.ack(delivery1.message_id)

    async with pool.acquire() as conn:
        task = await conn.fetchrow(
            "SELECT status, attempts FROM worker_tasks WHERE id = $1;",
            task_id,
        )
        archives = await conn.fetch(
            """
            SELECT id, failure_class, delivery_generation
            FROM worker_dead_letters
            WHERE original_task_id = $1;
            """,
            task_id,
        )
    assert task["status"] == "DEAD_LETTER"
    assert int(task["attempts"]) == 2
    assert len(archives) == 1
    assert archives[0]["failure_class"] == "transient_network_failure"
    assert int(archives[0]["delivery_generation"]) == 1
    await redis.delete(queue.stream_name)


async def test_selective_replay_is_audited_and_not_duplicated(resources):
    pool, redis = resources
    suffix = uuid.uuid4().hex
    task_repo, queue, dispatcher = await make_runtime(pool, redis, suffix)
    worker = f"worker:{suffix}"

    task_id = await task_repo.create_task(
        task_type="X_KEYWORD_SEARCH",
        payload={"search_keyword": "replay-me"},
        idempotency_key=f"replay-source:{suffix}",
        max_attempts=1,
    )
    delivery, lease = await dispatch_and_lease(
        task_repo,
        queue,
        dispatcher,
        task_id=task_id,
        worker_id=worker,
    )
    assert await task_repo.dead_letter_task(
        lease,
        error_message="collector_changed:test",
        failure_class="collector_changed",
    )
    await queue.ack(delivery.message_id)

    async with pool.acquire() as conn:
        dead_letter_id = int(
            await conn.fetchval(
                "SELECT id FROM worker_dead_letters WHERE original_task_id = $1;",
                task_id,
            )
        )

    replay_service = DeadLetterReplayService(pool)
    mismatch = await replay_service.replay(
        selector=ReplaySelector(failure_class="authentication_failure"),
        limit=10,
    )
    assert mismatch == []

    results = await replay_service.replay(
        selector=ReplaySelector(
            dead_letter_ids=(dead_letter_id,),
            task_type="X_KEYWORD_SEARCH",
            failure_class="collector_changed",
        ),
        limit=10,
        max_attempts=4,
        priority=75,
    )
    assert len(results) == 1
    result = results[0]
    assert result.dead_letter_id == dead_letter_id
    assert result.replay_generation == 1

    repeated = await replay_service.replay(
        selector=ReplaySelector(dead_letter_ids=(dead_letter_id,)),
        limit=10,
    )
    assert repeated == []

    async with pool.acquire() as conn:
        archive = await conn.fetchrow(
            """
            SELECT replayed_at, replay_task_id, replay_generation
            FROM worker_dead_letters WHERE id = $1;
            """,
            dead_letter_id,
        )
        replay_task = await conn.fetchrow(
            """
            SELECT status, attempts, max_attempts, priority,
                   origin_task_id, replay_of_dead_letter_id
            FROM worker_tasks WHERE id = $1;
            """,
            result.replay_task_id,
        )
        audit_rows = await conn.fetch(
            """
            SELECT dead_letter_id, replay_generation, replay_task_id
            FROM worker_dead_letter_replays
            WHERE dead_letter_id = $1;
            """,
            dead_letter_id,
        )

    assert archive["replayed_at"] is not None
    assert int(archive["replay_task_id"]) == result.replay_task_id
    assert int(archive["replay_generation"]) == 1
    assert replay_task["status"] == "PENDING"
    assert int(replay_task["attempts"]) == 0
    assert int(replay_task["max_attempts"]) == 4
    assert int(replay_task["priority"]) == 75
    assert int(replay_task["origin_task_id"]) == task_id
    assert int(replay_task["replay_of_dead_letter_id"]) == dead_letter_id
    assert len(audit_rows) == 1
    assert int(audit_rows[0]["replay_task_id"]) == result.replay_task_id

    replay_delivery, replay_lease = await dispatch_and_lease(
        task_repo,
        queue,
        dispatcher,
        task_id=result.replay_task_id,
        worker_id=worker,
    )
    assert await task_repo.complete_task(
        replay_lease,
        result_metadata={"replay": True},
    )
    await queue.ack(replay_delivery.message_id)
    await redis.delete(queue.stream_name)
