"""
Market discovery — scan, filter, and categorize active Polymarket markets.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import List

from src.config import DiscoveryConfig
from src.data.models import Market

logger = logging.getLogger(__name__)


class MarketDiscovery:
    """Discover and filter tradable markets from Gamma API."""

    def __init__(self, config: DiscoveryConfig):
        self.config = config

    def filter_markets(self, markets: List[Market]) -> List[Market]:
        """Apply all filters to find tradable markets."""
        filtered = []
        for m in markets:
            if not self._passes_filters(m):
                continue
            filtered.append(m)

        logger.info(
            f"Discovery: {len(markets)} total → {len(filtered)} pass filters"
        )
        return filtered

    def filter_same_day(self, markets: List[Market]) -> List[Market]:
        """
        Filter markets that resolve TODAY only.
        No multi-week bets — only same-day events.
        """
        now = datetime.now(timezone.utc)
        today_end = now.replace(hour=23, minute=59, second=59)
        result = []
        for m in markets:
            if not m.end_date:
                continue
            try:
                end_dt = datetime.fromisoformat(m.end_date.replace("Z", "+00:00"))
                if end_dt.date() == now.date():
                    result.append(m)
            except (ValueError, TypeError):
                continue

        logger.info(f"Same-day filter: {len(markets)} → {len(result)} markets ending today")
        return result

    def filter_crypto_5min(self, markets: List[Market]) -> List[Market]:
        """
        Find 5-minute crypto binary options.
        These are Polymarket's "BTC Up or Down" markets that resolve in 5 minutes.
        """
        crypto_kw = ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto"]
        time_kw = ["5 min", "5-min", "five min", "up or down"]
        result = []

        for m in markets:
            if not m.active or m.closed:
                continue
            q = m.question.lower()
            is_crypto = any(kw in q for kw in crypto_kw)
            is_5min = any(kw in q for kw in time_kw)
            if is_crypto and is_5min:
                result.append(m)
            elif is_crypto and self._is_same_day(m):
                result.append(m)

        logger.info(f"Crypto 5-min filter: {len(markets)} → {len(result)} crypto markets")
        return result

    def _is_same_day(self, m: Market) -> bool:
        """Check if a market ends today."""
        if not m.end_date:
            return False
        try:
            end = datetime.fromisoformat(m.end_date.replace("Z", "+00:00"))
            return end.date() == datetime.now(timezone.utc).date()
        except Exception:
            return False

    def _passes_filters(self, m: Market) -> bool:
        """Check if a market passes all discovery filters."""
        # Must be active and not closed
        if not m.active or m.closed:
            return False

        # Must have valid token IDs
        if not m.yes_token_id or not m.no_token_id:
            return False

        # Special Case: 5-minute crypto markets bypass volume and date filters
        # because they are high-velocity and resolve almost immediately.
        if self._is_5min_crypto(m):
            # Still require basic market quality so we don't trade dead/toxic markets.
            if m.yes_price <= 0.01 or m.yes_price >= 0.99:
                return False
            if m.liquidity and m.liquidity < 250:
                return False
            # NOTE: 5-min markets often show volume_24h=0 even when tradable,
            # so we intentionally do NOT filter on volume_24h here.
            return True

        # Liquidity filter
        if m.liquidity < self.config.min_liquidity_usd:
            return False

        # Volume filter
        if m.volume_24h < self.config.min_volume_24h:
            return False

        # Time-to-resolution filter (Strict Same-Day Requirement)
        if not m.end_date:
            return False
            
        try:
            end_dt = datetime.fromisoformat(m.end_date.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            
            # Require the market to resolve TODAY
            if end_dt.date() != now.date():
                return False
                
        except (ValueError, TypeError):
            # If we can't parse the date exactly, exclude the market to be safe
            return False

        # Category filter (if specified)
        if self.config.categories:
            market_cat = (m.category or "").lower()
            if not any(c.lower() in market_cat for c in self.config.categories):
                return False

        # Must have reasonable prices (not 0 or 1)
        if m.yes_price <= 0.01 or m.yes_price >= 0.99:
            return False

        return True

    def _is_5min_crypto(self, m: Market) -> bool:
        """Helper to identify 5-minute crypto markets."""
        crypto_kw = ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto"]
        time_kw = ["5 min", "5-min", "five min", "up or down"]
        q = m.question.lower()
        return any(kw in q for kw in crypto_kw) and any(kw in q for kw in time_kw)

    def get_search_queries(self, markets: List[Market]) -> List[str]:
        """
        Extract key search terms from market questions for news queries.
        Selects the most interesting markets for news search.
        """
        queries = []
        for m in markets[:20]:  # Top 20 markets by volume
            # Extract the core question, removing common prefixes
            q = m.question
            for prefix in ["Will ", "Is ", "Does ", "Can ", "Has "]:
                if q.startswith(prefix):
                    q = q[len(prefix):]
                    break
            # Take first 60 chars for search query
            queries.append(q[:60].strip("?").strip())
        return queries
