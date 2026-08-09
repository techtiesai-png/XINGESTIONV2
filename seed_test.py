from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg

from xingestion.capabilities import CAPABILITY_TASK_TYPE, CapabilityRequest
from xingestion.config import Settings
from xingestion.control_plane import TaskRepository

TOKEN_ROWS = [
    ("test_account_01", json.dumps({})),
    ("test_account_02", json.dumps({})),
]

TASK_PAYLOADS: list[dict[str, Any]] = [
    {"search_keyword": "bitcoin"},
    {"search_keyword": "ai_agents"},
    {"search_keyword": "tech_trends"},
    {"search_keyword": "data_engineering"},
    {"search_keyword": "open_source"},
]


async def seed_tokens(conn: asyncpg.Connection) -> int:
    for token_key, token_value in TOKEN_ROWS:
        await conn.execute(
            """
            INSERT INTO service_tokens (
                token_key,
                token_value,
                status,
                max_concurrency,
                updated_at
            )
            VALUES ($1, $2, 'REVOKED', 1, NOW())
            ON CONFLICT (token_key) DO UPDATE SET
                token_value = EXCLUDED.token_value,
                status = 'REVOKED',
                updated_at = NOW();
            """,
            token_key,
            token_value,
        )
    return len(TOKEN_ROWS)


async def main() -> None:
    settings = Settings.from_env()
    pool = await asyncpg.create_pool(
        dsn=settings.database_dsn,
        min_size=1,
        max_size=4,
    )
    task_repo = TaskRepository(pool)
    try:
        async with pool.acquire() as conn:
            token_count = await seed_tokens(conn)

        task_ids = []
        for payload in TASK_PAYLOADS:
            keyword = str(payload["search_keyword"])
            capability_request = CapabilityRequest(
                capability_id="SEARCH_TWEETS",
                capability_contract_version="1",
                params={"query": keyword, "product": "Latest", "max_pages": 1},
                page_size=20,
                correlation_id=f"seed:{keyword}",
            )
            task_ids.append(
                await task_repo.create_task(
                    task_type=CAPABILITY_TASK_TYPE,
                    payload=capability_request.to_task_payload(),
                    idempotency_key=f"seed:SEARCH_TWEETS:v1:{keyword}",
                    max_attempts=5,
                )
            )

        print(
            f"Seeded/updated {token_count} non-live test session rows and "
            f"ensured {len(task_ids)} durable tasks: {task_ids}"
        )
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
