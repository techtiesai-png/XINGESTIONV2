from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import TypeVar

import asyncpg

from xingestion.control_plane import RedisStreamQueue, StreamDelivery, TaskLease


logger = logging.getLogger("task_lease_guard")
T = TypeVar("T")


class TaskLeaseLost(RuntimeError):
    """Raised when a worker no longer owns the durable PostgreSQL task lease."""


class TaskLeaseGuard:
    """Maintain the durable task lease while one delivery is processed.

    PostgreSQL is the execution-authority fence. Redis PEL ownership is kept
    fresh as an optimization so healthy work stays below the XAUTOCLAIM idle
    threshold, but losing/reacquiring Redis ownership alone never authorizes a
    second execution while the PostgreSQL lease is still valid.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        queue: RedisStreamQueue,
        *,
        lease_seconds: int,
        heartbeat_seconds: float,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")
        if heartbeat_seconds <= 0:
            raise ValueError("heartbeat_seconds must be > 0")
        if heartbeat_seconds >= lease_seconds:
            raise ValueError("heartbeat_seconds must be shorter than lease_seconds")
        self.pool = pool
        self.queue = queue
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds

    async def _touch_pending(
        self,
        *,
        message_id: str,
        consumer_name: str,
    ) -> bool:
        """Reset Redis pending-entry idle time while preserving this consumer."""

        claimed = await self.queue.redis.xclaim(
            name=self.queue.stream_name,
            groupname=self.queue.group_name,
            consumername=consumer_name,
            min_idle_time=0,
            message_ids=[message_id],
            idle=0,
            justid=True,
        )
        claimed_ids = {
            item.decode("utf-8") if isinstance(item, bytes) else str(item)
            for item in (claimed or [])
        }
        return message_id in claimed_ids

    async def renew_once(
        self,
        *,
        task: TaskLease,
        delivery: StreamDelivery,
        consumer_name: str,
    ) -> None:
        """Renew PostgreSQL ownership, then refresh Redis pending-entry idle time."""

        async with self.pool.acquire() as conn:
            status = await conn.execute(
                """
                UPDATE worker_tasks
                SET lease_expires_at = NOW() + ($4 * INTERVAL '1 second'),
                    updated_at = NOW()
                WHERE id = $1
                  AND delivery_generation = $2
                  AND lease_owner = $3
                  AND status = 'RUNNING'
                  AND lease_expires_at > NOW();
                """,
                task.id,
                task.delivery_generation,
                task.lease_owner,
                self.lease_seconds,
            )
        if status != "UPDATE 1":
            raise TaskLeaseLost(
                "durable task lease is no longer owned by "
                f"{task.lease_owner} for task={task.id} "
                f"generation={task.delivery_generation}"
            )

        try:
            touched = await self._touch_pending(
                message_id=delivery.message_id,
                consumer_name=consumer_name,
            )
        except Exception as exc:
            # PostgreSQL remains the execution fence. A Redis heartbeat failure
            # can cause an unnecessary PEL reclaim, but the new consumer still
            # cannot lease the task while this durable lease remains valid.
            logger.warning(
                "redis pending-entry heartbeat failed task=%s generation=%s: %s",
                task.id,
                task.delivery_generation,
                exc,
            )
            return

        if not touched:
            logger.warning(
                "redis pending entry was not found during heartbeat "
                "task=%s generation=%s message=%s",
                task.id,
                task.delivery_generation,
                delivery.message_id,
            )

    async def release_for_recovery(self, task: TaskLease) -> bool:
        """Release a gracefully cancelled task without fabricating a retry."""

        async with self.pool.acquire() as conn:
            status = await conn.execute(
                """
                UPDATE worker_tasks
                SET status = 'ENQUEUED',
                    lease_owner = NULL,
                    lease_started_at = NULL,
                    lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE id = $1
                  AND delivery_generation = $2
                  AND lease_owner = $3
                  AND status = 'RUNNING';
                """,
                task.id,
                task.delivery_generation,
                task.lease_owner,
            )
        return status == "UPDATE 1"

    async def _heartbeat_loop(
        self,
        *,
        task: TaskLease,
        delivery: StreamDelivery,
        consumer_name: str,
        stop_event: asyncio.Event,
    ) -> None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self.heartbeat_seconds,
                )
                return
            except TimeoutError:
                pass

            await self.renew_once(
                task=task,
                delivery=delivery,
                consumer_name=consumer_name,
            )

    async def run_guarded(
        self,
        *,
        task: TaskLease,
        delivery: StreamDelivery,
        consumer_name: str,
        operation: Awaitable[T],
    ) -> T:
        """Run work while continuously fencing it with the durable lease."""

        stop_event = asyncio.Event()
        operation_task = asyncio.create_task(operation)
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(
                task=task,
                delivery=delivery,
                consumer_name=consumer_name,
                stop_event=stop_event,
            )
        )

        try:
            done, _pending = await asyncio.wait(
                {operation_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if heartbeat_task in done:
                heartbeat_error = heartbeat_task.exception()
                if heartbeat_error is not None:
                    operation_task.cancel()
                    await asyncio.gather(operation_task, return_exceptions=True)
                    raise heartbeat_error

            result = await operation_task
            stop_event.set()
            await heartbeat_task
            return result
        except asyncio.CancelledError:
            operation_task.cancel()
            heartbeat_task.cancel()
            await asyncio.gather(
                operation_task,
                heartbeat_task,
                return_exceptions=True,
            )
            await self.release_for_recovery(task)
            raise
        finally:
            stop_event.set()
            if not heartbeat_task.done():
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
