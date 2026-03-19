"""
Technical indicators for crypto trading — RSI, EMA, MACD, Bollinger, ATR, VWAP.
All calculations use pure Python (no pandas/numpy dependency).
"""

import math
import logging
from dataclasses import dataclass
from typing import List, Optional

from src.crypto.data_feed import Candle

logger = logging.getLogger(__name__)


@dataclass
class TechnicalSignal:
    """Combined technical analysis result."""
    # Individual indicators
    rsi: float = 50.0               # 0-100
    ema_fast: float = 0.0           # EMA(9)
    ema_slow: float = 0.0           # EMA(21)
    ema_trend: str = "neutral"      # "bullish" / "bearish" / "neutral"
    macd_line: float = 0.0
    macd_signal: float = 0.0
    macd_histogram: float = 0.0
    bollinger_upper: float = 0.0
    bollinger_middle: float = 0.0
    bollinger_lower: float = 0.0
    bollinger_pct_b: float = 0.5    # 0-1 position within bands
    atr: float = 0.0               # Average True Range
    vwap: float = 0.0
    vwap_deviation: float = 0.0     # % above/below VWAP
    avg_volume: float = 0.0
    current_volume: float = 0.0
    volume_ratio: float = 1.0       # current / average

    # Consensus
    bullish_count: int = 0          # How many indicators are bullish
    bearish_count: int = 0
    consensus_direction: str = "neutral"  # "bullish" / "bearish" / "neutral"
    consensus_strength: float = 0.0       # 0.0 to 1.0


def compute_indicators(candles: List[Candle]) -> Optional[TechnicalSignal]:
    """Calculate all technical indicators from 5-min candles."""
    if len(candles) < 26:  # Need at least 26 candles for MACD
        logger.warning(f"Not enough candles ({len(candles)}) for indicators")
        return None

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    volumes = [c.volume for c in candles]

    sig = TechnicalSignal()
    bullish = 0
    bearish = 0
    total_indicators = 7

    # 1. RSI(14)
    sig.rsi = _rsi(closes, 14)
    if sig.rsi < 30:
        bullish += 1  # Oversold → expect bounce
    elif sig.rsi > 70:
        bearish += 1  # Overbought → expect pullback

    # 2. EMA(9) vs EMA(21)
    sig.ema_fast = _ema(closes, 9)
    sig.ema_slow = _ema(closes, 21)
    if sig.ema_fast > sig.ema_slow * 1.0005:  # Small threshold
        sig.ema_trend = "bullish"
        bullish += 1
    elif sig.ema_fast < sig.ema_slow * 0.9995:
        sig.ema_trend = "bearish"
        bearish += 1
    else:
        sig.ema_trend = "neutral"

    # 3. MACD (12, 26, 9)
    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    macd_line_series = [a - b for a, b in zip(ema12[-len(ema26):], ema26)]
    if len(macd_line_series) >= 9:
        signal_series = _ema_series(macd_line_series, 9)
        sig.macd_line = macd_line_series[-1]
        sig.macd_signal = signal_series[-1]
        sig.macd_histogram = sig.macd_line - sig.macd_signal

        if sig.macd_histogram > 0:
            bullish += 1
        elif sig.macd_histogram < 0:
            bearish += 1

    # 4. Bollinger Bands (20, 2)
    period = 20
    if len(closes) >= period:
        sma = sum(closes[-period:]) / period
        variance = sum((c - sma) ** 2 for c in closes[-period:]) / period
        std = math.sqrt(variance)
        sig.bollinger_upper = sma + 2 * std
        sig.bollinger_middle = sma
        sig.bollinger_lower = sma - 2 * std

        band_width = sig.bollinger_upper - sig.bollinger_lower
        if band_width > 0:
            sig.bollinger_pct_b = (closes[-1] - sig.bollinger_lower) / band_width
        else:
            sig.bollinger_pct_b = 0.5

        # Price near lower band → bullish, near upper → bearish
        if sig.bollinger_pct_b < 0.2:
            bullish += 1
        elif sig.bollinger_pct_b > 0.8:
            bearish += 1

    # 5. ATR(14)
    sig.atr = _atr(highs, lows, closes, 14)

    # 6. Volume Profile
    if len(volumes) >= 20:
        sig.avg_volume = sum(volumes[-20:]) / 20
        sig.current_volume = volumes[-1]
        sig.volume_ratio = volumes[-1] / sig.avg_volume if sig.avg_volume > 0 else 1.0

        # High volume confirms the move
        if sig.volume_ratio > 1.2:
            # Volume confirms the direction of the last candle
            if closes[-1] > closes[-2]:
                bullish += 1
            else:
                bearish += 1
        # Low volume → don't count as either

    # 7. VWAP
    sig.vwap = _vwap(candles[-20:])
    if sig.vwap > 0:
        sig.vwap_deviation = (closes[-1] - sig.vwap) / sig.vwap * 100

        if closes[-1] > sig.vwap * 1.001:
            bullish += 1  # Price above VWAP → institutional buying
        elif closes[-1] < sig.vwap * 0.999:
            bearish += 1

    # Consensus
    sig.bullish_count = bullish
    sig.bearish_count = bearish

    if bullish >= 4:
        sig.consensus_direction = "bullish"
        sig.consensus_strength = bullish / total_indicators
    elif bearish >= 4:
        sig.consensus_direction = "bearish"
        sig.consensus_strength = bearish / total_indicators
    else:
        sig.consensus_direction = "neutral"
        sig.consensus_strength = max(bullish, bearish) / total_indicators

    return sig


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def _rsi(closes: List[float], period: int = 14) -> float:
    """Calculate RSI."""
    if len(closes) < period + 1:
        return 50.0

    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    # Use exponential smoothing (Wilder's method)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _ema(values: List[float], period: int) -> float:
    """Calculate single EMA value."""
    series = _ema_series(values, period)
    return series[-1] if series else 0.0


def _ema_series(values: List[float], period: int) -> List[float]:
    """Calculate EMA series."""
    if len(values) < period:
        return values[:]

    multiplier = 2 / (period + 1)
    ema = [sum(values[:period]) / period]

    for i in range(period, len(values)):
        ema.append((values[i] - ema[-1]) * multiplier + ema[-1])

    return ema


def _atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    """Calculate Average True Range."""
    if len(highs) < period + 1:
        return 0.0

    true_ranges = []
    for i in range(1, len(highs)):
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


def _vwap(candles: List[Candle]) -> float:
    """Calculate Volume Weighted Average Price."""
    total_volume = sum(c.volume for c in candles)
    if total_volume == 0:
        return 0.0

    total_pv = sum(
        ((c.high + c.low + c.close) / 3) * c.volume
        for c in candles
    )
    return total_pv / total_volume
