"""
Contrarian strategy — fade emotionally-driven price spikes.
"""

import logging
from typing import List, Dict

from src.config import StrategyContrarianConfig
from src.data.models import (
    Market, MarketSignal, PredictionResult, TradeSignal,
    Side, OrderType, Conviction,
)
from src.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class ContrarianStrategy(BaseStrategy):
    """
    Detect price spikes not backed by proportional news, then
    bet on mean reversion.
    """

    def __init__(self, config: StrategyContrarianConfig):
        self.config = config

    @property
    def name(self) -> str:
        return "contrarian"

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
        """Find overreactions — price moved significantly without matching news."""
        signal_map: Dict[str, MarketSignal] = {
            s.market.condition_id: s for s in signals
        }
        pred_map: Dict[str, PredictionResult] = {
            p.market.condition_id: p for p in predictions
        }

        trades = []

        for pred in predictions:
            market = pred.market
            cid = market.condition_id
            signal = signal_map.get(cid)

            abs_edge_pct = abs(pred.edge) * 100

            # We want markets where the MARKET price has moved far from
            # what our model thinks, AND the news doesn't support it
            if abs_edge_pct < self.config.spike_threshold_pct:
                continue

            # Check if news supports the current price
            news_supports_price = False
            if signal and signal.sentiment:
                # If sentiment agrees with price direction, skip (not a contrarian trade)
                if signal.sentiment.score > 0.3 and market.yes_price > 0.6:
                    news_supports_price = True
                if signal.sentiment.score < -0.3 and market.yes_price < 0.4:
                    news_supports_price = True

            if news_supports_price:
                continue  # News backs the price → not an overreaction

            # This looks like an overreaction — bet on mean reversion
            if pred.edge > 0:
                # YES is underpriced (overreaction to NO) → buy YES
                side = Side.BUY
                token_id = market.yes_token_id
                price = market.yes_price
            else:
                # NO is underpriced (overreaction to YES) → buy NO
                side = Side.BUY
                token_id = market.no_token_id
                price = market.no_price

            size_usd = budget_usd * 0.05  # Small positions for contrarian

            trades.append(TradeSignal(
                strategy_name=self.name,
                market=market,
                side=side,
                token_id=token_id,
                target_price=price,
                size_usd=size_usd,
                edge=pred.edge,
                conviction=Conviction.LOW,  # Contrarian = lower conviction
                confidence=pred.confidence * 0.7,  # Discount confidence
                stop_loss_price=price * 0.70,  # Wider stop for mean-reversion
                take_profit_price=min(price * 1.15, 0.98),
                reasoning=(
                    f"Contrarian: price spike without supporting news, "
                    f"edge={pred.edge*100:+.1f}%, expecting mean reversion"
                ),
                order_type=OrderType.GTC,
            ))

            logger.info(
                f"Contrarian: {market.question[:50]} — "
                f"overreaction detected, edge={pred.edge*100:+.1f}%"
            )

        return trades
