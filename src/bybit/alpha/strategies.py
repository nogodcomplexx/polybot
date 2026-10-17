"""
Alpha Strategies Suite for Bybit Linear Perpetual Scalper.
Implements:
1. Strategy 1: Turtle Soup / Liquidity Sweep (SFP)
2. Strategy 2: VWAP / FVG Mean Reversion
3. Strategy 3: Orderbook Absorption
4. Strategy 4: Trend / FVG Continuation
"""

import time
import uuid
from typing import Dict, Any, List, Optional
from src.bybit.models import Signal, OrderSide, StrategyName, MarketRegime, MarketSnapshot
from src.bybit.market.candles import Candle
from src.bybit.config import TunableParameterRegistry
from src.bybit.alpha.indicators import (
    calculate_wavetrend, calculate_rsi, detect_fvgs, detect_swings
)


class AlphaStrategySuite:
    """Evaluates all 4 alpha strategies using dynamically tunable parameters."""

    def __init__(self, tunable: Optional[TunableParameterRegistry] = None):
        self.tunable = tunable or TunableParameterRegistry()

    def _get_param(self, name: str, default: float) -> float:
        if self.tunable and name in self.tunable.bounds:
            return self.tunable.get(name)
        return default

    def _calculate_ema(self, series: List[float], period: int) -> float:
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
        Core Edge: Structural sweep of swing high/low with confirmed wick rejection back inside range.
        Checks both the developing bar (requiring >= 35% rejection wick) and the just-closed bar [-2].
        """
        if len(candles_15m) < 25:
            return None

        current_price = snapshot.last_price
        if current_price <= 0:
            return None

        atr = snapshot.atr_15m if snapshot.atr_15m > 0 else current_price * 0.005
        atr_mult = self._get_param("atr_stop_multiplier", 1.50) * 0.30  # ~0.45x ATR buffer beyond sweep wick
        wt1 = snapshot.wt1_15m
        wt1_prev = snapshot.wt1_prev

        # Detect swings excluding the last 2 bars so the swept level is an established prior pivot
        bsl_zones, ssl_zones = detect_swings(candles_15m[:-1], lookback=4)

        curr_bar = candles_15m[-1]
        prev_bar = candles_15m[-2]

        # --- LONG SETUP: Sweep SSL ---
        if ssl_zones:
            recent_ssl = ssl_zones[-1].price
            for bar, is_closed_bar in ((prev_bar, True), (curr_bar, False)):
                sweep_depth_pct = (recent_ssl - bar.low) / recent_ssl * 100.0 if recent_ssl > 0 else 0.0
                if bar.low < recent_ssl and 0.12 <= sweep_depth_pct <= 0.85:
                    bar_range = max(bar.high - bar.low, 1e-8)
                    lower_wick = min(bar.open, bar.close) - bar.low if is_closed_bar else (current_price - bar.low)
                    wick_ratio = lower_wick / bar_range

                    # Require reclaim of SSL + meaningful rejection wick (>= 25% of bar range for intrabar, or closed bar reclaim)
                    rejection_ok = (wick_ratio >= 0.25) if not is_closed_bar else (wick_ratio >= 0.15 or bar.close >= recent_ssl)
                    if bar.close >= recent_ssl and current_price >= recent_ssl and rejection_ok:
                        sweep_low = min(curr_bar.low, prev_bar.low)
                        raw_stop_dist = max(current_price - (sweep_low - (atr * atr_mult)), current_price * 0.0035)
                        stop_dist = min(raw_stop_dist, current_price * 0.0095)
                        stop_price = current_price - stop_dist
                        if stop_dist > 0:
                            tp1_price = current_price + (stop_dist * 1.65)
                            tp2_price = current_price + (stop_dist * 3.00)
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
                                confidence=0.82 if is_closed_bar else 0.78,
                                thesis_expiry=time.time() + 300.0,
                                reasoning=f"SSL swept at {recent_ssl:.4f} ({sweep_depth_pct:.2f}%), wick ratio={wick_ratio:.2f}",
                                features={
                                    "sweep_depth_pct": round(sweep_depth_pct, 3),
                                    "sfp_confirmed": True,
                                    "closed_bar_confirm": is_closed_bar,
                                    "wick_ratio": round(wick_ratio, 2),
                                    "swing_price": recent_ssl,
                                    "wt1": wt1,
                                    "wt1_prev": wt1_prev,
                                    "rsi": snapshot.rsi_15m,
                                }
                            )

        # --- SHORT SETUP: Sweep BSL ---
        if bsl_zones:
            recent_bsl = bsl_zones[-1].price
            for bar, is_closed_bar in ((prev_bar, True), (curr_bar, False)):
                sweep_depth_pct = (bar.high - recent_bsl) / recent_bsl * 100.0 if recent_bsl > 0 else 0.0
                if bar.high > recent_bsl and 0.12 <= sweep_depth_pct <= 0.85:
                    bar_range = max(bar.high - bar.low, 1e-8)
                    upper_wick = bar.high - max(bar.open, bar.close) if is_closed_bar else (bar.high - current_price)
                    wick_ratio = upper_wick / bar_range

                    rejection_ok = (wick_ratio >= 0.25) if not is_closed_bar else (wick_ratio >= 0.15 or bar.close <= recent_bsl)
                    if bar.close <= recent_bsl and current_price <= recent_bsl and rejection_ok:
                        sweep_high = max(curr_bar.high, prev_bar.high)
                        raw_stop_dist = max((sweep_high + (atr * atr_mult)) - current_price, current_price * 0.0035)
                        stop_dist = min(raw_stop_dist, current_price * 0.0095)
                        stop_price = current_price + stop_dist
                        if stop_dist > 0:
                            tp1_price = current_price - (stop_dist * 1.65)
                            tp2_price = current_price - (stop_dist * 3.00)
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
                                confidence=0.82 if is_closed_bar else 0.78,
                                thesis_expiry=time.time() + 300.0,
                                reasoning=f"BSL swept at {recent_bsl:.4f} ({sweep_depth_pct:.2f}%), wick ratio={wick_ratio:.2f}",
                                features={
                                    "sweep_depth_pct": round(sweep_depth_pct, 3),
                                    "sfp_confirmed": True,
                                    "closed_bar_confirm": is_closed_bar,
                                    "wick_ratio": round(wick_ratio, 2),
                                    "swing_price": recent_bsl,
                                    "wt1": wt1,
                                    "wt1_prev": wt1_prev,
                                    "rsi": snapshot.rsi_15m,
                                }
                            )

        return None

    # ------------------------------------------------------------------
    # Strategy 2: VWAP / FVG Mean Reversion
    # ------------------------------------------------------------------

    def evaluate_vwap_fvg_reversion(self, snapshot: MarketSnapshot, candles_15m: List[Candle]) -> Optional[Signal]:
        """
        Archetype 2: VWAP + Unmitigated FVG Mean Reversion.
        Core Edge: Price pulling back into a fresh, unmitigated Fair Value Gap in proximity to VWAP.
        """
        if len(candles_15m) < 25:
            return None

        current_price = snapshot.last_price
        if current_price <= 0:
            return None

        vwap = snapshot.vwap_15m if snapshot.vwap_15m > 0 else current_price
        rsi = snapshot.rsi_15m if snapshot.rsi_15m != 50.0 else calculate_rsi(candles_15m, 14)
        atr = snapshot.atr_15m if snapshot.atr_15m > 0 else current_price * 0.005
        atr_mult = self._get_param("atr_stop_multiplier", 1.50) * 0.30

        # detect_fvgs now filters out mitigated FVGs automatically
        fvgs = [f for f in detect_fvgs(candles_15m) if not f.mitigated]

        # Bullish Reversion: Price pulled back into unmitigated bullish FVG near VWAP
        bullish_fvgs = [f for f in fvgs if f.fvg_type == "BULLISH"]
        if bullish_fvgs:
            active_fvg = bullish_fvgs[-1]
            if active_fvg.lower <= current_price <= active_fvg.upper:
                vwap_dist_pct = abs(current_price - vwap) / current_price * 100.0
                if vwap_dist_pct <= 1.20:
                    raw_stop_dist = max(current_price - (active_fvg.lower - (atr * atr_mult)), current_price * 0.0035)
                    stop_dist = min(raw_stop_dist, current_price * 0.0090)
                    stop_price = current_price - stop_dist
                    if stop_dist > 0:
                        tp1_price = current_price + (stop_dist * 1.65)
                        tp2_price = current_price + (stop_dist * 3.00)
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
                            confidence=0.79,
                            thesis_expiry=time.time() + 360.0,
                            reasoning=f"Unmitigated Bullish FVG [{active_fvg.lower:.4f}-{active_fvg.upper:.4f}] near VWAP ({vwap_dist_pct:.2f}%)",
                            features={
                                "fvg_confluence": True,
                                "vwap_deviation_pct": round(vwap_dist_pct, 2),
                                "rsi": rsi,
                                "wt1": snapshot.wt1_15m,
                                "wt1_prev": snapshot.wt1_prev,
                            }
                        )

        # Bearish Reversion: Price pushed up into unmitigated bearish FVG near VWAP
        bearish_fvgs = [f for f in fvgs if f.fvg_type == "BEARISH"]
        if bearish_fvgs:
            active_fvg = bearish_fvgs[-1]
            if active_fvg.lower <= current_price <= active_fvg.upper:
                vwap_dist_pct = abs(current_price - vwap) / current_price * 100.0
                if vwap_dist_pct <= 1.20:
                    raw_stop_dist = max((active_fvg.upper + (atr * atr_mult)) - current_price, current_price * 0.0035)
                    stop_dist = min(raw_stop_dist, current_price * 0.0090)
                    stop_price = current_price + stop_dist
                    if stop_dist > 0:
                        tp1_price = current_price - (stop_dist * 1.65)
                        tp2_price = current_price - (stop_dist * 3.00)
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
                            confidence=0.79,
                            thesis_expiry=time.time() + 360.0,
                            reasoning=f"Unmitigated Bearish FVG [{active_fvg.lower:.4f}-{active_fvg.upper:.4f}] near VWAP ({vwap_dist_pct:.2f}%)",
                            features={
                                "fvg_confluence": True,
                                "vwap_deviation_pct": round(vwap_dist_pct, 2),
                                "rsi": rsi,
                                "wt1": snapshot.wt1_15m,
                                "wt1_prev": snapshot.wt1_prev,
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
        Uses tunable OBI thresholds, spoof_max_prob, and atr_stop_multiplier.
        """
        current_price = snapshot.last_price
        if current_price <= 0:
            return None

        atr = snapshot.atr_5m if snapshot.atr_5m > 0 else current_price * 0.003
        spoof_max = self._get_param("spoof_max_prob", 0.65)
        obi_bull = self._get_param("ob_imbalance_bullish", 1.25)
        obi_bear = self._get_param("ob_imbalance_bearish", 0.75)
        atr_mult = self._get_param("atr_stop_multiplier", 1.50) * 0.55  # ~0.825x 5m ATR

        if snapshot.spoof_probability >= spoof_max:
            return None

        # Bullish Absorption: Heavy market selling absorbed by passive bids
        if snapshot.orderbook_imbalance >= min(obi_bull, 1.25) and snapshot.trade_velocity > 1.2:
            raw_stop_dist = max(atr * atr_mult, current_price * 0.0035)
            stop_dist = min(raw_stop_dist, current_price * 0.0085)
            stop_price = current_price - stop_dist
            if stop_dist > 0:
                tp1_price = current_price + (stop_dist * 1.65)
                tp2_price = current_price + (stop_dist * 3.00)
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
                    confidence=0.80 if snapshot.orderbook_imbalance >= 1.45 else 0.76,
                    thesis_expiry=time.time() + 240.0,
                    reasoning=f"Passive Bullish Absorption: OBI={snapshot.orderbook_imbalance:.2f} (>={obi_bull:.2f}), Velocity={snapshot.trade_velocity:.1f}t/s",
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
        if snapshot.orderbook_imbalance <= max(obi_bear, 0.75) and snapshot.trade_velocity > 1.2:
            raw_stop_dist = max(atr * atr_mult, current_price * 0.0035)
            stop_dist = min(raw_stop_dist, current_price * 0.0085)
            stop_price = current_price + stop_dist
            if stop_dist > 0:
                tp1_price = current_price - (stop_dist * 1.65)
                tp2_price = current_price - (stop_dist * 3.00)
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
                    confidence=0.80 if snapshot.orderbook_imbalance <= 0.60 else 0.76,
                    thesis_expiry=time.time() + 240.0,
                    reasoning=f"Passive Bearish Absorption: OBI={snapshot.orderbook_imbalance:.2f} (<={obi_bear:.2f}), Velocity={snapshot.trade_velocity:.1f}t/s",
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
        """
        Archetype 4: Trend / EMA9 Pullback Continuation with Anti-Knife Guard.
        """
        if len(candles_15m) < 25:
            return None

        current_price = snapshot.last_price
        if current_price <= 0:
            return None

        atr = snapshot.atr_15m if snapshot.atr_15m > 0 else current_price * 0.005
        obi_bull = self._get_param("ob_imbalance_bullish", 1.25)
        obi_bear = self._get_param("ob_imbalance_bearish", 0.75)
        atr_mult = self._get_param("atr_stop_multiplier", 1.50) * 0.35

        closes = [c.close for c in candles_15m]
        ema9 = self._calculate_ema(closes, 9)
        ema21 = self._calculate_ema(closes, 21)
        last_bar = candles_15m[-1]

        # Bullish Trend Continuation
        if snapshot.regime == MarketRegime.TREND_UP and ema9 > ema21 and current_price >= ema21:
            dist_from_ema9_pct = abs(current_price - ema9) / current_price * 100.0
            # Anti-knife guard: do not buy if current bar is a violent bearish breakdown candle (> 1.2x ATR red body)
            bearish_body = max(0.0, last_bar.open - last_bar.close)
            is_falling_knife = bearish_body > (atr * 1.2)

            if not is_falling_knife and dist_from_ema9_pct <= 0.75 and snapshot.orderbook_imbalance >= min(obi_bull, 1.25):
                raw_stop_dist = max(current_price - (ema21 - (atr * atr_mult)), current_price * 0.0035)
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
                        reasoning=f"Bullish Trend Continuation: EMA9 ({ema9:.4f}) > EMA21 ({ema21:.4f}), dist={dist_from_ema9_pct:.2f}%, OBI={snapshot.orderbook_imbalance:.2f}",
                        features={
                            "ema9_dist_pct": round(dist_from_ema9_pct, 3),
                            "wt1": snapshot.wt1_15m,
                            "wt1_prev": snapshot.wt1_prev,
                            "rsi": snapshot.rsi_15m,
                        }
                    )

        # Bearish Trend Continuation
        if snapshot.regime == MarketRegime.TREND_DOWN and ema9 < ema21 and current_price <= ema21:
            dist_from_ema9_pct = abs(current_price - ema9) / current_price * 100.0
            bullish_body = max(0.0, last_bar.close - last_bar.open)
            is_short_squeeze_candle = bullish_body > (atr * 1.2)

            if not is_short_squeeze_candle and dist_from_ema9_pct <= 0.75 and snapshot.orderbook_imbalance <= max(obi_bear, 0.75):
                raw_stop_dist = max((ema21 + (atr * atr_mult)) - current_price, current_price * 0.0035)
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
                        reasoning=f"Bearish Trend Continuation: EMA9 ({ema9:.4f}) < EMA21 ({ema21:.4f}), dist={dist_from_ema9_pct:.2f}%, OBI={snapshot.orderbook_imbalance:.2f}",
                        features={
                            "ema9_dist_pct": round(dist_from_ema9_pct, 3),
                            "wt1": snapshot.wt1_15m,
                            "wt1_prev": snapshot.wt1_prev,
                            "rsi": snapshot.rsi_15m,
                        }
                    )

        return None
