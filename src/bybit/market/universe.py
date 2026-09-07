"""
Dynamic Universe Discovery & Rotation Manager.
Continuously scans Bybit Linear Tickers to identify and rank the Top N most liquid,
active, and volatile perpetual instruments while preserving macro anchors and active trades.
"""

import logging
import time
from typing import Dict, Any, List, Optional, Tuple, Set

from src.bybit.config import UniverseConfig
from src.bybit.exchange.bybit_client import BybitClient

logger = logging.getLogger(__name__)


class DynamicUniverseManager:
    """Discovers, scores, filters, and rotates the perpetual trading universe."""

    def __init__(self, config: UniverseConfig, fallback_symbols: Optional[List[str]] = None):
        self.config = config
        self.fallback_symbols = [s.upper() for s in (fallback_symbols or ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT"])]
        self.active_universe: List[str] = list(self.fallback_symbols)
        self.last_rotation_time: float = 0.0
        self.symbol_metrics: Dict[str, Dict[str, Any]] = {}

    def should_rotate(self) -> bool:
        """Check if scheduled rotation interval has elapsed."""
        if self.config.mode != "dynamic":
            return False
        if self.last_rotation_time == 0.0:
            return True
        elapsed_hours = (time.time() - self.last_rotation_time) / 3600.0
        return elapsed_hours >= self.config.rotation_interval_hours

    async def discover_universe(self, client: BybitClient) -> List[str]:
        """
        Query Bybit V5 Linear Tickers, filter by liquidity, turnover, spread, and volatility,
        and return the ranked Top N perpetual universe.
        """
        if self.config.mode != "dynamic":
            logger.info(f"Using static universe ({len(self.fallback_symbols)} symbols): {', '.join(self.fallback_symbols)}")
            self.active_universe = list(self.fallback_symbols)
            return self.active_universe

        try:
            raw_tickers = await client.get_tickers(category="linear")
            if not raw_tickers:
                logger.warning("No tickers returned from Bybit API; keeping current universe.")
                return self.active_universe

            scored_candidates = []
            pinned_set = set(self.config.pinned_symbols)
            metrics_cache = {}

            for item in raw_tickers:
                symbol = item.get("symbol", "").upper()
                if not symbol.endswith("USDT"):
                    continue

                last_price = float(item.get("lastPrice", 0) or 0)
                bid1 = float(item.get("bid1Price", 0) or 0)
                ask1 = float(item.get("ask1Price", 0) or 0)
                turnover24h = float(item.get("turnover24h", 0) or 0)
                high24h = float(item.get("highPrice24h", 0) or 0)
                low24h = float(item.get("lowPrice24h", 0) or 0)

                if last_price <= 0 or bid1 <= 0 or ask1 <= 0:
                    continue

                # Spread %
                spread = ask1 - bid1
                mid = (ask1 + bid1) / 2.0
                spread_pct = (spread / mid) * 100 if mid > 0 else 999.0

                # 24h Volatility / Price Range %
                volatility_pct = ((high24h - low24h) / low24h * 100) if low24h > 0 else 0.0

                metrics_cache[symbol] = {
                    "last_price": last_price,
                    "turnover_usd": turnover24h,
                    "spread_pct": spread_pct,
                    "volatility_pct": volatility_pct,
                }

                # Macro pinned anchors bypass filters
                if symbol in pinned_set:
                    continue

                # Filter 1: 24h Turnover Floor ($50M default)
                if turnover24h < self.config.min_turnover_usd:
                    continue

                # Filter 2: Max Spread % (0.04% default)
                if spread_pct > self.config.max_spread_pct:
                    continue

                # Filter 3: Minimum 24h Volatility (2.5% default)
                if volatility_pct < self.config.min_volatility_pct:
                    continue

                # Institutional Liquidity & Volatility Score
                # Higher turnover + higher volatility + tighter spread = higher score
                score = (turnover24h / 1_000_000.0) * (volatility_pct / max(spread_pct, 0.005))
                scored_candidates.append((symbol, score, turnover24h, spread_pct, volatility_pct))

            self.symbol_metrics = metrics_cache

            # Sort candidate pool descending by composite score
            scored_candidates.sort(key=lambda x: x[1], reverse=True)

            # Assemble final universe: pinned macro anchors first, followed by top scorers
            selected = list(self.config.pinned_symbols)

            for sym, _, _, _, _ in scored_candidates:
                if len(selected) >= self.config.top_n:
                    break
                if sym not in selected:
                    selected.append(sym)

            if len(selected) < len(self.config.pinned_symbols):
                # Fallback safeguard
                selected = list(self.fallback_symbols)

            self.last_rotation_time = time.time()
            logger.info(
                f"🌟 [DYNAMIC UNIVERSE DISCOVERED] {len(selected)} symbols selected "
                f"(Pinned: {', '.join(self.config.pinned_symbols)} | Top Candidates: {', '.join(selected[len(self.config.pinned_symbols):10])}...)"
            )
            return selected

        except Exception as e:
            logger.error(f"Failed to discover dynamic universe: {e}", exc_info=True)
            return self.active_universe

    def compute_rotation_diff(
        self,
        new_universe: List[str],
        protected_symbol: Optional[str] = None
    ) -> Tuple[List[str], List[str], List[str]]:
        """
        Compute added and removed symbols, ensuring any currently active trade
        is strictly protected from being evicted.
        Returns (added_symbols, removed_symbols, finalized_universe).
        """
        final_universe = list(new_universe)

        # Protect active trade symbol from eviction
        if protected_symbol and protected_symbol not in final_universe:
            logger.info(f"🛡️ [ROTATION PROTECTION] Retaining active position symbol {protected_symbol} in universe")
            final_universe.append(protected_symbol)

        added = [s for s in final_universe if s not in self.active_universe]
        removed = [s for s in self.active_universe if s not in final_universe]

        # Double check protected symbol is not in removed
        if protected_symbol and protected_symbol in removed:
            removed.remove(protected_symbol)

        return added, removed, final_universe
