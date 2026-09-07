"""
Alpha Scoring, Expected Value (EV), and Execution Quality Engines.
Implements the 0-100 institutional scoring breakdown and realistic friction-adjusted EV calculation.
"""

from typing import Dict, Any, Tuple
from src.bybit.models import Signal, MarketSnapshot, OrderSide, StrategyName


class AlphaScorer:
    """Computes explainable 0-100 Alpha Score, net EV, and Execution Quality."""

    MIN_ALPHA_SCORE = 75.0
    MIN_EXECUTION_SCORE = 65.0
    MIN_EXPECTED_EV = 0.0020  # +0.20% net expected return after fees and friction

    def score_signal(
        self,
        signal: Signal,
        snapshot: MarketSnapshot,
        cross_confidence: float = 50.0,
        derivatives_score: float = 0.0,
    ) -> float:
        """
        Compute 0-100 Alpha Score:
        STRUCTURE:       0-20
        ORDERFLOW:       0-20
        LIQUIDITY:       0-15
        REGIME:          10
        BTC BETA:        10
        MOMENTUM:        10
        EXECUTION:       10
        R:R:              5
        CROSS-VENUE:     +5 / -8 (Cross-exchange +3/-5 and derivatives +2/-3 adjustment)
        """
        score = 0.0

        # 1. Structure (0-20): Proximity to clean invalidation & clear structural anchor
        stop_dist_pct = abs(signal.entry - signal.structural_stop) / signal.entry * 100
        if 0.20 <= stop_dist_pct <= 1.20:
            score += 20.0
        elif 0.10 <= stop_dist_pct <= 1.80:
            score += 14.0
        else:
            score += 8.0

        # 2. Orderflow (0-20): OBI alignment and volume velocity
        obi = snapshot.orderbook_imbalance
        if signal.side == OrderSide.BUY:
            if obi >= 1.30:
                score += 20.0
            elif obi >= 1.10:
                score += 15.0
            elif obi >= 1.0:
                score += 10.0
        else:
            if obi <= 0.75:
                score += 20.0
            elif obi <= 0.90:
                score += 15.0
            elif obi <= 1.0:
                score += 10.0

        # 3. Liquidity (0-15): Book depth and low spoof probability
        if snapshot.spoof_probability < 0.35 and snapshot.bid_depth > 0 and snapshot.ask_depth > 0:
            score += 15.0
        elif snapshot.spoof_probability < 0.50:
            score += 10.0
        else:
            score += 4.0

        # 4. Regime Alignment (0-10)
        if signal.regime.value in ("TREND_UP", "TREND_DOWN", "RANGE"):
            score += 10.0
        elif signal.regime.value in ("LOW_VOL", "HIGH_VOL"):
            score += 7.0

        # 5. BTC Beta (0-10)
        if snapshot.btc_state in ("BULLISH", "NEUTRAL") and signal.side == OrderSide.BUY:
            score += 10.0
        elif snapshot.btc_state in ("BEARISH", "NEUTRAL") and signal.side == OrderSide.SELL:
            score += 10.0
        else:
            score += 5.0

        # 6. Momentum (0-10): WaveTrend alignment
        if signal.side == OrderSide.BUY and snapshot.wt1_15m < -30.0:
            score += 10.0
        elif signal.side == OrderSide.SELL and snapshot.wt1_15m > 30.0:
            score += 10.0
        else:
            score += 6.0

        # 7. Execution Quality (0-10): Tight spread (< 0.05%)
        if snapshot.spread_pct < 0.04:
            score += 10.0
        elif snapshot.spread_pct < 0.08:
            score += 7.0
        else:
            score += 4.0

        # 8. R:R (0-5)
        stop_dist = abs(signal.entry - signal.structural_stop)
        tp1_dist = abs(signal.tp1 - signal.entry)
        rr1 = tp1_dist / stop_dist if stop_dist > 0 else 0.0
        if rr1 >= 1.6:
            score += 5.0
        elif rr1 >= 1.4:
            score += 3.0

        # 9. Cross-Exchange & Derivatives Context Confirmation (+/- 5 points)
        if cross_confidence >= 70.0:
            score += min(3.0, (cross_confidence - 50.0) / 10.0)
        elif cross_confidence <= 30.0:
            score -= min(5.0, (50.0 - cross_confidence) / 10.0)

        if signal.side == OrderSide.BUY:
            if derivatives_score >= 25.0:
                score += 2.0
            elif derivatives_score <= -25.0:
                score -= 3.0
        else:
            if derivatives_score <= -25.0:
                score += 2.0
            elif derivatives_score >= 25.0:
                score -= 3.0

        return max(0.0, min(100.0, round(score, 1)))


    def calculate_ev(self, signal: Signal, snapshot: MarketSnapshot) -> float:
        """
        Calculate Expected Value (EV):
        EV = P(win) * AvgWin - P(loss) * AvgLoss - fees - slippage - adverse_selection
        """
        stop_dist_pct = abs(signal.entry - signal.structural_stop) / signal.entry
        tp1_dist_pct = abs(signal.tp1 - signal.entry) / signal.entry
        tp2_dist_pct = abs(signal.tp2 - signal.entry) / signal.entry

        # Base Win Probability from Confidence & Alpha Score
        p_win = max(0.40, min(0.72, signal.confidence * 0.70 + (signal.alpha_score / 100.0) * 0.30))
        p_loss = 1.0 - p_win

        avg_win_pct = (tp1_dist_pct * 0.50) + (tp2_dist_pct * 0.50)
        avg_loss_pct = stop_dist_pct

        # Friction
        maker_fee = 0.0002         # 0.02% Bybit maker fee
        taker_fee = 0.00055        # 0.055% Bybit taker fee (for SL)
        total_fees = maker_fee + (taker_fee * p_loss + maker_fee * p_win)
        expected_slippage = (snapshot.spread_pct / 100.0) * 0.5
        adverse_selection = 0.0004  # 0.04% estimated adverse selection buffer

        ev = (p_win * avg_win_pct) - (p_loss * avg_loss_pct) - total_fees - expected_slippage - adverse_selection
        return round(ev, 4)

    def evaluate_execution_quality(self, signal: Signal, snapshot: MarketSnapshot) -> float:
        """Score execution feasibility and quality (0-100)."""
        score = 0.0

        # Spread (max 35 pts)
        if snapshot.spread_pct <= 0.03:
            score += 35.0
        elif snapshot.spread_pct <= 0.06:
            score += 25.0
        elif snapshot.spread_pct <= 0.10:
            score += 15.0

        # Depth at Entry (max 25 pts)
        if snapshot.bid_depth > 0 and snapshot.ask_depth > 0:
            score += 25.0

        # Spoof Safety (max 25 pts)
        if snapshot.spoof_probability < 0.30:
            score += 25.0
        elif snapshot.spoof_probability < 0.50:
            score += 15.0

        # Microprice proximity (max 15 pts)
        micro_dev_pct = abs(snapshot.last_price - snapshot.microprice) / snapshot.last_price * 100 if snapshot.last_price > 0 else 0.0
        if micro_dev_pct < 0.03:
            score += 15.0
        elif micro_dev_pct < 0.08:
            score += 10.0

        return round(score, 1)
