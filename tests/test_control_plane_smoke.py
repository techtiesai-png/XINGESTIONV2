from __future__ import annotations

from xingestion.config import Settings
from xingestion.control_plane import StreamDelivery, TaskLease, TokenLease


def test_settings_defaults_are_positive(monkeypatch):
    monkeypatch.delenv("TASK_LEASE_SECONDS", raising=False)
    monkeypatch.delenv("TOKEN_LEASE_SECONDS", raising=False)
    monkeypatch.delenv("WORKER_CONCURRENCY", raising=False)

    settings = Settings.from_env()

    assert settings.task_lease_seconds > 0
    assert settings.token_lease_seconds > 0
    assert settings.worker_concurrency > 0
    assert settings.task_reclaim_idle_ms >= settings.task_lease_seconds * 1000


def test_control_plane_value_objects_are_stable():
    delivery = StreamDelivery(message_id="1-0", task_id=10, generation=3)
    assert delivery.task_id == 10
    assert delivery.generation == 3

    task = TaskLease(
        id=10,
        task_type="X_KEYWORD_SEARCH",
        payload={"search_keyword": "example"},
        attempts=1,
        max_attempts=5,
        delivery_generation=3,
        lease_owner="worker-1",
        lease_expires_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ),
    )
    assert task.delivery_generation == delivery.generation

    token = TokenLease(
        lease_id=7,
        token_id=11,
        token_key="account",
        token_value="{}",
        lease_owner="worker-1",
        lease_expires_at=task.lease_expires_at,
    )
    assert token.id == token.token_id == 11
