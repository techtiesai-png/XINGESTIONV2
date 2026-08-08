from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import asyncpg
from redis.asyncio import Redis

from xingestion.control_plane import utcnow


@dataclass(slots=True, frozen=True)
class ClaimedOutboxEvent:
    outbox_id: int
    task_id: int
    generation: int
    event_type: str
    payload: dict[str, Any]
    claim_token: str


class DurableOutboxDispatcher:
    """Publish PostgreSQL outbox rows to Redis Streams without a visibility race.

    The database transition to ENQUEUED is committed *before* an event becomes
    visible in Redis. A short-lived claim allows multiple dispatcher processes
    to run concurrently. If a dispatcher dies after XADD but before marking the
    row published, the event may be published again after claim expiry; task
    generation/idempotency makes that duplicate delivery safe.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        redis: Redis,
        *,
        stream_name: str,
        claim_seconds: int = 30,
    ) -> None:
        if claim_seconds <= 0:
            raise ValueError("claim_seconds must be > 0")
        self.pool = pool
        self.redis = redis
        self.stream_name = stream_name
        self.claim_seconds = claim_seconds

    async def claim_ready(self, *, batch_size: int = 100) -> list[ClaimedOutboxEvent]:
        if batch_size <= 0:
            raise ValueError("batch_size must be > 0")

        claim_token = uuid.uuid4().hex
        claim_until = utcnow() + timedelta(seconds=self.claim_seconds)

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT
                        o.id,
                        o.task_id,
                        o.delivery_generation,
                        o.event_type,
                        o.payload
                    FROM task_outbox o
                    JOIN worker_tasks wt ON wt.id = o.task_id
                    WHERE o.published_at IS NULL
                      AND o.available_at <= NOW()
                      AND (
                            o.claim_expires_at IS NULL
                            OR o.claim_expires_at <= NOW()
                          )
                      AND wt.delivery_generation = o.delivery_generation
                      AND wt.status IN (
                            'PENDING',
                            'RETRY_SCHEDULED',
                            'ENQUEUED'
                          )
                    ORDER BY wt.priority DESC, o.available_at ASC, o.id ASC
                    FOR UPDATE OF o SKIP LOCKED
                    LIMIT $1;
                    """,
                    batch_size,
                )

                claimed: list[ClaimedOutboxEvent] = []
                for row in rows:
                    outbox_id = int(row["id"])
                    task_id = int(row["task_id"])
                    generation = int(row["delivery_generation"])

                    await conn.execute(
                        """
                        UPDATE task_outbox
                        SET claim_token = $2,
                            claim_expires_at = $3,
                            publish_attempts = publish_attempts + 1,
                            last_error = NULL
                        WHERE id = $1;
                        """,
                        outbox_id,
                        claim_token,
                        claim_until,
                    )
                    await conn.execute(
                        """
                        UPDATE worker_tasks
                        SET status = 'ENQUEUED',
                            enqueued_at = COALESCE(enqueued_at, NOW()),
                            updated_at = NOW()
                        WHERE id = $1
                          AND delivery_generation = $2
                          AND status IN ('PENDING', 'RETRY_SCHEDULED');
                        """,
                        task_id,
                        generation,
                    )

                    payload = row["payload"]
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    claimed.append(
                        ClaimedOutboxEvent(
                            outbox_id=outbox_id,
                            task_id=task_id,
                            generation=generation,
                            event_type=str(row["event_type"]),
                            payload=dict(payload),
                            claim_token=claim_token,
                        )
                    )

                return claimed

    async def publish_claimed(self, event: ClaimedOutboxEvent) -> str:
        message_id = await self.redis.xadd(
            self.stream_name,
            {
                "task_id": str(event.task_id),
                "generation": str(event.generation),
                "event_type": event.event_type,
            },
        )
        if isinstance(message_id, bytes):
            message_id = message_id.decode("utf-8")

        async with self.pool.acquire() as conn:
            status = await conn.execute(
                """
                UPDATE task_outbox
                SET published_at = NOW(),
                    redis_message_id = $3,
                    claim_token = NULL,
                    claim_expires_at = NULL,
                    last_error = NULL
                WHERE id = $1
                  AND claim_token = $2
                  AND published_at IS NULL;
                """,
                event.outbox_id,
                event.claim_token,
                str(message_id),
            )
            if status != "UPDATE 1":
                # Redis delivery already happened. Do not delete the stream
                # entry: a duplicate is safer than losing a task. Generation
                # guards make a later duplicate delivery harmless.
                raise RuntimeError(
                    "outbox publish succeeded but claim ownership was lost "
                    f"for outbox_id={event.outbox_id}"
                )
        return str(message_id)

    async def record_failure(self, event: ClaimedOutboxEvent, error: Exception) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE task_outbox
                SET claim_token = NULL,
                    claim_expires_at = NULL,
                    last_error = $3
                WHERE id = $1
                  AND claim_token = $2
                  AND published_at IS NULL;
                """,
                event.outbox_id,
                event.claim_token,
                str(error)[:4000],
            )

    async def dispatch_ready(self, *, batch_size: int = 100) -> int:
        events = await self.claim_ready(batch_size=batch_size)
        dispatched = 0
        for event in events:
            try:
                await self.publish_claimed(event)
            except Exception as exc:
                await self.record_failure(event, exc)
                continue
            dispatched += 1
        return dispatched
