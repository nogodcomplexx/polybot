"""
Multi-source news aggregator with deduplication.
"""

import logging
import hashlib
import time
from typing import List, Set

from src.config import BotConfig
from src.data.models import NewsArticle
from src.news.sources.newsapi import NewsAPISource
from src.news.sources.rss import RSSSource

logger = logging.getLogger(__name__)


class NewsIngestor:
    """Unified news aggregator across all sources."""

    def __init__(self, config: BotConfig):
        self.config = config
        self._seen_hashes: Set[str] = set()
        self._last_fetch: float = 0.0

        # Initialize sources
        self.newsapi = NewsAPISource(config.news.newsapi_key)
        self.rss = RSSSource(config.news.rss_feeds)

    async def close(self):
        await self.newsapi.close()
        await self.rss.close()

    async def fetch_news(self, queries: List[str] = None) -> List[NewsArticle]:
        """
        Fetch news from all sources, deduplicated.
        
        Args:
            queries: Optional search queries (e.g., market questions).
                     If None, fetches general headlines.
        """
        all_articles: List[NewsArticle] = []

        # 1. RSS feeds (always)
        rss_articles = await self.rss.fetch_all()
        all_articles.extend(rss_articles)

        # 2. NewsAPI
        if queries:
            for query in queries[:10]:  # Limit queries to save API calls
                if self.newsapi._is_rate_limited():
                    logger.warning("NewsAPI is cooling down, skipping remaining queries this cycle.")
                    break
                articles = await self.newsapi.search(query, page_size=10)
                all_articles.extend(articles)
        else:
            headlines = await self.newsapi.get_top_headlines(page_size=20)
            all_articles.extend(headlines)

        # 3. Deduplicate
        unique_articles = self._deduplicate(all_articles)

        # 4. Trim to max
        max_articles = self.config.news.max_articles_per_cycle
        if len(unique_articles) > max_articles:
            unique_articles = unique_articles[:max_articles]

        self._last_fetch = time.time()
        logger.info(
            f"News ingestion: {len(all_articles)} raw → {len(unique_articles)} unique articles"
        )
        return unique_articles

    def _deduplicate(self, articles: List[NewsArticle]) -> List[NewsArticle]:
        """Remove duplicate articles based on content hash."""
        unique = []
        for article in articles:
            h = article.content_hash
            if not h:
                # Generate hash if missing
                hash_input = f"{article.title}{article.description}".lower().strip()
                h = hashlib.md5(hash_input.encode()).hexdigest()
                article.content_hash = h

            if h not in self._seen_hashes:
                self._seen_hashes.add(h)
                unique.append(article)
        return unique

    def clear_seen(self):
        """Reset dedup cache (call periodically, e.g. daily)."""
        self._seen_hashes.clear()
