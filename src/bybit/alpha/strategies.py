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

    def __init__(self, tunable: Optional[Any] = None):
        self.tunable = tunable

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
        """
        Archetype 1: Turtle Soup / Liquidity Purge (SFP).
        Core Edge: Structural sweep of swing high/low with wick rejection back inside range.
        Secondary indicators (WT, OBI, CVD) contribute to Alpha Score, not as hard vetoes here.
        """
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
            bars_to_check = []
            if len(candles_15m) >= 2:
                bars_to_check.append(("prev", candles_15m[-2]))
            bars_to_check.append(("curr", candles_15m[-1]))

            sfp_found = False
            sweep_depth_pct = 0.0
            trigger_bar = candles_15m[-1]

            for bar_type, bar in bars_to_check:
                if bar.low < recent_ssl:
                    depth = (recent_ssl - bar.low) / recent_ssl * 100
                    if 0.15 <= depth <= 0.80:
                        total_span = bar.high - bar.low
                        lower_wick = min(bar.open, bar.close) - bar.low
                        wick_ratio = (lower_wick / total_span) if total_span > 0 else 0.0

                        if bar_type == "prev":
                            if bar.close >= recent_ssl and current_price >= recent_ssl:
                                sfp_found = True
                                sweep_depth_pct = depth
                                trigger_bar = bar
                                break
                        elif bar_type == "curr":
                            if bar.close >= recent_ssl and current_price >= recent_ssl and wick_ratio >= 0.25:
                                sfp_found = True
                                sweep_depth_pct = depth
                                trigger_bar = bar
                                break

            if sfp_found:
                raw_stop_dist = max(current_price - (trigger_bar.low - (atr * 0.4)), current_price * 0.0035)
                stop_dist = min(raw_stop_dist, current_price * 0.0090)
                stop_price = current_price - stop_dist
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
                        confidence=0.80,
                        thesis_expiry=time.time() + 300.0,
                        reasoning=f"SSL swept at {recent_ssl:.2f} ({sweep_depth_pct:.2f}%), SFP wick rejected",
                        features={
                            "sweep_depth_pct": round(sweep_depth_pct, 3),
                            "sfp_confirmed": True,
                            "swing_price": recent_ssl,
                            "wt1": wt1,
                            "wt1_prev": wt1_prev,
                        }
                    )

        # --- SHORT SETUP: Sweep BSL ---
        if bsl_zones:
            recent_bsl = bsl_zones[-1].price
            bars_to_check = []
            if len(candles_15m) >= 2:
                bars_to_check.append(("prev", candles_15m[-2]))
            bars_to_check.append(("curr", candles_15m[-1]))

            sfp_found = False
            sweep_depth_pct = 0.0
            trigger_bar = candles_15m[-1]

            for bar_type, bar in bars_to_check:
                if bar.high > recent_bsl:
                    depth = (bar.high - recent_bsl) / recent_bsl * 100
                    if 0.15 <= depth <= 0.80:
                        total_span = bar.high - bar.low
                        upper_wick = bar.high - max(bar.open, bar.close)
                        wick_ratio = (upper_wick / total_span) if total_span > 0 else 0.0

                        if bar_type == "prev":
                            if bar.close <= recent_bsl and current_price <= recent_bsl:
                                sfp_found = True
                                sweep_depth_pct = depth
                                trigger_bar = bar
                                break
                        elif bar_type == "curr":
                            if bar.close <= recent_bsl and current_price <= recent_bsl and wick_ratio >= 0.25:
                                sfp_found = True
                                sweep_depth_pct = depth
                                trigger_bar = bar
                                break

            if sfp_found:
                raw_stop_dist = max((trigger_bar.high + (atr * 0.4)) - current_price, current_price * 0.0035)
                stop_dist = min(raw_stop_dist, current_price * 0.0090)
                stop_price = current_price + stop_dist
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
                        confidence=0.80,
                        thesis_expiry=time.time() + 300.0,
                        reasoning=f"BSL swept at {recent_bsl:.2f} ({sweep_depth_pct:.2f}%), SFP wick rejected",
                        features={
                            "sweep_depth_pct": round(sweep_depth_pct, 3),
                            "sfp_confirmed": True,
                            "swing_price": recent_bsl,
                            "wt1": wt1,
                            "wt1_prev": wt1_prev,
                        }
                    )

        return None

    # ------------------------------------------------------------------
    # Strategy 2: VWAP / FVG Mean Reversion
    # ------------------------------------------------------------------

    def evaluate_vwap_fvg_reversion(self, snapshot: MarketSnapshot, candles_15m: List[Candle]) -> Optional[Signal]:
        """
        Archetype 2: VWAP + FVG Mean Reversion.
        Core Edge: Price pulling back into a valid Fair Value Gap in proximity to VWAP.
        """
        if len(candles_15m) < 25:
            return None

        current_price = snapshot.last_price
        vwap = snapshot.vwap_15m if snapshot.vwap_15m > 0 else current_price
        rsi = calculate_rsi(candles_15m, 14)
        atr = snapshot.atr_15m if snapshot.atr_15m > 0 else current_price * 0.005
        fvgs = detect_fvgs(candles_15m)

        # Bullish Reversion: Price pulled back into unmitigated bullish FVG near VWAP
        bullish_fvgs = [f for f in fvgs if f.fvg_type == "BULLISH" and not f.mitigated]
        if bullish_fvgs:
            active_fvg = bullish_fvgs[-1]
            if active_fvg.lower <= current_price <= active_fvg.upper:
                vwap_dist_pct = abs(current_price - vwap) / current_price * 100
                if vwap_dist_pct <= 1.20:
                    raw_stop_dist = max(current_price - (active_fvg.lower - (atr * 0.4)), current_price * 0.0035)
                    stop_dist = min(raw_stop_dist, current_price * 0.0090)
                    stop_price = current_price - stop_dist
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
                            reasoning=f"Bullish FVG [{active_fvg.lower:.2f}-{active_fvg.upper:.2f}] near VWAP ({vwap_dist_pct:.2f}%)",
                            features={
                                "fvg_confluence": True,
                                "vwap_deviation_pct": round(vwap_dist_pct, 2),
                                "rsi": rsi,
                            }
                        )

        # Bearish Reversion: Price pushed up into unmitigated bearish FVG near VWAP
        bearish_fvgs = [f for f in fvgs if f.fvg_type == "BEARISH" and not f.mitigated]
        if bearish_fvgs:
            active_fvg = bearish_fvgs[-1]
            if active_fvg.lower <= current_price <= active_fvg.upper:
                vwap_dist_pct = abs(current_price - vwap) / current_price * 100
                if vwap_dist_pct <= 1.20:
                    raw_stop_dist = max((active_fvg.upper + (atr * 0.4)) - current_price, current_price * 0.0035)
                    stop_dist = min(raw_stop_dist, current_price * 0.0090)
                    stop_price = current_price + stop_dist
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
                            reasoning=f"Bearish FVG [{active_fvg.upper:.2f}-{active_fvg.lower:.2f}] near VWAP ({vwap_dist_pct:.2f}%)",
                            features={
                                "fvg_confluence": True,
                                "vwap_deviation_pct": round(vwap_dist_pct, 2),
                                "rsi": rsi,
                            }
                        )

        return None

    # ------------------------------------------------------------------
    # Strategy 3: Orderbook Absorption
    # ------------------------------------------------------------------

    def evaluate_absorption(self, snapshot: MarketSnapshot) -> Optional[Signal]:
        """
        Archetype 3: Orderbook Absorption Cluster.
        Core Edge: Strong aggressive market flow being absorbed by resting limit liquidity.
        """
        current_price = snapshot.last_price
        atr = snapshot.atr_5m if snapshot.atr_5m > 0 else current_price * 0.003

        # Safety: avoid high spoof uncertainty
        if snapshot.spoof_probability >= 0.65:
            return None

        bull_obi = self.tunable.get("ob_imbalance_bullish") if self.tunable else 1.25
        bear_obi = self.tunable.get("ob_imbalance_bearish") if self.tunable else 0.75

        # Bullish Absorption: Heavy market selling absorbed by passive bids
        if snapshot.orderbook_imbalance >= bull_obi and snapshot.trade_velocity > 1.2:
            raw_stop_dist = max(atr * 0.8, current_price * 0.0035)
            stop_dist = min(raw_stop_dist, current_price * 0.0085)
            stop_price = current_price - stop_dist
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
                    features={
                        "absorption_confirmed": True,
                        "obi": snapshot.orderbook_imbalance,
                        "velocity": snapshot.trade_velocity,
                        "spoof_probability": snapshot.spoof_probability,
                        "wt1": snapshot.wt1_15m,
                        "wt1_prev": snapshot.wt1_prev,
                        "rsi": snapshot.rsi_15m,
                    }
                )

        # Bearish Absorption: Heavy market buying absorbed by passive asks
        if snapshot.orderbook_imbalance <= bear_obi and snapshot.trade_velocity > 1.2:
            raw_stop_dist = max(atr * 0.8, current_price * 0.0035)
            stop_dist = min(raw_stop_dist, current_price * 0.0085)
            stop_price = current_price + stop_dist
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
                    features={
                        "absorption_confirmed": True,
                        "obi": snapshot.orderbook_imbalance,
                        "velocity": snapshot.trade_velocity,
                        "spoof_probability": snapshot.spoof_probability,
                        "wt1": snapshot.wt1_15m,
                        "wt1_prev": snapshot.wt1_prev,
                        "rsi": snapshot.rsi_15m,
                    }
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
        bull_obi = self.tunable.get("ob_imbalance_bullish") if self.tunable else 1.25
        bear_obi = self.tunable.get("ob_imbalance_bearish") if self.tunable else 0.75

        # Bullish Trend Continuation
        if snapshot.regime == MarketRegime.TREND_UP and ema9 > ema21 and current_price >= ema21:
            dist_from_ema9_pct = abs(current_price - ema9) / current_price * 100
            # Pullback to within 0.75% of EMA9 (no FOMO chase) and strong orderbook support
            if dist_from_ema9_pct <= 0.75 and snapshot.orderbook_imbalance >= bull_obi:
                raw_stop_dist = max(current_price - (ema21 - (atr * 0.5)), current_price * 0.0035)
                stop_dist = min(raw_stop_dist, current_price * 0.0085)
                stop_price = current_price - stop_dist
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
                        reasoning=f"Bullish Trend Continuation: EMA9 ({ema9:.2f}) > EMA21 ({ema21:.2f}), dist={dist_from_ema9_pct:.2f}%, OBI={snapshot.orderbook_imbalance:.2f}",
                        features={
                            "trend_confirmed": True,
                            "dist_from_ema9_pct": round(dist_from_ema9_pct, 2),
                            "obi": snapshot.orderbook_imbalance,
                            "wt1": snapshot.wt1_15m,
                            "wt1_prev": snapshot.wt1_prev,
                            "rsi": snapshot.rsi_15m,
                        }
                    )

        # Bearish Trend Continuation
        if snapshot.regime == MarketRegime.TREND_DOWN and ema9 < ema21 and current_price <= ema21:
            dist_from_ema9_pct = abs(current_price - ema9) / current_price * 100
            # Pullback to within 0.75% of EMA9 (no FOMO chase) and strong orderbook selling
            if dist_from_ema9_pct <= 0.75 and snapshot.orderbook_imbalance <= bear_obi:
                raw_stop_dist = max((ema21 + (atr * 0.5)) - current_price, current_price * 0.0035)
                stop_dist = min(raw_stop_dist, current_price * 0.0085)
                stop_price = current_price + stop_dist
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
                        reasoning=f"Bearish Trend Continuation: EMA9 ({ema9:.2f}) < EMA21 ({ema21:.2f}), dist={dist_from_ema9_pct:.2f}%, OBI={snapshot.orderbook_imbalance:.2f}",
                        features={
                            "trend_confirmed": True,
                            "dist_from_ema9_pct": round(dist_from_ema9_pct, 2),
                            "obi": snapshot.orderbook_imbalance,
                            "wt1": snapshot.wt1_15m,
                            "wt1_prev": snapshot.wt1_prev,
                            "rsi": snapshot.rsi_15m,
                        }
                    )

        return None
