from __future__ import annotations

import asyncio
import os

import asyncpg

from xingestion.config import Settings
from xingestion.control_plane import TaskRepository


async def replay_dead_letters(pool: asyncpg.Pool, *, limit: int) -> int:
    task_repo = TaskRepository(pool)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                id,
                task_type,
                payload,
                replay_generation
            FROM worker_dead_letters
            WHERE replayed_at IS NULL
            ORDER BY failed_at ASC, id ASC
            LIMIT $1;
            """,
            limit,
        )

    replayed = 0
    for row in rows:
        archive_id = int(row["id"])
        generation = int(row["replay_generation"]) + 1
        payload = row["payload"]
        task_id = await task_repo.create_task(
            task_type=str(row["task_type"]),
            payload=dict(payload),
            idempotency_key=f"dead-letter:{archive_id}:replay:{generation}",
            max_attempts=int(os.getenv("REPLAY_MAX_ATTEMPTS", "5")),
            priority=int(os.getenv("REPLAY_PRIORITY", "50")),
        )
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE worker_dead_letters
                SET replayed_at = NOW(),
                    replay_task_id = $2,
                    replay_generation = $3
                WHERE id = $1
                  AND replayed_at IS NULL;
                """,
                archive_id,
                task_id,
                generation,
            )
        if result == "UPDATE 1":
            replayed += 1

    return replayed


async def main() -> None:
    settings = Settings.from_env()
    limit = max(1, int(os.getenv("REPLAY_LIMIT", "100")))
    pool = await asyncpg.create_pool(
        dsn=settings.database_dsn,
        min_size=1,
        max_size=2,
    )
    try:
        count = await replay_dead_letters(pool, limit=limit)
        print(f"Replayed {count} dead-letter tasks into the durable task ledger.")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
