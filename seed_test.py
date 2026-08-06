from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import asyncpg


DATABASE_DSN = os.getenv("DATABASE_DSN", "postgresql://app_user:app_password@localhost:5432/appdb")


TOKEN_ROWS = [
    ("test_account_01", "mock_session_cookie_token_aaa"),
    ("test_account_02", "mock_session_cookie_token_bbb"),
]


# Aligned payloads with our new search_keyword requirements to prevent worker errors
TASK_PAYLOADS: list[dict[str, Any]] = [
    {"search_keyword": "bitcoin"},
    {"search_keyword": "ai_agents"},
    {"search_keyword": "tech_trends"},
    {"search_keyword": "data_engineering"},
    {"search_keyword": "open_source"},
]


async def seed_tokens(conn: Any) -> int:
    seeded = 0
    for token_key, token_value in TOKEN_ROWS:
        await conn.execute(
            """
            INSERT INTO service_tokens (token_key, token_value, status)
            VALUES ($1, $2, 'ACTIVE')
            ON CONFLICT (token_key) DO UPDATE SET
                token_value = EXCLUDED.token_value,
                status = 'ACTIVE'
            """,
            token_key,
            token_value,
        )
        seeded += 1
    return seeded


async def seed_tasks(conn: Any) -> int:
    seeded = 0
    for payload in TASK_PAYLOADS:
        await conn.execute(
            """
            INSERT INTO worker_tasks (task_type, payload, status, attempts, max_attempts, next_run_at)
            VALUES ($1, $2::jsonb, 'PENDING', 0, 5, NOW())
            ON CONFLICT DO NOTHING
            """,
            "X_KEYWORD_SEARCH",
            json.dumps(payload),
        )
        seeded += 1
    return seeded


async def main() -> None:
    pool = await asyncpg.create_pool(dsn=DATABASE_DSN, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            token_count = await seed_tokens(conn)
            task_count = await seed_tasks(conn)
        print(f"Seeded {token_count} service token rows and {task_count} worker task rows successfully.")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
