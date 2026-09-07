"""
Alpha Strategies Suite for Bybit Linear Perpetual Scalper.
Implements:
1. Strategy 1: Turtle Soup / Liquidity Sweep
2. Strategy 2: VWAP / FVG Mean Reversion
3. Strategy 3: Orderbook Absorption
4. Strategy 4: Trend / FVG Continuation
"""

import time
import uuid
from typing import Dict, Any, List, Optional
from src.bybit.models import Signal, OrderSide, StrategyName, MarketRegime, MarketSnapshot
from src.bybit.market.candles import Candle
from src.bybit.alpha.indicators import (
    calculate_wavetrend, calculate_rsi, detect_fvgs, detect_swings
)


class AlphaStrategySuite:
    """Evaluates all 4 alpha strategies and returns candidate signals."""

    def __init__(self):
        pass

    def _calculate_ema(self, series, period):
        if len(series) < period:
            return series[-1] if series else 0.0
        mult = 2.0 / (period + 1.0)
        ema = sum(series[:period]) / period
        for val in series[period:]:
            ema = (val - ema) * mult + ema
        return ema

    # ------------------------------------------------------------------
    # Strategy 1: Turtle Soup / Liquidity Sweep (SFP)
    # ------------------------------------------------------------------

    def evaluate_turtle_soup(self, snapshot: MarketSnapshot, candles_15m: List[Candle]) -> Optional[Signal]:
        if len(candles_15m) < 25:
            return None

        current_price = snapshot.last_price
        atr = snapshot.atr_15m if snapshot.atr_15m > 0 else current_price * 0.005
        wt1, wt2, wt1_prev, is_curling = calculate_wavetrend(candles_15m)
        bsl_zones, ssl_zones = detect_swings(candles_15m, lookback=4)

        last_bar = candles_15m[-1]

        # --- LONG SETUP: Sweep SSL ---
        if ssl_zones:
            recent_ssl = ssl_zones[-1].price
            # 0.2% - 0.6% excursion below recent SSL
            if last_bar.low < recent_ssl and 0.20 <= (recent_ssl - last_bar.low) / recent_ssl * 100 <= 0.60:
                # Failure to close below SSL (SFP wick rejection)
                if last_bar.close >= recent_ssl and current_price >= recent_ssl:
                    # WaveTrend oversold & curling up
                    if wt1 < -45.0 and (wt1 >= wt1_prev):
                        # Orderflow confirmation (imbalance favorable or positive CVD divergence)
                        if snapshot.orderbook_imbalance >= 1.05:
                            stop_price = last_bar.low - (atr * 0.5)
                            stop_dist = current_price - stop_price
                            if stop_dist > 0:
                                tp1_price = current_price + (stop_dist * 1.6)
                                tp2_price = current_price + (stop_dist * 3.0)
                                return Signal(
                                    id=f"sig_{uuid.uuid4().hex[:8]}",
                                    timestamp=time.time(),
                                    symbol=snapshot.symbol,
                                    side=OrderSide.BUY,
                                    strategy=StrategyName.TURTLE_SOUP,
                                    regime=snapshot.regime,
                                    entry=current_price,
                                    structural_stop=stop_price,
                                    tp1=tp1_price,
                                    tp2=tp2_price,
                                    confidence=0.82,
                                    thesis_expiry=time.time() + 300.0,
                                    reasoning=f"SSL swept at {recent_ssl:.2f}, SFP wick rejected, WT1 ({wt1}) curling up",
                                    features={"sweep_depth_pct": round((recent_ssl - last_bar.low)/recent_ssl*100, 3)}
                                )

        # --- SHORT SETUP: Sweep BSL ---
        if bsl_zones:
            recent_bsl = bsl_zones[-1].price
            # 0.2% - 0.6% excursion above recent BSL
            if last_bar.high > recent_bsl and 0.20 <= (last_bar.high - recent_bsl) / recent_bsl * 100 <= 0.60:
                # Failure to close above BSL (SFP wick rejection)
                if last_bar.close <= recent_bsl and current_price <= recent_bsl:
                    # WaveTrend overbought & curling down
                    if wt1 > 45.0 and (wt1 <= wt1_prev):
                        # Orderflow confirmation
                        if snapshot.orderbook_imbalance <= 0.95:
                            stop_price = last_bar.high + (atr * 0.5)
                            stop_dist = stop_price - current_price
                            if stop_dist > 0:
                                tp1_price = current_price - (stop_dist * 1.6)
                                tp2_price = current_price - (stop_dist * 3.0)
                                return Signal(
                                    id=f"sig_{uuid.uuid4().hex[:8]}",
                                    timestamp=time.time(),
                                    symbol=snapshot.symbol,
                                    side=OrderSide.SELL,
                                    strategy=StrategyName.TURTLE_SOUP,
                                    regime=snapshot.regime,
                                    entry=current_price,
                                    structural_stop=stop_price,
                                    tp1=tp1_price,
                                    tp2=tp2_price,
                                    confidence=0.82,
                                    thesis_expiry=time.time() + 300.0,
                                    reasoning=f"BSL swept at {recent_bsl:.2f}, SFP wick rejected, WT1 ({wt1}) curling down",
                                    features={"sweep_depth_pct": round((last_bar.high - recent_bsl)/recent_bsl*100, 3)}
                                )

        return None

    # ------------------------------------------------------------------
    # Strategy 2: VWAP / FVG Mean Reversion
    # ------------------------------------------------------------------

    def evaluate_vwap_fvg_reversion(self, snapshot: MarketSnapshot, candles_15m: List[Candle]) -> Optional[Signal]:
        if len(candles_15m) < 25:
            return None

        current_price = snapshot.last_price
        vwap = snapshot.vwap_15m if snapshot.vwap_15m > 0 else current_price
        rsi = calculate_rsi(candles_15m, 14)
        atr = snapshot.atr_15m if snapshot.atr_15m > 0 else current_price * 0.005
        fvgs = detect_fvgs(candles_15m)

        # Bullish Reversion: Price pulled back into bullish FVG near VWAP, RSI in reset 40-48
        bullish_fvgs = [f for f in fvgs if f.fvg_type == "BULLISH"]
        if bullish_fvgs and 38.0 <= rsi <= 50.0:
            active_fvg = bullish_fvgs[-1]
            if active_fvg.lower <= current_price <= active_fvg.upper:
                if abs(current_price - vwap) / current_price * 100 <= 0.80:
                    stop_price = active_fvg.lower - (atr * 0.4)
                    stop_dist = current_price - stop_price
                    if stop_dist > 0:
                        tp1_price = current_price + (stop_dist * 1.6)
                        tp2_price = current_price + (stop_dist * 3.0)
                        return Signal(
                            id=f"sig_{uuid.uuid4().hex[:8]}",
                            timestamp=time.time(),
                            symbol=snapshot.symbol,
                            side=OrderSide.BUY,
                            strategy=StrategyName.VWAP_FVG_REVERSION,
                            regime=snapshot.regime,
                            entry=current_price,
                            structural_stop=stop_price,
                            tp1=tp1_price,
                            tp2=tp2_price,
                            confidence=0.78,
                            thesis_expiry=time.time() + 360.0,
                            reasoning=f"Bullish FVG [{active_fvg.lower:.2f}-{active_fvg.upper:.2f}] confluence with VWAP, RSI={rsi}",
                        )

        # Bearish Reversion: Price pushed up into bearish FVG near VWAP, RSI in reset 52-60
        bearish_fvgs = [f for f in fvgs if f.fvg_type == "BEARISH"]
        if bearish_fvgs and 50.0 <= rsi <= 62.0:
            active_fvg = bearish_fvgs[-1]
            if active_fvg.upper <= current_price <= active_fvg.lower:
                if abs(current_price - vwap) / current_price * 100 <= 0.80:
                    stop_price = active_fvg.lower + (atr * 0.4)
                    stop_dist = stop_price - current_price
                    if stop_dist > 0:
                        tp1_price = current_price - (stop_dist * 1.6)
                        tp2_price = current_price - (stop_dist * 3.0)
                        return Signal(
                            id=f"sig_{uuid.uuid4().hex[:8]}",
                            timestamp=time.time(),
                            symbol=snapshot.symbol,
                            side=OrderSide.SELL,
                            strategy=StrategyName.VWAP_FVG_REVERSION,
                            regime=snapshot.regime,
                            entry=current_price,
                            structural_stop=stop_price,
                            tp1=tp1_price,
                            tp2=tp2_price,
                            confidence=0.78,
                            thesis_expiry=time.time() + 360.0,
                            reasoning=f"Bearish FVG [{active_fvg.upper:.2f}-{active_fvg.lower:.2f}] confluence with VWAP, RSI={rsi}",
                        )

        return None

    # ------------------------------------------------------------------
    # Strategy 3: Orderbook Absorption
    # ------------------------------------------------------------------

    def evaluate_absorption(self, snapshot: MarketSnapshot) -> Optional[Signal]:
        current_price = snapshot.last_price
        atr = snapshot.atr_5m if snapshot.atr_5m > 0 else current_price * 0.003

        # Must have verified low spoof probability (< 0.50)
        if snapshot.spoof_probability >= 0.50:
            return None

        # Bullish Absorption: Heavy market selling absorbed by passive bids
        if snapshot.orderbook_imbalance >= 1.35 and snapshot.trade_velocity > 1.5:
            stop_price = current_price - (atr * 0.8)
            stop_dist = current_price - stop_price
            if stop_dist > 0:
                tp1_price = current_price + (stop_dist * 1.6)
                tp2_price = current_price + (stop_dist * 3.0)
                return Signal(
                    id=f"sig_{uuid.uuid4().hex[:8]}",
                    timestamp=time.time(),
                    symbol=snapshot.symbol,
                    side=OrderSide.BUY,
                    strategy=StrategyName.ABSORPTION,
                    regime=snapshot.regime,
                    entry=current_price,
                    structural_stop=stop_price,
                    tp1=tp1_price,
                    tp2=tp2_price,
                    confidence=0.76,
                    thesis_expiry=time.time() + 240.0,
                    reasoning=f"Passive Bullish Absorption: OBI={snapshot.orderbook_imbalance:.2f}, Velocity={snapshot.trade_velocity:.1f}t/s",
                )

        # Bearish Absorption: Heavy market buying absorbed by passive asks
        if snapshot.orderbook_imbalance <= 0.70 and snapshot.trade_velocity > 1.5:
            stop_price = current_price + (atr * 0.8)
            stop_dist = stop_price - current_price
            if stop_dist > 0:
                tp1_price = current_price - (stop_dist * 1.6)
                tp2_price = current_price - (stop_dist * 3.0)
                return Signal(
                    id=f"sig_{uuid.uuid4().hex[:8]}",
                    timestamp=time.time(),
                    symbol=snapshot.symbol,
                    side=OrderSide.SELL,
                    strategy=StrategyName.ABSORPTION,
                    regime=snapshot.regime,
                    entry=current_price,
                    structural_stop=stop_price,
                    tp1=tp1_price,
                    tp2=tp2_price,
                    confidence=0.76,
                    thesis_expiry=time.time() + 240.0,
                    reasoning=f"Passive Bearish Absorption: OBI={snapshot.orderbook_imbalance:.2f}, Velocity={snapshot.trade_velocity:.1f}t/s",
                )

        return None

    # ------------------------------------------------------------------
    # Strategy 4: Trend / FVG Continuation
    # ------------------------------------------------------------------

    def evaluate_trend_continuation(self, snapshot: MarketSnapshot, candles_15m: List[Candle]) -> Optional[Signal]:
        if len(candles_15m) < 25:
            return None

        current_price = snapshot.last_price
        atr = snapshot.atr_15m if snapshot.atr_15m > 0 else current_price * 0.005
        closes = [c.close for c in candles_15m]
        ema9 = self._calculate_ema(closes, 9)
        ema21 = self._calculate_ema(closes, 21)

        # Bullish Trend Continuation
        if snapshot.regime == MarketRegime.TREND_UP and current_price >= ema21:
            if snapshot.orderbook_imbalance >= 1.15:
                stop_price = ema21 - (atr * 0.5)
                stop_dist = current_price - stop_price
                if stop_dist > 0:
                    tp1_price = current_price + (stop_dist * 1.6)
                    tp2_price = current_price + (stop_dist * 3.0)
                    return Signal(
                        id=f"sig_{uuid.uuid4().hex[:8]}",
                        timestamp=time.time(),
                        symbol=snapshot.symbol,
                        side=OrderSide.BUY,
                        strategy=StrategyName.TREND_CONTINUATION,
                        regime=snapshot.regime,
                        entry=current_price,
                        structural_stop=stop_price,
                        tp1=tp1_price,
                        tp2=tp2_price,
                        confidence=0.80,
                        thesis_expiry=time.time() + 300.0,
                        reasoning=f"Bullish Trend Continuation: EMA9 ({ema9:.2f}) > EMA21 ({ema21:.2f}), OBI={snapshot.orderbook_imbalance:.2f}",
                    )

        # Bearish Trend Continuation
        if snapshot.regime == MarketRegime.TREND_DOWN and current_price <= ema21:
            if snapshot.orderbook_imbalance <= 0.85:
                stop_price = ema21 + (atr * 0.5)
                stop_dist = stop_price - current_price
                if stop_dist > 0:
                    tp1_price = current_price - (stop_dist * 1.6)
                    tp2_price = current_price - (stop_dist * 3.0)
                    return Signal(
                        id=f"sig_{uuid.uuid4().hex[:8]}",
                        timestamp=time.time(),
                        symbol=snapshot.symbol,
                        side=OrderSide.SELL,
                        strategy=StrategyName.TREND_CONTINUATION,
                        regime=snapshot.regime,
                        entry=current_price,
                        structural_stop=stop_price,
                        tp1=tp1_price,
                        tp2=tp2_price,
                        confidence=0.80,
                        thesis_expiry=time.time() + 300.0,
                        reasoning=f"Bearish Trend Continuation: EMA9 ({ema9:.2f}) < EMA21 ({ema21:.2f}), OBI={snapshot.orderbook_imbalance:.2f}",
                    )

        return None
