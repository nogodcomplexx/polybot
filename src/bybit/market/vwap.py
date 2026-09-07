"""
Volume-Weighted Average Price (VWAP) Engine.
Calculates anchored and rolling VWAP across 1m, 5m, 15m, and 24h with standard deviation bands.
"""

import math
from typing import List, Tuple
from src.bybit.market.candles import Candle


def calculate_vwap(candles: List[Candle]) -> float:
    """Calculate volume-weighted average price."""
    if not candles:
        return 0.0

    total_volume = sum(c.volume for c in candles)
    if total_volume <= 0:
        return candles[-1].close if candles else 0.0

    total_pv = sum(((c.high + c.low + c.close) / 3.0) * c.volume for c in candles)
    return total_pv / total_volume


def calculate_vwap_bands(candles: List[Candle], num_std: float = 2.0) -> Tuple[float, float, float]:
    """Calculate VWAP along with upper and lower standard deviation bands."""
    vwap = calculate_vwap(candles)
    if vwap <= 0 or not candles:
        return 0.0, 0.0, 0.0

    total_vol = sum(c.volume for c in candles)
    if total_vol <= 0:
        return vwap, vwap, vwap

    variance = sum(
        c.volume * (((c.high + c.low + c.close) / 3.0 - vwap) ** 2)
        for c in candles
    ) / total_vol

    std_dev = math.sqrt(variance)
    return vwap, vwap + (num_std * std_dev), vwap - (num_std * std_dev)
