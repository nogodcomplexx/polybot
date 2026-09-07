"""
BTC Macro Beta Filter.
Acts as a macro veto layer for all altcoin perpetual setups.
"""

from typing import Dict, Any, List, Tuple
from src.bybit.models import OrderSide
from src.bybit.market.candles import Candle
from src.bybit.alpha.indicators import calculate_wavetrend, detect_swings


class BTCBetaFilter:
    """Evaluates BTCUSDT macro state and vetoes incompatible altcoin trades."""

    def _calculate_ema(self, series, period):
        if len(series) < period:
            return series[-1] if series else 0.0
        mult = 2.0 / (period + 1.0)
        ema = sum(series[:period]) / period
        for val in series[period:]:
            ema = (val - ema) * mult + ema
        return ema

    def evaluate_alt_trade(
        self,
        trade_side: OrderSide,
        btc_candles_15m: List[Candle],
        btc_candles_4h: List[Candle],
        btc_vwap: float,
        btc_liquidation_flow: float = 0.0,
    ) -> Tuple[bool, str, str, float]:
        """
        Evaluate BTC macro condition for the proposed altcoin trade side.
        Returns: (passes_filter, reason, btc_state, score_delta)
        Only extreme catastrophic market cascades trigger hard veto (passes_filter=False).
        All other macro context provides score adjustment (+8 to -6 points).
        """
        if not btc_candles_15m or len(btc_candles_15m) < 25:
            return True, "No BTC macro data (neutral context)", "NEUTRAL", 0.0

        last_btc_price = btc_candles_15m[-1].close
        wt1, wt2, wt1_prev, _ = calculate_wavetrend(btc_candles_15m)

        # BTC Trend via EMAs
        closes = [c.close for c in btc_candles_15m]
        ema9 = self._calculate_ema(closes, 9)
        ema21 = self._calculate_ema(closes, 21)

        # BTC 4H Swings
        _, ssl_4h = detect_swings(btc_candles_4h, lookback=3) if btc_candles_4h else ([], [])
        bsl_4h, _ = detect_swings(btc_candles_4h, lookback=3) if btc_candles_4h else ([], [])

        btc_state = "NEUTRAL"
        if ema9 > ema21 and wt1 > 0:
            btc_state = "BULLISH"
        elif ema9 < ema21 and wt1 < 0:
            btc_state = "BEARISH"

        score_delta = 3.0  # Baseline neutral

        # --------------------------------------------------------------
        # ALTCOIN LONG EVALUATION
        # --------------------------------------------------------------
        if trade_side == OrderSide.BUY:
            # Extreme hard veto: severe liquidation cascade (> $500k)
            if btc_liquidation_flow < -500000.0:
                return False, "BTC severe downside liquidation cascade", btc_state, -15.0

            if btc_state == "BULLISH":
                score_delta = 8.0
                if wt1 > wt1_prev:
                    score_delta = 10.0
                return True, "BTC bullish trend confluence", btc_state, score_delta
            elif btc_state == "BEARISH":
                score_delta = -4.0
                if wt1 < -25.0 and (wt1 - wt1_prev) < -6.0:
                    score_delta = -6.0
                return True, "BTC bearish momentum counter-current", btc_state, score_delta
            else:
                return True, "BTC neutral state", btc_state, 3.0

        # --------------------------------------------------------------
        # ALTCOIN SHORT EVALUATION
        # --------------------------------------------------------------
        elif trade_side == OrderSide.SELL:
            # Extreme hard veto: severe upside liquidation cascade (> $500k)
            if btc_liquidation_flow > 500000.0:
                return False, "BTC severe upside liquidation cascade", btc_state, -15.0

            if btc_state == "BEARISH":
                score_delta = 8.0
                if wt1 < wt1_prev:
                    score_delta = 10.0
                return True, "BTC bearish trend confluence", btc_state, score_delta
            elif btc_state == "BULLISH":
                score_delta = -4.0
                if wt1 > 25.0 and (wt1 - wt1_prev) > 6.0:
                    score_delta = -6.0
                return True, "BTC bullish momentum counter-current", btc_state, score_delta
            else:
                return True, "BTC neutral state", btc_state, 3.0

        return True, "", btc_state, 0.0
