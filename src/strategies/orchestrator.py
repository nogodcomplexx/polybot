"""
Strategy orchestrator — runs all strategies, deduplicates, and selects the best trades.
"""

import logging
from typing import List, Optional

from src.config import StrategiesConfig, RiskConfig
from src.data.models import Market, MarketSignal, PredictionResult, TradeSignal
from src.strategies.base import BaseStrategy
from src.strategies.arbitrage import ArbitrageStrategy
from src.strategies.news_driven import NewsDrivenStrategy
from src.strategies.high_prob import HighProbabilityStrategy
from src.strategies.contrarian import ContrarianStrategy
from src.strategies.crypto_scalper import CryptoScalperStrategy

logger = logging.getLogger(__name__)


class StrategyOrchestrator:
    """
    Run all enabled strategies, deduplicate, and rank the results.
    """

    def __init__(self, strategies_cfg: StrategiesConfig, risk_cfg: RiskConfig,
                 crypto_data_feed=None, budget: float = 10.0, clob=None):
        self.strategies: List[BaseStrategy] = []

        if strategies_cfg.arbitrage.enabled:
            self.strategies.append(ArbitrageStrategy(strategies_cfg.arbitrage))
        if strategies_cfg.news_driven.enabled:
            self.strategies.append(NewsDrivenStrategy(strategies_cfg.news_driven))
        if strategies_cfg.high_probability.enabled:
            self.strategies.append(HighProbabilityStrategy(strategies_cfg.high_probability))
        if strategies_cfg.contrarian.enabled:
            self.strategies.append(ContrarianStrategy(strategies_cfg.contrarian))

        # Crypto scalper — always enabled if data feed is available
        self.crypto_scalper: Optional[CryptoScalperStrategy] = None
        if crypto_data_feed:
            self.crypto_scalper = CryptoScalperStrategy(crypto_data_feed, budget, clob)
            self.strategies.append(self.crypto_scalper)
            logger.info("🎰 CryptoScalper strategy registered")

        self.risk_cfg = risk_cfg
        logger.info(f"Orchestrator loaded {len(self.strategies)} strategies: "
                     f"{[s.name for s in self.strategies]}")

    def evaluate_all(
        self,
        markets: List[Market],
        predictions: List[PredictionResult],
        signals: List[MarketSignal],
        budget_usd: float,
    ) -> List[TradeSignal]:
        """Run all strategies and return deduplicated, ranked trade signals."""
        all_signals: List[TradeSignal] = []

        for strategy in self.strategies:
            try:
                results = strategy.evaluate(markets, predictions, signals, budget_usd)
                all_signals.extend(results)
                if results:
                    logger.info(f"Strategy '{strategy.name}' produced {len(results)} signals")
            except Exception as e:
                logger.error(f"Strategy '{strategy.name}' failed: {e}")

        # Deduplicate by market (keep best signal per market)
        deduped = self._deduplicate(all_signals)

        # Rank by risk-adjusted expected value
        ranked = sorted(deduped, key=self._rank_score, reverse=True)

        # Enforce total budget constraint
        constrained = self._enforce_budget(ranked, budget_usd)

        logger.info(
            f"Orchestrator: {len(all_signals)} raw → {len(deduped)} deduped → "
            f"{len(constrained)} within budget"
        )
        return constrained

    def _deduplicate(self, signals: List[TradeSignal]) -> List[TradeSignal]:
        """Keep only the highest-conviction signal per market+direction."""
        best: dict = {}
        for signal in signals:
            key = f"{signal.market.condition_id}_{signal.token_id}"
            if key not in best or self._rank_score(signal) > self._rank_score(best[key]):
                best[key] = signal
        return list(best.values())

    def _rank_score(self, signal: TradeSignal) -> float:
        """Compute ranking score for a trade signal."""
        edge_score = abs(signal.edge) * 100  # 0-50
        conviction_bonus = {
            "HIGH": 1.5, "MEDIUM": 1.0, "LOW": 0.5
        }.get(signal.conviction.value, 0.5)

        # Crypto scalper and arbitrage get priority
        strategy_bonus = {
            "arbitrage": 2.0,
            "crypto_scalper": 1.8,  # High priority for 5-min trades
        }.get(signal.strategy_name, 1.0)

        return edge_score * conviction_bonus * strategy_bonus * signal.confidence

    def _enforce_budget(
        self, signals: List[TradeSignal], budget_usd: float
    ) -> List[TradeSignal]:
        """Ensure total signal size doesn't exceed available budget."""
        result = []
        remaining = budget_usd * (1 - self.risk_cfg.cash_buffer_pct / 100)

        for signal in signals:
            if signal.size_usd <= remaining:
                result.append(signal)
                remaining -= signal.size_usd
            elif remaining > 0.5:  # At least $0.50 left
                signal.size_usd = remaining
                result.append(signal)
                remaining = 0
            else:
                break

        return result
