from __future__ import annotations

import asyncio
import logging
import os
import signal

import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("db_cleanup_daemon")

DATABASE_DSN = os.getenv(
    "DATABASE_DSN",
    "postgresql://app_user:app_password@localhost:5432/appdb",
)
DATA_RETENTION_DAYS = int(os.getenv("DATA_RETENTION_DAYS", "30"))
CLEANUP_POLL_INTERVAL_SECONDS = int(os.getenv("CLEANUP_POLL_INTERVAL", "3600"))


async def execute_database_garbage_collection(pool: asyncpg.Pool) -> None:
    """Runs automated relational storage sweeps to clear expired records."""
    logger.info("Starting automated relational storage space optimization sweep...")

    async with pool.acquire() as conn:
        async with conn.transaction():
            deleted_tasks = await conn.execute(
                "DELETE FROM worker_tasks WHERE status = 'DONE' AND leased_at IS NULL;"
            )
            logger.info("Storage Lifecycle Manager: Purged completed tasks. Return: %s", deleted_tasks)

            purged_tweets = await conn.execute(
                f"""
                DELETE FROM social_insights_feed
                WHERE ingested_at < NOW() - INTERVAL '{DATA_RETENTION_DAYS} days';
                """
            )
            logger.info(
                "Storage Lifecycle Manager: Enforced %s-day retention threshold across analytics columns. Return: %s",
                DATA_RETENTION_DAYS,
                purged_tweets,
            )

    async with pool.acquire() as conn:
        await conn.execute("VACUUM ANALYZE social_insights_feed;")
        logger.info("Storage Lifecycle Manager: Database structural VACUUM index pass completed.")


async def main() -> None:
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        logger.info("Shutdown input intercepted. Closing clean-up daemon threads safely...")
        shutdown_event.set()

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            loop.add_signal_handler(sig, _request_shutdown)

    try:
        pool = await asyncpg.create_pool(dsn=DATABASE_DSN, min_size=1, max_size=2)
    except Exception as exc:
        logger.error("Lifecycle Daemon failed to initialize database pool: %s", exc)
        return

    logger.info(
        "Automated Space Optimization System initialized. Scan frequency: every %s seconds.",
        CLEANUP_POLL_INTERVAL_SECONDS,
    )

    try:
        while not shutdown_event.is_set():
            try:
                await execute_database_garbage_collection(pool)
            except Exception as loop_error:
                logger.error("Error captured during active garbage collection loop pass: %s", loop_error)

            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=CLEANUP_POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                continue
    finally:
        await pool.close()
        logger.info("Database lifecycle optimization daemon stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
