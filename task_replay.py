from __future__ import annotations

import asyncio
import os

import asyncpg

from xingestion.config import Settings
from xingestion.replay import DeadLetterReplayService, ReplaySelector


def parse_dead_letter_ids(raw: str | None) -> tuple[int, ...]:
    if not raw:
        return ()
    values: list[int] = []
    for part in raw.split(","):
        text = part.strip()
        if not text:
            continue
        value = int(text)
        if value <= 0:
            raise ValueError("REPLAY_DEAD_LETTER_IDS must contain positive IDs")
        values.append(value)
    return tuple(dict.fromkeys(values))


async def main() -> None:
    settings = Settings.from_env()
    limit = max(1, int(os.getenv("REPLAY_LIMIT", "100")))
    max_attempts = max(1, int(os.getenv("REPLAY_MAX_ATTEMPTS", "5")))
    priority = int(os.getenv("REPLAY_PRIORITY", "50"))
    selector = ReplaySelector(
        dead_letter_ids=parse_dead_letter_ids(
            os.getenv("REPLAY_DEAD_LETTER_IDS")
        ),
        task_type=os.getenv("REPLAY_TASK_TYPE") or None,
        failure_class=os.getenv("REPLAY_FAILURE_CLASS") or None,
    )

    pool = await asyncpg.create_pool(
        dsn=settings.database_dsn,
        min_size=1,
        max_size=2,
    )
    try:
        service = DeadLetterReplayService(pool)
        results = await service.replay(
            selector=selector,
            limit=limit,
            max_attempts=max_attempts,
            priority=priority,
        )
        print(
            "Replayed "
            f"{len(results)} dead-letter tasks into the durable task ledger: "
            f"{[result.replay_task_id for result in results]}"
        )
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
