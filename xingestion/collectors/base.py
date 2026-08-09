from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from xingestion.control_plane import TokenLease


@dataclass(slots=True, frozen=True)
class CollectionRequest:
    query: str
    product: str = "Latest"
    cursor: str | None = None
    page_size: int = 20


@dataclass(slots=True)
class CollectionBatch:
    items: list[dict[str, Any]]
    next_cursor: str | None
    adapter_name: str
    adapter_version: str
    metadata: dict[str, Any] = field(default_factory=dict)


class CollectionError(RuntimeError):
    failure_class = "collection_error"
    retryable = True
    session_fault = False

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class AuthenticationFailure(CollectionError):
    failure_class = "authentication_failure"
    retryable = True
    session_fault = True


class RateLimited(CollectionError):
    failure_class = "rate_limited"
    retryable = True
    session_fault = True


class TransientNetworkFailure(CollectionError):
    failure_class = "transient_network_failure"
    retryable = True
    session_fault = False


class CollectorChanged(CollectionError):
    failure_class = "collector_changed"
    retryable = True
    session_fault = False


class PermanentTaskFailure(CollectionError):
    failure_class = "permanent_task_failure"
    retryable = False
    session_fault = False


class SourceAdapter(Protocol):
    name: str
    version: str
    requires_session: bool

    async def collect(
        self,
        request: CollectionRequest,
        *,
        session: TokenLease | None,
    ) -> CollectionBatch:
        ...

    async def close(self) -> None:
        ...
