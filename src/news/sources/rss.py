"""
RSS feed parser — fetches news from configurable RSS feeds.
"""

import logging
import hashlib
from typing import List

import feedparser
import httpx

from src.data.models import NewsArticle

logger = logging.getLogger(__name__)


class RSSSource:
    """Parse RSS feeds for news articles."""

    def __init__(self, feed_urls: List[str]):
        self.feed_urls = feed_urls
        self._client = httpx.AsyncClient(timeout=15.0)

    async def close(self):
        await self._client.aclose()

    async def fetch_all(self) -> List[NewsArticle]:
        """Fetch articles from all configured RSS feeds."""
        all_articles = []
        for url in self.feed_urls:
            articles = await self._fetch_feed(url)
            all_articles.extend(articles)
        logger.info(f"RSS: {len(all_articles)} articles from {len(self.feed_urls)} feeds")
        return all_articles

    async def _fetch_feed(self, url: str) -> List[NewsArticle]:
        """Fetch and parse a single RSS feed."""
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            feed = feedparser.parse(resp.text)

            articles = []
            for entry in feed.entries[:20]:  # Limit per feed
                title = entry.get("title", "")
                description = entry.get("summary", entry.get("description", ""))
                content = ""
                if hasattr(entry, "content") and entry.content:
                    content = entry.content[0].get("value", "")

                # Create content hash for deduplication
                hash_input = f"{title}{description}".lower().strip()
                content_hash = hashlib.md5(hash_input.encode()).hexdigest()

                articles.append(NewsArticle(
                    title=title,
                    description=description,
                    content=content,
                    source=feed.feed.get("title", url),
                    url=entry.get("link", ""),
                    published_at=entry.get("published", ""),
                    content_hash=content_hash,
                ))
            return articles

        except Exception as e:
            logger.warning(f"RSS feed error ({url}): {e}")
            return []
