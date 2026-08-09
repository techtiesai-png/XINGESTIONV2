from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from worker import IngestionExecutor
from xingestion.capabilities import CAPABILITY_TASK_TYPE, CapabilityRequest
from xingestion.collectors import MockSearchAdapter, PermanentTaskFailure
from xingestion.config import Settings
from xingestion.control_plane import TaskLease


class UnusedTokenRepository:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"mock capability route should not access sessions: {name}")


def make_task(*, payload: dict[str, Any]) -> TaskLease:
    return TaskLease(
        id=101,
        task_type=CAPABILITY_TASK_TYPE,
        payload=payload,
        attempts=0,
        max_attempts=3,
        delivery_generation=0,
        lease_owner="worker-test",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )


@pytest.mark.asyncio
async def test_worker_executes_canonical_search_through_planner(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    monkeypatch.setenv("COLLECTOR_MAX_PAGES", "2")
    settings = Settings.from_env()
    executor = IngestionExecutor(
        settings=settings,
        token_repo=UnusedTokenRepository(),  # type: ignore[arg-type]
        primary_adapter=MockSearchAdapter(),
        recovery_adapter=None,
    )
    request = CapabilityRequest(
        capability_id="SEARCH_TWEETS",
        capability_contract_version="1",
        params={"query": "capability test", "product": "Latest", "max_pages": 1},
        page_size=2,
    )

    items, metadata = await executor.collect(
        task=make_task(payload=request.to_task_payload()), worker_id="worker-test"
    )

    assert len(items) == 2
    assert metadata["capability_id"] == "SEARCH_TWEETS"
    assert metadata["capability_contract_version"] == "1"
    assert metadata["acquisition_route"] == "legacy-source-adapter/search-tweets/v1"
    assert metadata["product"] == "Latest"


@pytest.mark.asyncio
async def test_worker_rejects_unapproved_product_semantics(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    settings = Settings.from_env()
    executor = IngestionExecutor(
        settings=settings,
        token_repo=UnusedTokenRepository(),  # type: ignore[arg-type]
        primary_adapter=MockSearchAdapter(),
        recovery_adapter=None,
    )
    request = CapabilityRequest(
        capability_id="SEARCH_TWEETS",
        capability_contract_version="1",
        params={"query": "capability test", "product": "Top"},
    )

    with pytest.raises(PermanentTaskFailure):
        await executor.collect(
            task=make_task(payload=request.to_task_payload()), worker_id="worker-test"
        )
