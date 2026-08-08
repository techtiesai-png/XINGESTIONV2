from __future__ import annotations

import asyncio
import logging
import signal

import asyncpg
import redis.asyncio as aioredis

from xingestion.config import Settings
from xingestion.control_plane import RedisStreamQueue
from xingestion.outbox import DurableOutboxDispatcher


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("outbox_dispatcher")


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
        min_size=1,
        max_size=max(2, settings.db_pool_min_size),
        timeout=5.0,
    )
    redis = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
    )
    queue = RedisStreamQueue(
        redis,
        stream_name=settings.task_stream,
        group_name=settings.task_consumer_group,
    )
    dispatcher = DurableOutboxDispatcher(
        pool,
        redis,
        stream_name=settings.task_stream,
    )

    try:
        await redis.ping()
        await queue.ensure_group()
        logger.info(
            "outbox dispatcher online stream=%s group=%s",
            settings.task_stream,
            settings.task_consumer_group,
        )
        while not shutdown_event.is_set():
            count = await dispatcher.dispatch_ready(
                batch_size=settings.outbox_batch_size
            )
            if count == 0:
                try:
                    await asyncio.wait_for(
                        shutdown_event.wait(),
                        timeout=settings.outbox_poll_seconds,
                    )
                except asyncio.TimeoutError:
                    pass
    finally:
        await redis.aclose()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
