from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "schema_analytics.sql"
MIGRATIONS = ROOT / "migrations"


async def apply_sql_file(conn: asyncpg.Connection, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    if not sql.strip():
        return
    await conn.execute(sql)
    print(f"applied {path.relative_to(ROOT)}")


async def main() -> None:
    dsn = os.getenv(
        "DATABASE_DSN",
        "postgresql://app_user:app_password@localhost:5432/appdb",
    )
    conn = await asyncpg.connect(dsn)
    try:
        await apply_sql_file(conn, BASELINE)
        for migration in sorted(MIGRATIONS.glob("*.sql")):
            await apply_sql_file(conn, migration)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
