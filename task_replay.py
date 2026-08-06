from __future__ import annotations

import asyncio
import os
import logging
import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("task_replay_utility")

DATABASE_DSN = os.getenv(
    "DATABASE_DSN", 
    "postgresql://app_user:app_password@localhost:5432/appdb"
)


async def replay_all_dead_letters(pool: asyncpg.Pool) -> None:
    """
    Automated Data Triage Engine: Scans dead-letter tables, recreates fresh queue 
    tasks using identical payloads, and flushes processed items out of the archive.
    """
    logger.info("Initializing system-wide Dead-Letter recovery scan...")
    
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. Gather all pending dead letter rows for atomic batch processing
            dead_tasks = await conn.fetch("SELECT id, task_type, payload FROM worker_dead_letters FOR UPDATE;")
            
            if not dead_tasks:
                logger.info("Zero dead-letter tasks found. Database state is clean.")
                return

            logger.info(f"Discovered {len(dead_tasks)} failed task profiles. Initiating re-injection loop...")

            for row in dead_tasks:
                archive_id = row["id"]
                task_type = row["task_type"]
                payload = row["payload"]

                # 2. Inject a completely fresh, reset task tracking entity back into active queues
                await conn.execute(
                    """
                    INSERT INTO worker_tasks (task_type, payload, status, attempts, max_attempts, next_run_at)
                    VALUES ($1, $2, 'PENDING', 0, 5, NOW());
                    """,
                    task_type,
                    payload
                )

                # 3. Cleanly remove the triaged record out of the dead-letter history logs
                await conn.execute("DELETE FROM worker_dead_letters WHERE id = $1;", archive_id)
                logger.info(f"Successfully re-queued and replayed dead task array index: {archive_id}")

            logger.info("All dead letters triaged and restored back to global queues successfully.")


async def main() -> None:
    try:
        pool = await asyncpg.create_pool(dsn=DATABASE_DSN, min_size=1, max_size=2)
    except Exception as exc:
        logger.error(f"Replay utility failed to connect to database host: {exc}")
        return

    try:
        await replay_all_dead_letters(pool)
    finally:
        await pool.close()
        logger.info("Utility operations closed cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
