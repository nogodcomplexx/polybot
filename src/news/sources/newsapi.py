"""
NewsAPI source — fetches breaking news by keyword.
"""

import logging
from typing import List, Optional

import httpx

from src.data.models import NewsArticle

logger = logging.getLogger(__name__)

NEWSAPI_BASE = "https://newsapi.org/v2"


class NewsAPISource:
    """Fetch news from NewsAPI.org."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=20.0)
        self._backoff_until: float = 0.0

    async def close(self):
        await self._client.aclose()

    def _is_rate_limited(self) -> bool:
        """Check if we are currently in a backoff period."""
        import time
        return time.time() < self._backoff_until

    async def search(
        self,
        query: str,
        language: str = "en",
        sort_by: str = "publishedAt",
        page_size: int = 20,
    ) -> List[NewsArticle]:
        """Search for news articles matching a query."""
        if not self.api_key:
            logger.warning("NewsAPI key not configured, skipping")
            return []

        if self._is_rate_limited():
            import time
            remaining = int(self._backoff_until - time.time())
            logger.warning(f"NewsAPI: Rate limited. Skipping search for '{query}' (recovers in {remaining}s)")
            return []

        try:
            resp = await self._client.get(
                f"{NEWSAPI_BASE}/everything",
                params={
                    "q": query,
                    "language": language,
                    "sortBy": sort_by,
                    "pageSize": page_size,
                    "apiKey": self.api_key,
                },
            )
            
            if resp.status_code == 429:
                import time
                self._backoff_until = time.time() + 3600  # 1 hour cooldown
                logger.error("NewsAPI: Received 429 Too Many Requests. Cooling down for 1 hour.")
                return []

            resp.raise_for_status()
            data = resp.json()

            articles = []
            for a in data.get("articles", []):
                articles.append(NewsArticle(
                    title=a.get("title") or "",
                    description=a.get("description") or "",
                    content=a.get("content") or "",
                    source=a.get("source", {}).get("name") or "",
                    url=a.get("url") or "",
                    published_at=a.get("publishedAt") or "",
                ))
            logger.info(f"NewsAPI: {len(articles)} articles for '{query}'")
            return articles

        except httpx.HTTPStatusError as e:
            logger.error(f"NewsAPI HTTP error: {e}")
            return []
        except Exception as e:
            logger.error(f"NewsAPI unexpected error: {e}")
            return []

    async def get_top_headlines(
        self,
        category: Optional[str] = None,
        country: str = "us",
        page_size: int = 20,
    ) -> List[NewsArticle]:
        """Fetch top headlines."""
        if not self.api_key:
            return []

        if self._is_rate_limited():
            logger.warning("NewsAPI: Rate limited. Skipping top headlines.")
            return []

        try:
            params = {
                "country": country,
                "pageSize": page_size,
                "apiKey": self.api_key,
            }
            if category:
                params["category"] = category

            resp = await self._client.get(f"{NEWSAPI_BASE}/top-headlines", params=params)
            
            if resp.status_code == 429:
                import time
                self._backoff_until = time.time() + 3600
                logger.error("NewsAPI: Received 429 Too Many Requests. Cooling down for 1 hour.")
                return []

            resp.raise_for_status()
            data = resp.json()

            articles = []
            for a in data.get("articles", []):
                articles.append(NewsArticle(
                    title=a.get("title") or "",
                    description=a.get("description") or "",
                    content=a.get("content") or "",
                    source=a.get("source", {}).get("name") or "",
                    url=a.get("url") or "",
                    published_at=a.get("publishedAt") or "",
                ))
            return articles

        except Exception as e:
            logger.error(f"NewsAPI headlines error: {e}")
            return []
