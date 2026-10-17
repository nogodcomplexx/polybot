"""
Technical Indicators & SMC Price Action Mechanics.
Implements WaveTrend (WT1/WT2), RSI, Fair Value Gap (FVG), and Swing/Liquidity Sweep (SFP).
All calculations are strictly deterministic and mathematically documented.
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple
from src.bybit.market.candles import Candle


@dataclass
class FVGZone:
    """Fair Value Gap structure."""
    fvg_type: str         # "BULLISH" or "BEARISH"
    upper: float
    lower: float
    midpoint: float
    timestamp: float
    mitigated: bool = False


@dataclass
class LiquidityZone:
    """Swing high / low liquidity pool."""
    zone_type: str        # "BSL" (Buy-Side Liquidity) or "SSL" (Sell-Side Liquidity)
    price: float
    timestamp: float
    strength: int = 1


def calculate_wavetrend(candles: List[Candle], n1: int = 10, n2: int = 21) -> Tuple[float, float, float, bool]:
    """
    Calculate WaveTrend Oscillator (WT1 and WT2).
    Returns (wt1, wt2, wt1_prev, is_curling).
    """
    if len(candles) < (n1 + n2 + 5):
        return 0.0, 0.0, 0.0, False

    # 1. Typical Price series: ap = (H + L + C) / 3
    ap = [(c.high + c.low + c.close) / 3.0 for c in candles]

    # 2. esa = EMA(ap, n1)
    esa = _ema_series(ap, n1)

    # 3. d = EMA(|ap - esa|, n1)
    diff = [abs(p - e) for p, e in zip(ap, esa)]
    d = _ema_series(diff, n1)

    # 4. ci = (ap - esa) / (0.015 * d)
    ci = []
    for p, e, dev in zip(ap, esa, d):
        val = (p - e) / (0.015 * dev) if dev > 0 else 0.0
        ci.append(val)

    # 5. wt1 = EMA(ci, n2)
    wt1_series = _ema_series(ci, n2)

    # 6. wt2 = SMA(wt1, 4)
    if len(wt1_series) < 4:
        return 0.0, 0.0, 0.0, False

    wt1 = wt1_series[-1]
    wt1_prev = wt1_series[-2]
    wt2 = sum(wt1_series[-4:]) / 4.0

    # Curling: oversold turning up OR overbought turning down
    curling_up = (wt1_prev < -50.0 and wt1 > wt1_prev)
    curling_down = (wt1_prev > 50.0 and wt1 < wt1_prev)
    is_curling = curling_up or curling_down

    return round(wt1, 2), round(wt2, 2), round(wt1_prev, 2), is_curling


def calculate_rsi(candles: List[Candle], period: int = 14) -> float:
    """Calculate RSI using Wilder's smoothing."""
    if len(candles) < period + 1:
        return 50.0

    closes = [c.close for c in candles]
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def detect_fvgs(candles: List[Candle], include_mitigated: bool = False) -> List[FVGZone]:
    """
    Detect Fair Value Gaps in 3-candle structures and track subsequent mitigation.
    When len(candles) >= 4, only forms FVGs on closed candles (up to candles[-2])
    and checks all subsequent candles (including current live candle[-1]) for full invalidation.
    """
    fvgs: List[FVGZone] = []
    if len(candles) < 3:
        return fvgs

    # Avoid forming an FVG using the currently unclosed developing bar if enough history exists
    end_idx = (len(candles) - 3) if len(candles) >= 4 else (len(candles) - 2)

    for i in range(end_idx):
        c1 = candles[i]
        c2 = candles[i + 1]
        c3 = candles[i + 2]

        # Bullish FVG: Low of candle 3 > High of candle 1
        if c3.low > c1.high * 1.0005:
            upper = c3.low
            lower = c1.high
            mid = (upper + lower) / 2.0
            # Mitigated/Invalidated if any subsequent candle after c3 trades below the bottom of the gap (lower)
            is_mitigated = any(sub.low < lower for sub in candles[i + 3:])
            zone = FVGZone(
                fvg_type="BULLISH",
                upper=upper,
                lower=lower,
                midpoint=mid,
                timestamp=c2.timestamp,
                mitigated=is_mitigated,
            )
            if include_mitigated or not is_mitigated:
                fvgs.append(zone)

        # Bearish FVG: High of candle 3 < Low of candle 1
        elif c3.high < c1.low * 0.9995:
            upper = c1.low
            lower = c3.high
            mid = (upper + lower) / 2.0
            # Mitigated/Invalidated if any subsequent candle after c3 trades above the top of the gap (upper)
            is_mitigated = any(sub.high > upper for sub in candles[i + 3:])
            zone = FVGZone(
                fvg_type="BEARISH",
                upper=upper,
                lower=lower,
                midpoint=mid,
                timestamp=c2.timestamp,
                mitigated=is_mitigated,
            )
            if include_mitigated or not is_mitigated:
                fvgs.append(zone)

    return fvgs


def detect_swings(candles: List[Candle], lookback: int = 5) -> Tuple[List[LiquidityZone], List[LiquidityZone]]:
    """Detect structural pivot swing highs (BSL) and swing lows (SSL)."""
    bsl = []
    ssl = []
    if len(candles) < (lookback * 2 + 1):
        return bsl, ssl

    for i in range(lookback, len(candles) - lookback):
        curr_high = candles[i].high
        curr_low = candles[i].low

        is_high = all(curr_high >= candles[j].high for j in range(i - lookback, i + lookback + 1) if j != i)
        is_low = all(curr_low <= candles[j].low for j in range(i - lookback, i + lookback + 1) if j != i)

        if is_high:
            bsl.append(LiquidityZone("BSL", curr_high, candles[i].timestamp))
        if is_low:
            ssl.append(LiquidityZone("SSL", curr_low, candles[i].timestamp))

    return bsl, ssl


def _ema_series(values: List[float], period: int) -> List[float]:
    if not values:
        return []
    if len(values) < period:
        return values[:]
    mult = 2.0 / (period + 1.0)
    ema = [sum(values[:period]) / period]
    for val in values[period:]:
        ema.append((val - ema[-1]) * mult + ema[-1])
    return [values[0]] * (len(values) - len(ema)) + ema
