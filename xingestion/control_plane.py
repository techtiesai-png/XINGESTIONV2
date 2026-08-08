from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import asyncpg
from redis.asyncio import Redis
from redis.exceptions import ResponseError


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True, frozen=True)
class TaskLease:
    id: int
    task_type: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    delivery_generation: int
    lease_owner: str
    lease_expires_at: datetime


@dataclass(slots=True, frozen=True)
class TokenLease:
    lease_id: int
    token_id: int
    token_key: str
    token_value: str
    lease_owner: str
    lease_expires_at: datetime

    @property
    def id(self) -> int:
        """Compatibility alias while legacy code is migrated."""
        return self.token_id


@dataclass(slots=True, frozen=True)
class StreamDelivery:
    message_id: str
    task_id: int
    generation: int


class TaskRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def create_task(
        self,
        *,
        task_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
        max_attempts: int = 5,
        priority: int = 100,
        next_run_at: datetime | None = None,
    ) -> int:
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be > 0")

        run_at = next_run_at or utcnow()
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO worker_tasks (
                        task_type,
                        payload,
                        status,
                        attempts,
                        max_attempts,
                        next_run_at,
                        idempotency_key,
                        priority,
                        delivery_generation,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        $1, $2::jsonb, 'PENDING', 0, $3, $4, $5, $6, 0,
                        NOW(), NOW()
                    )
                    ON CONFLICT (idempotency_key) DO UPDATE SET
                        updated_at = worker_tasks.updated_at
                    RETURNING id;
                    """,
                    task_type,
                    json.dumps(payload),
                    max_attempts,
                    run_at,
                    idempotency_key,
                    priority,
                )
                task_id = int(row["id"])
                await conn.execute(
                    """
                    INSERT INTO task_outbox (
                        task_id,
                        delivery_generation,
                        event_type,
                        payload,
                        available_at,
                        created_at
                    )
                    SELECT
                        wt.id,
                        wt.delivery_generation,
                        'TASK_READY',
                        jsonb_build_object(
                            'task_id', wt.id,
                            'generation', wt.delivery_generation
                        ),
                        wt.next_run_at,
                        NOW()
                    FROM worker_tasks wt
                    WHERE wt.id = $1
                      AND wt.status IN ('PENDING', 'RETRY_SCHEDULED')
                    ON CONFLICT (task_id, delivery_generation)
                    DO NOTHING;
                    """,
                    task_id,
                )
                return task_id

    async def lease_task(
        self,
        *,
        task_id: int,
        generation: int,
        worker_id: str,
        lease_seconds: int,
    ) -> TaskLease | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE worker_tasks
                SET status = 'RUNNING',
                    lease_owner = $3,
                    lease_started_at = NOW(),
                    lease_expires_at = NOW() + ($4 * INTERVAL '1 second'),
                    updated_at = NOW()
                WHERE id = $1
                  AND delivery_generation = $2
                  AND next_run_at <= NOW()
                  AND (
                        status = 'ENQUEUED'
                        OR (
                            status = 'RUNNING'
                            AND lease_expires_at <= NOW()
                        )
                      )
                RETURNING
                    id,
                    task_type,
                    payload,
                    attempts,
                    max_attempts,
                    delivery_generation,
                    lease_owner,
                    lease_expires_at;
                """,
                task_id,
                generation,
                worker_id,
                lease_seconds,
            )
            if row is None:
                return None

            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)

            return TaskLease(
                id=int(row["id"]),
                task_type=str(row["task_type"]),
                payload=dict(payload),
                attempts=int(row["attempts"]),
                max_attempts=int(row["max_attempts"]),
                delivery_generation=int(row["delivery_generation"]),
                lease_owner=str(row["lease_owner"]),
                lease_expires_at=row["lease_expires_at"],
            )

    async def complete_task(
        self,
        task: TaskLease,
        *,
        result_metadata: dict[str, Any] | None = None,
    ) -> bool:
        async with self.pool.acquire() as conn:
            status = await conn.execute(
                """
                UPDATE worker_tasks
                SET status = 'DONE',
                    completed_at = NOW(),
                    lease_owner = NULL,
                    lease_started_at = NULL,
                    lease_expires_at = NULL,
                    last_error = NULL,
                    result_metadata = COALESCE($4::jsonb, result_metadata),
                    updated_at = NOW()
                WHERE id = $1
                  AND delivery_generation = $2
                  AND lease_owner = $3
                  AND status = 'RUNNING';
                """,
                task.id,
                task.delivery_generation,
                task.lease_owner,
                json.dumps(result_metadata) if result_metadata is not None else None,
            )
            return status == "UPDATE 1"

    async def schedule_retry(
        self,
        task: TaskLease,
        *,
        error_message: str,
        delay_seconds: float,
        failure_class: str,
    ) -> bool:
        next_run_at = utcnow() + timedelta(seconds=max(delay_seconds, 0.0))
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    UPDATE worker_tasks
                    SET status = 'RETRY_SCHEDULED',
                        attempts = attempts + 1,
                        delivery_generation = delivery_generation + 1,
                        next_run_at = $4,
                        lease_owner = NULL,
                        lease_started_at = NULL,
                        lease_expires_at = NULL,
                        last_error = $5,
                        last_failure_class = $6,
                        updated_at = NOW()
                    WHERE id = $1
                      AND delivery_generation = $2
                      AND lease_owner = $3
                      AND status = 'RUNNING'
                    RETURNING id, delivery_generation, next_run_at;
                    """,
                    task.id,
                    task.delivery_generation,
                    task.lease_owner,
                    next_run_at,
                    error_message[:4000],
                    failure_class[:100],
                )
                if row is None:
                    return False

                await conn.execute(
                    """
                    INSERT INTO task_outbox (
                        task_id,
                        delivery_generation,
                        event_type,
                        payload,
                        available_at,
                        created_at
                    )
                    VALUES (
                        $1, $2, 'TASK_READY',
                        jsonb_build_object(
                            'task_id', $1::int,
                            'generation', $2::int
                        ),
                        $3,
                        NOW()
                    )
                    ON CONFLICT (task_id, delivery_generation)
                    DO NOTHING;
                    """,
                    int(row["id"]),
                    int(row["delivery_generation"]),
                    row["next_run_at"],
                )
                return True

    async def dead_letter_task(
        self,
        task: TaskLease,
        *,
        error_message: str,
        failure_class: str,
    ) -> bool:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    UPDATE worker_tasks
                    SET status = 'DEAD_LETTER',
                        attempts = attempts + 1,
                        lease_owner = NULL,
                        lease_started_at = NULL,
                        lease_expires_at = NULL,
                        last_error = $4,
                        last_failure_class = $5,
                        updated_at = NOW()
                    WHERE id = $1
                      AND delivery_generation = $2
                      AND lease_owner = $3
                      AND status = 'RUNNING'
                    RETURNING id, task_type, payload, attempts,
                              delivery_generation;
                    """,
                    task.id,
                    task.delivery_generation,
                    task.lease_owner,
                    error_message[:4000],
                    failure_class[:100],
                )
                if row is None:
                    return False

                await conn.execute(
                    """
                    INSERT INTO worker_dead_letters (
                        original_task_id,
                        task_type,
                        payload,
                        last_error,
                        failure_class,
                        delivery_generation,
                        failed_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, NOW());
                    """,
                    int(row["id"]),
                    str(row["task_type"]),
                    row["payload"],
                    error_message[:4000],
                    failure_class[:100],
                    int(row["delivery_generation"]),
                )
                return True

    async def is_terminal_or_stale(
        self,
        *,
        task_id: int,
        generation: int,
    ) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT status, delivery_generation
                FROM worker_tasks
                WHERE id = $1;
                """,
                task_id,
            )
            if row is None:
                return True
            return (
                int(row["delivery_generation"]) != generation
                or str(row["status"]) in {"DONE", "DEAD_LETTER"}
            )


class RedisStreamQueue:
    def __init__(
        self,
        redis: Redis,
        *,
        stream_name: str,
        group_name: str,
    ) -> None:
        self.redis = redis
        self.stream_name = stream_name
        self.group_name = group_name

    async def ensure_group(self) -> None:
        try:
            await self.redis.xgroup_create(
                name=self.stream_name,
                groupname=self.group_name,
                id="0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    @staticmethod
    def _decode_delivery(
        message_id: str | bytes,
        fields: dict[Any, Any],
    ) -> StreamDelivery:
        def text(value: Any) -> str:
            if isinstance(value, bytes):
                return value.decode("utf-8")
            return str(value)

        normalized = {text(k): text(v) for k, v in fields.items()}
        return StreamDelivery(
            message_id=text(message_id),
            task_id=int(normalized["task_id"]),
            generation=int(normalized["generation"]),
        )

    async def read_new(
        self,
        *,
        consumer_name: str,
        count: int = 1,
        block_ms: int = 2_000,
    ) -> list[StreamDelivery]:
        response = await self.redis.xreadgroup(
            groupname=self.group_name,
            consumername=consumer_name,
            streams={self.stream_name: ">"},
            count=count,
            block=block_ms,
        )
        deliveries: list[StreamDelivery] = []
        for _stream, messages in response or []:
            for message_id, fields in messages:
                deliveries.append(self._decode_delivery(message_id, fields))
        return deliveries

    async def reclaim(
        self,
        *,
        consumer_name: str,
        min_idle_ms: int,
        count: int = 10,
    ) -> list[StreamDelivery]:
        response = await self.redis.xautoclaim(
            name=self.stream_name,
            groupname=self.group_name,
            consumername=consumer_name,
            min_idle_time=min_idle_ms,
            start_id="0-0",
            count=count,
        )
        messages: Iterable[tuple[Any, dict[Any, Any]]]
        if not response:
            return []
        if len(response) >= 2:
            messages = response[1]
        else:
            return []
        return [
            self._decode_delivery(message_id, fields)
            for message_id, fields in messages
        ]

    async def ack(self, message_id: str) -> None:
        await self.redis.xack(
            self.stream_name,
            self.group_name,
            message_id,
        )


class OutboxDispatcher:
    def __init__(
        self,
        pool: asyncpg.Pool,
        redis: Redis,
        *,
        stream_name: str,
    ) -> None:
        self.pool = pool
        self.redis = redis
        self.stream_name = stream_name

    async def dispatch_ready(self, *, batch_size: int = 100) -> int:
        dispatched = 0
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT
                        o.id,
                        o.task_id,
                        o.delivery_generation
                    FROM task_outbox o
                    JOIN worker_tasks wt ON wt.id = o.task_id
                    WHERE o.published_at IS NULL
                      AND o.available_at <= NOW()
                      AND wt.delivery_generation = o.delivery_generation
                      AND wt.status IN ('PENDING', 'RETRY_SCHEDULED')
                    ORDER BY wt.priority DESC, o.available_at ASC, o.id ASC
                    FOR UPDATE OF o SKIP LOCKED
                    LIMIT $1;
                    """,
                    batch_size,
                )

                for row in rows:
                    task_id = int(row["task_id"])
                    generation = int(row["delivery_generation"])
                    message_id = await self.redis.xadd(
                        self.stream_name,
                        {
                            "task_id": str(task_id),
                            "generation": str(generation),
                        },
                    )
                    if isinstance(message_id, bytes):
                        message_id = message_id.decode("utf-8")

                    await conn.execute(
                        """
                        UPDATE task_outbox
                        SET published_at = NOW(),
                            redis_message_id = $2,
                            publish_attempts = publish_attempts + 1,
                            last_error = NULL
                        WHERE id = $1;
                        """,
                        int(row["id"]),
                        str(message_id),
                    )
                    await conn.execute(
                        """
                        UPDATE worker_tasks
                        SET status = 'ENQUEUED',
                            enqueued_at = NOW(),
                            updated_at = NOW()
                        WHERE id = $1
                          AND delivery_generation = $2
                          AND status IN ('PENDING', 'RETRY_SCHEDULED');
                        """,
                        task_id,
                        generation,
                    )
                    dispatched += 1
        return dispatched


class TokenRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def checkout_token(
        self,
        *,
        lease_owner: str,
        lease_seconds: int,
    ) -> TokenLease | None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    DELETE FROM service_token_leases
                    WHERE lease_expires_at <= NOW();
                    """
                )
                row = await conn.fetchrow(
                    """
                    SELECT
                        st.id,
                        st.token_key,
                        st.token_value
                    FROM service_tokens st
                    WHERE st.status = 'ACTIVE'
                      AND (
                          st.cooldown_until IS NULL
                          OR st.cooldown_until <= NOW()
                      )
                      AND (
                          SELECT COUNT(*)
                          FROM service_token_leases l
                          WHERE l.token_id = st.id
                            AND l.lease_expires_at > NOW()
                      ) < st.max_concurrency
                    ORDER BY
                        st.last_leased_at NULLS FIRST,
                        st.id ASC
                    FOR UPDATE OF st SKIP LOCKED
                    LIMIT 1;
                    """
                )
                if row is None:
                    return None

                lease_row = await conn.fetchrow(
                    """
                    INSERT INTO service_token_leases (
                        token_id,
                        lease_owner,
                        acquired_at,
                        lease_expires_at
                    )
                    VALUES (
                        $1, $2, NOW(),
                        NOW() + ($3 * INTERVAL '1 second')
                    )
                    RETURNING id, lease_expires_at;
                    """,
                    int(row["id"]),
                    lease_owner,
                    lease_seconds,
                )
                await conn.execute(
                    """
                    UPDATE service_tokens
                    SET last_leased_at = NOW(),
                        updated_at = NOW()
                    WHERE id = $1;
                    """,
                    int(row["id"]),
                )
                return TokenLease(
                    lease_id=int(lease_row["id"]),
                    token_id=int(row["id"]),
                    token_key=str(row["token_key"]),
                    token_value=str(row["token_value"]),
                    lease_owner=lease_owner,
                    lease_expires_at=lease_row["lease_expires_at"],
                )

    async def record_success(self, lease: TokenLease) -> None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE service_tokens
                    SET last_used_at = NOW(),
                        last_error = CASE
                            WHEN status = 'ACTIVE' THEN NULL
                            ELSE last_error
                        END,
                        updated_at = NOW()
                    WHERE id = $1;
                    """,
                    lease.token_id,
                )
                await conn.execute(
                    """
                    DELETE FROM service_token_leases
                    WHERE id = $1 AND lease_owner = $2;
                    """,
                    lease.lease_id,
                    lease.lease_owner,
                )

    async def release_lease(self, lease: TokenLease) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                DELETE FROM service_token_leases
                WHERE id = $1 AND lease_owner = $2;
                """,
                lease.lease_id,
                lease.lease_owner,
            )

    async def mark_cooldown(
        self,
        lease: TokenLease,
        *,
        cooldown_seconds: int,
        error_message: str,
    ) -> None:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE service_tokens
                    SET status = 'COOLDOWN',
                        cooldown_until = NOW()
                            + ($2 * INTERVAL '1 second'),
                        last_error = $3,
                        updated_at = NOW()
                    WHERE id = $1;
                    """,
                    lease.token_id,
                    cooldown_seconds,
                    error_message[:4000],
                )
                await conn.execute(
                    """
                    DELETE FROM service_token_leases
                    WHERE id = $1 AND lease_owner = $2;
                    """,
                    lease.lease_id,
                    lease.lease_owner,
                )

    async def recover_expired_cooldowns(self) -> int:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                UPDATE service_tokens
                SET status = 'ACTIVE',
                    cooldown_until = NULL,
                    last_error = NULL,
                    updated_at = NOW()
                WHERE status = 'COOLDOWN'
                  AND cooldown_until <= NOW()
                RETURNING id;
                """
            )
            return len(rows)
