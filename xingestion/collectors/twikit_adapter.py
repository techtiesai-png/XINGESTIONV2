from __future__ import annotations

import json
import time
from importlib.metadata import PackageNotFoundError, version as package_version
from typing import Any

from twikit import Client
from twikit.errors import (
    AccountLocked,
    AccountSuspended,
    Forbidden,
    RequestTimeout,
    ServerError,
    TooManyRequests,
    TwitterException,
    Unauthorized,
)

from xingestion.collectors.base import (
    AuthenticationFailure,
    CollectionBatch,
    CollectionRequest,
    CollectorChanged,
    RateLimited,
    TransientNetworkFailure,
)
from xingestion.control_plane import TokenLease


def _package_version() -> str:
    try:
        return package_version("twikit")
    except PackageNotFoundError:
        return "unknown"


class TwikitSearchAdapter:
    name = "twikit"
    version = _package_version()
    requires_session = True

    def __init__(
        self,
        *,
        language: str = "en-US",
        proxy: str | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.language = language
        self.proxy = proxy
        self.timeout_seconds = timeout_seconds

    async def collect(
        self,
        request: CollectionRequest,
        *,
        session: TokenLease | None,
    ) -> CollectionBatch:
        if session is None:
            raise AuthenticationFailure("twikit adapter requires a session")

        client = Client(
            language=self.language,
            proxy=self.proxy,
            timeout=self.timeout_seconds,
        )
        try:
            try:
                cookies = json.loads(session.token_value)
                if not isinstance(cookies, dict):
                    raise TypeError("session cookie payload must be a JSON object")
                client.set_cookies(cookies)
            except Exception as exc:
                raise AuthenticationFailure(
                    f"session {session.token_id} contains invalid cookie data"
                ) from exc

            result = await client.search_tweet(
                query=request.query,
                product="Latest",
                count=max(1, min(request.page_size, 20)),
                cursor=request.cursor,
            )

            items: list[dict[str, Any]] = []
            for tweet in result:
                user = getattr(tweet, "user", None)
                if user is None:
                    continue
                items.append(
                    {
                        "original_tweet_id": str(tweet.id),
                        "author_id": str(user.id),
                        "author_handle": str(user.screen_name),
                        "text_content": str(tweet.text),
                        "engagement_likes": int(
                            getattr(tweet, "favorite_count", 0) or 0
                        ),
                        "engagement_retweets": int(
                            getattr(tweet, "retweet_count", 0) or 0
                        ),
                        "conversation_id": str(
                            getattr(tweet, "conversation_id", None) or tweet.id
                        ),
                        "sentiment_label": "NEUTRAL",
                    }
                )

            return CollectionBatch(
                items=items,
                next_cursor=getattr(result, "next_cursor", None),
                adapter_name=self.name,
                adapter_version=self.version,
                metadata={
                    "session_id": session.token_id,
                    "query": request.query,
                },
            )
        except (Unauthorized, Forbidden, AccountLocked, AccountSuspended) as exc:
            raise AuthenticationFailure(str(exc)) from exc
        except TooManyRequests as exc:
            reset_at = getattr(exc, "rate_limit_reset", None)
            retry_after = None
            if reset_at:
                retry_after = max(float(reset_at) - time.time(), 1.0)
            raise RateLimited(
                str(exc),
                retry_after_seconds=retry_after,
            ) from exc
        except (RequestTimeout, ServerError) as exc:
            raise TransientNetworkFailure(str(exc)) from exc
        except TwitterException as exc:
            raise CollectorChanged(str(exc)) from exc
        except (OSError, TimeoutError) as exc:
            raise TransientNetworkFailure(str(exc)) from exc
        finally:
            http_client = getattr(client, "http", None)
            close = getattr(http_client, "aclose", None)
            if close is not None:
                await close()

    async def close(self) -> None:
        return None
