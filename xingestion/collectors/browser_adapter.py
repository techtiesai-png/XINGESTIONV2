from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import quote_plus

from xingestion.collectors.base import (
    CollectionBatch,
    CollectionRequest,
    CollectorChanged,
)
from xingestion.control_plane import TokenLease


_STATUS_PATH = re.compile(r"/([^/]+)/status/(\d+)")


class PlaywrightSearchAdapter:
    name = "playwright_search_recovery"
    version = "1"
    requires_session = False

    def __init__(
        self,
        *,
        proxy: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.proxy = proxy
        self.timeout_seconds = timeout_seconds

    async def collect(
        self,
        request: CollectionRequest,
        *,
        session: TokenLease | None,
    ) -> CollectionBatch:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise CollectorChanged(
                "playwright recovery adapter is not installed"
            ) from exc

        target_url = (
            "https://x.com/search?q="
            f"{quote_plus(request.query)}&src=typed_query&f=live"
        )
        collected: list[dict[str, object]] = []

        try:
            async with async_playwright() as playwright:
                launch_args: dict[str, object] = {"headless": True}
                if self.proxy:
                    launch_args["proxy"] = {"server": self.proxy}
                browser = await playwright.chromium.launch(**launch_args)
                try:
                    context = await browser.new_context()
                    if session is not None:
                        try:
                            cookies = json.loads(session.token_value)
                            if isinstance(cookies, dict):
                                await context.add_cookies(
                                    [
                                        {
                                            "name": str(name),
                                            "value": str(value),
                                            "domain": ".x.com",
                                            "path": "/",
                                        }
                                        for name, value in cookies.items()
                                    ]
                                )
                        except Exception:
                            pass

                    page = await context.new_page()
                    await page.goto(
                        target_url,
                        wait_until="domcontentloaded",
                        timeout=int(self.timeout_seconds * 1000),
                    )
                    await page.wait_for_timeout(2_000)
                    articles = page.locator('article[data-testid="tweet"]')
                    count = min(await articles.count(), max(1, request.page_size))
                    for index in range(count):
                        article = articles.nth(index)
                        try:
                            text = await article.locator(
                                'div[data-testid="tweetText"]'
                            ).inner_text()
                        except Exception:
                            continue

                        tweet_id: str | None = None
                        handle = "unknown"
                        try:
                            href = await article.locator(
                                'a[href*="/status/"]'
                            ).first.get_attribute("href")
                            match = _STATUS_PATH.search(href or "")
                            if match:
                                handle, tweet_id = match.groups()
                        except Exception:
                            pass

                        if tweet_id is None:
                            digest = hashlib.sha256(
                                text.encode("utf-8")
                            ).hexdigest()[:24]
                            tweet_id = f"browser_{digest}"

                        collected.append(
                            {
                                "original_tweet_id": tweet_id,
                                "author_id": f"handle:{handle}",
                                "author_handle": handle,
                                "text_content": text,
                                "engagement_likes": 0,
                                "engagement_retweets": 0,
                                "conversation_id": tweet_id,
                                "sentiment_label": "NEUTRAL",
                            }
                        )
                finally:
                    await browser.close()
        except Exception as exc:
            raise CollectorChanged(
                f"browser recovery adapter failed: {exc}"
            ) from exc

        return CollectionBatch(
            items=collected,
            next_cursor=None,
            adapter_name=self.name,
            adapter_version=self.version,
            metadata={
                "query": request.query,
                "fidelity": "recovery_partial",
            },
        )

    async def close(self) -> None:
        return None
