"""
Arbitrage strategy — exploit YES + NO < $1.00 for risk-free profit.
"""

import logging
from typing import List

from src.config import StrategyArbitrageConfig
from src.data.models import (
    Market, MarketSignal, PredictionResult, TradeSignal,
    Side, OrderType, Conviction,
)
from src.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class ArbitrageStrategy(BaseStrategy):
    """
    Complement arbitrage: Buy both YES and NO when their combined
    price is less than $1.00, guaranteeing profit at resolution.
    """

    def __init__(self, config: StrategyArbitrageConfig):
        self.config = config

    @property
    def name(self) -> str:
        return "arbitrage"

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
        """Find arbitrage opportunities where YES + NO < $1.00."""
        trades = []

        for market in markets:
            if not market.yes_price or not market.no_price:
                continue

            combined = market.yes_price + market.no_price
            if combined >= 1.0:
                continue

            spread_profit = 1.0 - combined
            profit_pct = (spread_profit / combined) * 100

            # Must exceed min profit after fees (~2% Polymarket fee)
            net_profit_pct = profit_pct - 2.0
            if net_profit_pct < self.config.min_profit_pct:
                continue

            # Split budget to buy both sides
            half_budget = min(budget_usd * 0.4, budget_usd / 2)

            # Buy YES
            trades.append(TradeSignal(
                strategy_name=self.name,
                market=market,
                side=Side.BUY,
                token_id=market.yes_token_id,
                target_price=market.yes_price,
                size_usd=half_budget,
                edge=spread_profit,
                conviction=Conviction.HIGH,
                confidence=0.95,  # Near-certain profit
                reasoning=f"Arbitrage: YES({market.yes_price:.3f}) + NO({market.no_price:.3f}) = {combined:.3f} < $1.00 → {net_profit_pct:.1f}% profit",
                order_type=OrderType.FOK,  # Fill-or-kill to avoid partial
            ))

            # Buy NO
            trades.append(TradeSignal(
                strategy_name=self.name,
                market=market,
                side=Side.BUY,
                token_id=market.no_token_id,
                target_price=market.no_price,
                size_usd=half_budget,
                edge=spread_profit,
                conviction=Conviction.HIGH,
                confidence=0.95,
                reasoning=f"Arbitrage pair: NO side",
                order_type=OrderType.FOK,
            ))

            logger.info(
                f"Arbitrage found: {market.question[:50]} — "
                f"net profit: {net_profit_pct:.1f}%"
            )

        return trades
