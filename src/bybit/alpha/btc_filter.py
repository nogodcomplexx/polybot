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
    ) -> Tuple[bool, str, str]:
        """
        Evaluate if BTC macro condition permits the proposed altcoin trade side.
        Returns: (passes_filter, veto_reason, btc_state)
        """
        if not btc_candles_15m or len(btc_candles_15m) < 25:
            return True, "", "NEUTRAL"

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

        # --------------------------------------------------------------
        # ALTCOIN LONG VETOES
        # --------------------------------------------------------------
        if trade_side == OrderSide.BUY:
            # Veto 1: BTC severe bearish momentum acceleration
            if wt1 < -25.0 and (wt1 - wt1_prev) < -6.0:
                return False, "BTC momentum accelerating downward sharply", btc_state

            # Veto 2: BTC breakdown of key 4H swing low
            if ssl_4h:
                lowest_4h_low = min(s.price for s in ssl_4h[-3:])
                if last_btc_price < lowest_4h_low * 0.998:
                    return False, "BTC broke major 4H support structure", btc_state

            # Veto 3: BTC downside liquidation cascade
            if btc_liquidation_flow < -300000.0:
                return False, "BTC in downside liquidation cascade", btc_state

        # --------------------------------------------------------------
        # ALTCOIN SHORT VETOES
        # --------------------------------------------------------------
        elif trade_side == OrderSide.SELL:
            # Veto 1: BTC aggressive upward squeeze
            if wt1 > 25.0 and (wt1 - wt1_prev) > 6.0 and last_btc_price > btc_vwap:
                return False, "BTC aggressively squeezing upward above VWAP", btc_state

            # Veto 2: BTC breakout of key 4H swing high with volume
            if bsl_4h:
                highest_4h_high = max(b.price for b in bsl_4h[-3:])
                if last_btc_price > highest_4h_high * 1.002:
                    return False, "BTC broke major 4H resistance structure", btc_state

            # Veto 3: BTC upside liquidation cascade
            if btc_liquidation_flow > 300000.0:
                return False, "BTC in upside liquidation cascade", btc_state

        return True, "", btc_state
