from __future__ import annotations

import asyncio
import os
import json
import logging
import signal
from datetime import datetime, timezone
import asyncpg
import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("analytics_briefs")

DATABASE_DSN = os.getenv(
    "DATABASE_DSN", 
    "postgresql://app_user:app_password@localhost:5432/appdb"
)
LLM_API_KEY = os.getenv("LLM_API_KEY", "mock_key_or_insert_real_openai_token")
LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "gpt-4o-mini")
BRIEF_POLL_INTERVAL_SECONDS = 3600 # Runs exactly once per hour


async def generate_and_archive_executive_brief(pool: asyncpg.Pool) -> None:
    """
    Downstream Synthesis: Pulls the top 50 viral tweets, runs deterministic 
    summarization via temperature=0.0, and archives the resulting brief to the DB.
    """
    logger.info("Starting hourly AI Executive Summary Briefing generation cycle...")
    
    async with pool.acquire() as conn:
        # Extract the top 50 viral tweets over the trailing hour horizon
        viral_rows = await conn.fetch(
            """
            SELECT author_handle, text_content, (engagement_likes + engagement_retweets) as viral_score
            FROM social_insights_feed
            WHERE ingested_at >= NOW() - INTERVAL '1 hour'
            ORDER BY viral_score DESC
            LIMIT 50;
            """
        )
        
        if not viral_rows:
            logger.info("Skipping brief cycle: Zero viral tracking rows found over the trailing hour.")
            return

        # Assemble the structured prompt context mapping
        data_payload_text = ""
        for row in viral_rows:
            data_payload_text += f"- [@{row['author_handle']}]: \"{row['text_content']}\" (Score: {row['viral_score']})\n"

        system_prompt = (
            "You are a Principal Market Intelligence Analyst. Analyze the following data streams "
            "captured over the past 60 minutes. Distill these raw signals into an executive, "
            "highly dense, 3-bullet-point summary tracking active trends. Do not introduce outside data. "
            "Return ONLY the structural markdown block."
        )

        user_content = f"Source Data Records:\n{data_payload_text}"

        # Execute a secure, deterministic call targeting the cloud provider network
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://openai.com",
                    headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
                    json={
                        "model": LLM_MODEL_NAME,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content}
                        ],
                        "temperature": 0.0 # Strict hallucination containment guardrail
                    }
                )
                response.raise_for_status()
                result_json = response.json()
                brief_summary = result_json["choices"][0]["message"]["content"].strip()

            # Persist the generated cached summary directly into the relational storage tier
            await conn.execute(
                "INSERT INTO executive_briefs_history (summary_text, generated_at) VALUES ($1, NOW());",
                brief_summary
            )
            logger.info("SUCCESS: Hourly AI Executive Brief generated and archived cleanly to database.")

        except Exception as api_exc:
            logger.error(f"Failed to generate AI executive brief due to network endpoint failure: {api_exc}")


async def main() -> None:
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        logger.info("Shutting down briefs background worker...")
        shutdown_event.set()

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            loop.add_signal_handler(sig, _request_shutdown)

    try:
        pool = await asyncpg.create_pool(dsn=DATABASE_DSN, min_size=1, max_size=2)
    except Exception as exc:
        logger.error(f"Brief daemon failed to initialize database pool: {exc}")
        return

    try:
        while not shutdown_event.is_set():
            await generate_and_archive_executive_brief(pool)
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=BRIEF_POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                continue
    finally:
        await pool.close()
        logger.info("Briefing loop worker stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
