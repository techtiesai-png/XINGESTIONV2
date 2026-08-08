from __future__ import annotations

import os
from dataclasses import dataclass


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be > 0")
    return value


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be > 0")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    database_dsn: str
    redis_url: str
    task_stream: str
    task_consumer_group: str
    task_lease_seconds: int
    task_read_block_ms: int
    task_reclaim_idle_ms: int
    outbox_poll_seconds: float
    outbox_batch_size: int
    worker_concurrency: int
    idle_sleep_seconds: float
    token_lease_seconds: int
    token_cooldown_seconds: int
    db_pool_min_size: int
    db_pool_max_size: int
    request_timeout_seconds: float
    http_proxy: str | None
    mock_mode: bool
    collector_page_size: int
    collector_max_pages: int
    browser_recovery_enabled: bool

    @classmethod
    def from_env(cls) -> "Settings":
        min_pool = _positive_int("DB_POOL_MIN_SIZE", 2)
        max_pool = _positive_int("DB_POOL_MAX_SIZE", 20)
        if min_pool > max_pool:
            raise ValueError("DB_POOL_MIN_SIZE cannot exceed DB_POOL_MAX_SIZE")

        lease_seconds = _positive_int("TASK_LEASE_SECONDS", 120)
        reclaim_idle_ms = _positive_int(
            "TASK_RECLAIM_IDLE_MS",
            max(lease_seconds * 1000, 120_000),
        )

        return cls(
            database_dsn=os.getenv(
                "DATABASE_DSN",
                "postgresql://app_user:app_password@localhost:5432/appdb",
            ),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            task_stream=os.getenv("TASK_STREAM", "stream:x_tasks"),
            task_consumer_group=os.getenv(
                "TASK_CONSUMER_GROUP",
                "xingestion-workers",
            ),
            task_lease_seconds=lease_seconds,
            task_read_block_ms=_positive_int("TASK_READ_BLOCK_MS", 2_000),
            task_reclaim_idle_ms=reclaim_idle_ms,
            outbox_poll_seconds=_positive_float("OUTBOX_POLL_SECONDS", 0.25),
            outbox_batch_size=_positive_int("OUTBOX_BATCH_SIZE", 100),
            worker_concurrency=_positive_int("WORKER_CONCURRENCY", 4),
            idle_sleep_seconds=_positive_float("IDLE_SLEEP_SECONDS", 0.25),
            token_lease_seconds=_positive_int("TOKEN_LEASE_SECONDS", 90),
            token_cooldown_seconds=_positive_int("COOLDOWN_SECONDS", 300),
            db_pool_min_size=min_pool,
            db_pool_max_size=max_pool,
            request_timeout_seconds=_positive_float(
                "REQUEST_TIMEOUT_SECONDS",
                20.0,
            ),
            http_proxy=os.getenv("HTTP_PROXY") or None,
            mock_mode=os.getenv("MOCK_MODE", "false").lower()
            in {"1", "true", "yes", "on"},
            collector_page_size=min(
                _positive_int("COLLECTOR_PAGE_SIZE", 20),
                20,
            ),
            collector_max_pages=_positive_int("COLLECTOR_MAX_PAGES", 1),
            browser_recovery_enabled=os.getenv(
                "ENABLE_BROWSER_RECOVERY", "true"
            ).lower() in {"1", "true", "yes", "on"},
        )
