"""
High-probability "bond" strategy — buy near-certain outcomes for small guaranteed returns.
"""

import logging
from typing import List

from src.config import StrategyHighProbConfig
from src.data.models import (
    Market, MarketSignal, PredictionResult, TradeSignal,
    Side, OrderType, Conviction,
)
from src.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class HighProbabilityStrategy(BaseStrategy):
    """
    Buy outcomes priced 92-98 cents that are very likely to resolve in our favor.
    Effectively "lending" money at a small interest rate until resolution.
    """

    def __init__(self, config: StrategyHighProbConfig):
        self.config = config

    @property
    def name(self) -> str:
        return "high_probability"

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def evaluate(
        self,
        markets: List[Market],
        predictions: List[PredictionResult],
        signals: List[MarketSignal],
        budget_usd: float,
    ) -> List[TradeSignal]:
        """Find near-certain outcomes priced below $1.00."""
        trades = []
        min_price = self.config.min_price_cents / 100.0
        max_price = self.config.max_price_cents / 100.0

        for market in markets:
            # Check YES side
            if min_price <= market.yes_price <= max_price:
                return_pct = ((1.0 / market.yes_price) - 1.0) * 100
                trades.append(self._create_signal(
                    market, market.yes_token_id, market.yes_price,
                    "YES", return_pct, budget_usd,
                ))

            # Check NO side
            if min_price <= market.no_price <= max_price:
                return_pct = ((1.0 / market.no_price) - 1.0) * 100
                trades.append(self._create_signal(
                    market, market.no_token_id, market.no_price,
                    "NO", return_pct, budget_usd,
                ))

        if trades:
            logger.info(f"High-prob: found {len(trades)} near-certain outcomes")
        return trades

    def _create_signal(
        self,
        market: Market,
        token_id: str,
        price: float,
        direction: str,
        return_pct: float,
        budget_usd: float,
    ) -> TradeSignal:
        size_usd = budget_usd * 0.10  # 10% per high-prob trade

        return TradeSignal(
            strategy_name=self.name,
            market=market,
            side=Side.BUY,
            token_id=token_id,
            target_price=price,
            size_usd=size_usd,
            edge=(0.99 - price),
            conviction=Conviction.HIGH,
            confidence=0.99,  # We strongly believe these high-prob bounds will resolve YES
            stop_loss_price=price * 0.90,  # Tight 10% stop (shouldn't trigger)
            take_profit_price=0.99,
            reasoning=(
                f"High-prob bond: {direction} @ {price:.3f} → "
                f"{return_pct:.1f}% return on resolution"
            ),
            order_type=OrderType.GTC,
        )
