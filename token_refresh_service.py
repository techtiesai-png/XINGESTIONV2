from __future__ import annotations

import asyncio
import os
import json
import logging
from datetime import datetime, timezone
from typing import Any
import asyncpg
import pyotp
from twikit import Client

class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        return json.dumps(log_payload)

log_handler = logging.StreamHandler()
log_handler.setFormatter(JSONFormatter())

logger = logging.getLogger("token_refresh_service")
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)
logger.propagate = False

DATABASE_DSN = os.getenv(
    "DATABASE_DSN", 
    "postgresql://app_user:app_password@localhost:5432/appdb"
)

REFRESH_POLL_INTERVAL_SECONDS = 60.0


async def programmatically_repair_session(
    pool: asyncpg.Pool, 
    token_id: int, 
    username: str, 
    account_credentials_json: str
) -> None:
    """
    Executes an out-of-band headless re-authentication sequence for a dead session.
    Parses original credentials keys, generates fresh TOTP tokens, and revives the row.
    """
    try:
        creds = json.loads(account_credentials_json)
        email = creds.get("email")
        password = creds.get("password")
        totp_secret = creds.get("totp_secret")

        if not email or not password:
            logger.error(f"Cannot auto-repair token ID {token_id}: Missing fallback credentials keys.")
            return

        logger.info(f"Identity Auto-Repair System: Launching background re-auth tunnel for [{username}]")
        client = Client(language="en-US")

        if totp_secret and str(totp_secret).strip().upper() != "NONE":
            totp = pyotp.TOTP(str(totp_secret).strip().replace(" ", ""))
            active_pin = totp.now()
            
            await client.login(
                auth_info_1=username,
                auth_info_2=email,
                password=password,
                totp_secret=active_pin
            )
        else:
            await client.login(
                auth_info_1=username,
                auth_info_2=email,
                password=password
            )

        fresh_cookies_dict = client.get_cookies()
        fresh_cookies_json_text = json.dumps(fresh_cookies_dict)

        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE service_tokens
                SET token_value = $1,
                    status = 'ACTIVE',
                    cooldown_until = NULL,
                    last_error = NULL,
                    updated_at = NOW()
                WHERE id = $2;
                """,
                fresh_cookies_json_text,
                token_id
            )
        logger.info(f"SUCCESS: Automated Session Repair Complete! Account [{username}] is now fully ACTIVE.")

    except Exception as exc:
        logger.error(f"Auto-Repair Critical Failure for account [{username}]: {exc}")
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE service_tokens SET status = 'REVOKED', last_error = $1 WHERE id = $2;",
                f"re_auth_failed: {exc}"[:2000],
                token_id
            )


async def scan_and_triage_dead_tokens(pool: asyncpg.Pool) -> None:
    """
    Scans the token database rows pool to isolate tokens benched with auth issues.
    """
    async with pool.acquire() as conn:
        dead_candidates = await conn.fetch(
            """
            SELECT id, token_key, token_value 
            FROM service_tokens 
            WHERE status = 'COOLDOWN' 
              AND (last_error LIKE '%401%' OR last_error LIKE '%unauthorized%' OR last_error LIKE '%auth%');
            """
        )

        if not dead_candidates:
            return

        logger.info(f"Scanner found {len(dead_candidates)} dead account session targets requiring auto-repair.")

        for row in dead_candidates:
            token_id = row["id"]
            username = row["token_key"]
            raw_payload = row["token_value"]

            await programmatically_repair_session(pool, token_id, username, raw_payload)


async def main() -> None:
    logger.info("Initializing Automated Identity Session Auto-Repair Daemon...")
    
    try:
        pool = await asyncpg.create_pool(dsn=DATABASE_DSN, min_size=1, max_size=2)
    except Exception as exc:
        logger.error(f"Session Auto-Repair system failed to initialize PostgreSQL pool: {exc}")
        return

    try:
        while True:
            await scan_and_triage_dead_tokens(pool)
            await asyncio.sleep(REFRESH_POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        pass
    finally:
        await pool.close()
        logger.info("Identity Session Auto-Repair Daemon shutdown cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
