from __future__ import annotations

import hashlib

from xingestion.collectors.base import CollectionBatch, CollectionRequest
from xingestion.control_plane import TokenLease


class MockSearchAdapter:
    name = "mock"
    version = "1"
    requires_session = False

    async def collect(
        self,
        request: CollectionRequest,
        *,
        session: TokenLease | None,
    ) -> CollectionBatch:
        query_hash = hashlib.sha256(request.query.encode("utf-8")).hexdigest()[:16]
        items = [
            {
                "original_tweet_id": f"mock_{query_hash}_{idx}",
                "author_id": f"mock_author_{idx}",
                "author_handle": f"mock_handle_{idx}",
                "text_content": (
                    "Synthetic fixture record for collection query: "
                    f"{request.query}"
                ),
                "engagement_likes": idx * 100,
                "engagement_retweets": idx * 25,
                "conversation_id": f"mock_conversation_{query_hash}",
                "sentiment_label": "NEUTRAL",
            }
            for idx in range(1, min(request.page_size, 3) + 1)
        ]
        return CollectionBatch(
            items=items,
            next_cursor=None,
            adapter_name=self.name,
            adapter_version=self.version,
            metadata={"fixture": True, "product": request.product},
        )

    async def close(self) -> None:
        return None
