from __future__ import annotations

import asyncio
import os
import logging
import signal
import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("analytics_alerts")

DATABASE_DSN = os.getenv(
    "DATABASE_DSN", 
    "postgresql://app_user:app_password@localhost:5432/appdb"
)

SCAN_INTERVAL_SECONDS = int(os.getenv("ALERT_SCAN_INTERVAL", "60"))
VELOCITY_THRESHOLD = int(os.getenv("ALERT_VELOCITY_THRESHOLD", "5"))


async def process_velocity_spike_detection(pool: asyncpg.Pool) -> None:
    """
    Scans pre-aggregated rollup rows to detect instant volume anomalies,
    writing active spike events to our operational monitoring tables.
    """
    logger.info("Executing real-time keyword velocity spike check...")
    
    async with pool.acquire() as conn:
        # High-speed index lookup across pre-calculated rollups over a fast 1-hour window mark
        records = await conn.fetch(
            """
            SELECT keyword, SUM(tweet_count) as total_volume
            FROM keyword_hourly_rollups
            WHERE window_timestamp >= NOW() - INTERVAL '1 hour'
            GROUP BY keyword
            HAVING SUM(tweet_count) > $1
            ORDER BY total_volume DESC;
            """,
            VELOCITY_THRESHOLD
        )

        for row in records:
            keyword = row["keyword"]
            volume = row["total_volume"]
            
            logger.warning(f"ALERT DETECTED: Keyword [{keyword}] is spiking rapidly! Volume: {volume} items/hr.")
            
            # Log the active anomaly directly into our alert event registers
            await conn.execute(
                """
                INSERT INTO system_operational_alerts (keyword, observed_volume, threshold_limit, triggered_at)
                VALUES ($1, $2, $3, NOW());
                """,
                keyword,
                int(volume),
                VELOCITY_THRESHOLD
            )


async def main() -> None:
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        logger.info("De-activating alert background daemons safely...")
        shutdown_event.set()

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            loop.add_signal_handler(sig, _request_shutdown)

    try:
        pool = await asyncpg.create_pool(dsn=DATABASE_DSN, min_size=1, max_size=3)
    except Exception as exc:
        logger.error(f"Alert manager engine failed to access database pool: {exc}")
        return

    try:
        while not shutdown_event.is_set():
            try:
                await process_velocity_spike_detection(pool)
            except Exception as loop_error:
                logger.error(f"Error captured during active alert scanning sequence: {loop_error}")
            
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=SCAN_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                continue
    finally:
        await pool.close()
        logger.info("Alert scanning daemon service terminated cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
