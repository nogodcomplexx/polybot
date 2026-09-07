"""
Volatility Engine for Bybit Scalper.
Calculates deterministic ATR (1m, 5m, 15m), realized volatility, and volatility percentiles.
"""

import math
from typing import List
from src.bybit.market.candles import Candle


def calculate_atr(candles: List[Candle], period: int = 14) -> float:
    """Calculate Average True Range using Wilder's smoothing."""
    if len(candles) < period + 1:
        if candles:
            return sum(c.high - c.low for c in candles) / len(candles)
        return 0.0

    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]

    true_ranges = []
    for i in range(1, len(candles)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0

    # Wilder's smoothing
    atr = sum(true_ranges[:period]) / period
    for i in range(period, len(true_ranges)):
        atr = (atr * (period - 1) + true_ranges[i]) / period

    return atr


def calculate_realized_volatility(candles: List[Candle], lookback: int = 20) -> float:
    """Calculate realized volatility as standard deviation of close log returns."""
    if len(candles) < lookback + 1:
        return 0.0

    closes = [c.close for c in candles[-lookback - 1:]]
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]

    if not returns:
        return 0.0

    mean_ret = sum(returns) / len(returns)
    variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
    return math.sqrt(variance)
