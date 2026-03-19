"""
News-to-market mapper — links news articles to Polymarket markets.
"""

import logging
import re
from typing import List, Dict, Tuple

from src.data.models import Market, NewsArticle, MarketSignal, SentimentResult

logger = logging.getLogger(__name__)


class NewsMapper:
    """Map news articles to relevant Polymarket markets."""

    def __init__(self):
        # Dynamically built keyword index from market questions
        self._market_keywords: Dict[str, List[str]] = {}

    def build_index(self, markets: List[Market]):
        """Build a keyword index from market questions for matching."""
        self._market_keywords.clear()
        for market in markets:
            keywords = self._extract_keywords(market.question)
            # Also include category keywords
            if market.category:
                keywords.extend(self._extract_keywords(market.category))
            self._market_keywords[market.condition_id] = keywords

    def match(
        self,
        articles: List[NewsArticle],
        markets: List[Market],
        sentiments: List[SentimentResult],
    ) -> List[MarketSignal]:
        """
        Match news articles to markets based on keyword overlap.
        Returns MarketSignal objects linking articles + sentiment to markets.
        """
        if not self._market_keywords:
            self.build_index(markets)

        market_map = {m.condition_id: m for m in markets}
        signals: Dict[str, MarketSignal] = {}

        for article, sentiment in zip(articles, sentiments):
            article_words = set(self._extract_keywords(
                f"{article.title} {article.description}"
            ))

            for cid, market_keywords in self._market_keywords.items():
                overlap = article_words & set(market_keywords)
                relevance = len(overlap) / max(len(market_keywords), 1)

                if relevance >= 0.15:  # At least 15% keyword overlap
                    if cid not in signals:
                        signals[cid] = MarketSignal(
                            market=market_map[cid],
                            articles=[],
                            sentiment=None,
                            relevance_score=0.0,
                        )
                    signals[cid].articles.append(article)
                    # Update relevance as max of all matches
                    signals[cid].relevance_score = max(
                        signals[cid].relevance_score, relevance
                    )

        # Attach aggregated sentiment to each signal
        for cid, signal in signals.items():
            matching_sentiments = []
            for article, sentiment in zip(articles, sentiments):
                if article in signal.articles:
                    matching_sentiments.append(sentiment)
            if matching_sentiments:
                # Use the sentiment analyzer's aggregate (import here to avoid circular)
                total_weight = sum(s.confidence for s in matching_sentiments) or 1.0
                avg_score = sum(s.score * s.confidence for s in matching_sentiments) / total_weight
                avg_conf = sum(s.confidence for s in matching_sentiments) / len(matching_sentiments)
                signal.sentiment = SentimentResult(
                    text=f"Aggregated from {len(matching_sentiments)} articles",
                    score=avg_score,
                    confidence=avg_conf,
                    label="positive" if avg_score > 0.1 else ("negative" if avg_score < -0.1 else "neutral"),
                    model_used="aggregated",
                )

        result = sorted(signals.values(), key=lambda s: s.relevance_score, reverse=True)
        logger.info(f"Mapped {len(articles)} articles → {len(result)} market signals")
        return result

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract meaningful keywords from text."""
        # Remove common stop words and extract meaningful tokens
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "will", "would", "could", "should", "may", "might", "can",
            "do", "does", "did", "has", "have", "had", "to", "of", "in",
            "for", "on", "with", "at", "by", "from", "as", "into", "about",
            "that", "this", "it", "its", "or", "and", "but", "not", "no",
            "if", "than", "what", "when", "where", "who", "which", "how",
            "their", "they", "them", "he", "she", "his", "her", "we",
            "our", "your", "more", "most", "some", "any", "all", "each",
            "many", "much", "very", "too", "also", "just", "then", "so",
            "up", "out", "over", "after", "before", "between", "under",
        }

        # Tokenize: split on non-alphanumeric, lowercase
        words = re.findall(r"[a-zA-Z0-9]+", text.lower())
        # Filter stop words and short tokens
        keywords = [w for w in words if w not in stop_words and len(w) > 2]
        return keywords
