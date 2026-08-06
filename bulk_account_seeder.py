from __future__ import annotations

import asyncio
import os
import json
import logging
from typing import Any
import asyncpg
import pyotp
from twikit import Client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("bulk_seeder")

DATABASE_DSN = os.getenv("DATABASE_DSN", "postgresql://app_user:app_password@localhost:5432/appdb")

# Fill this array with your 40 identity credentials profiles
ACCOUNTS_INPUT_DATA = [
    {
        "username": "market_bot_01",
        "email": "bot01@gmail.com",
        "password": "SecurePassword123!",
        "totp_secret": "HI3TLU2NNNXYZABC"  # Put base32 secret here if 2FA is active, else leave as None
    }
]

async def register_account_tokens(pool: asyncpg.Pool, account_data: dict[str, Any]) -> None:
    client = Client('en-US')
    username = account_data["username"]
    
    try:
        logger.info(f"Connecting authentication tunnel for identity: {username}")
        
        if account_data.get("totp_secret"):
            totp = pyotp.TOTP(str(account_data["totp_secret"]))
            active_pin = totp.now()
            
            await client.login(
                auth_info_1=username,
                auth_info_2=account_data["email"],
                password=account_data["password"],
                totp_secret=active_pin
            )
        else:
            await client.login(
                auth_info_1=username,
                auth_info_2=account_data["email"],
                password=account_data["password"]
            )
            
        cookies_dict = client.get_cookies()
        cookies_json_string = json.dumps(cookies_dict)
        
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO service_tokens (token_key, token_value, status, updated_at)
                VALUES ($1, $2, 'ACTIVE', NOW())
                ON CONFLICT (token_key) DO UPDATE SET
                    token_value = EXCLUDED.token_value,
                    status = 'ACTIVE',
                    updated_at = NOW();
                """,
                username,
                cookies_json_string
            )
        logger.info(f"SUCCESS: Token session strings for [{username}] loaded directly to database.")
        
    except Exception as exc:
        logger.error(f"AUTHENTICATION FAILURE on account [{username}]: {exc}")

async def main() -> None:
    pool = await asyncpg.create_pool(dsn=DATABASE_DSN, min_size=1, max_size=5)
    try:
        for account in ACCOUNTS_INPUT_DATA:
            await register_account_tokens(pool, account)
            await asyncio.sleep(2.0)
    finally:
        await pool.close()
        logger.info("Bulk seeder process finalized successfully.")

if __name__ == "__main__":
    asyncio.run(main())
