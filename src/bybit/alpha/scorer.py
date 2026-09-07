"""
Alpha Scoring, Expected Value (EV), and Execution Quality Engines.
Implements the 0-100 institutional scoring breakdown and realistic friction-adjusted EV calculation.
"""

from typing import Dict, Any, Tuple
from src.bybit.models import Signal, MarketSnapshot, OrderSide, StrategyName


class AlphaScorer:
    """Computes explainable 0-100 Alpha Score, net EV, and Execution Quality."""

    MIN_ALPHA_SCORE = 70.0
    MIN_EXECUTION_SCORE = 65.0
    MIN_EXPECTED_EV = 0.0020  # +0.20% net expected return after fees and friction

    def score_signal(
        self,
        signal: Signal,
        snapshot: MarketSnapshot,
        btc_score_delta: float = 0.0,
        cross_confidence: float = 50.0,
        derivatives_score: float = 0.0,
    ) -> float:
        """
        Compute explainable 0-100 Alpha Score from modular evidence:
        1. STRATEGY CORE EDGE:          0-35
        2. ORDERFLOW & MICROSTRUCTURE:  0-20
        3. MOMENTUM & TECHNICALS:       0-15
        4. REGIME & LIQUIDITY:          0-12
        5. BTC BETA CONTEXT:            0-10
        6. CROSS-VENUE INTELLIGENCE:    0-8
        Total Range: 0 to 100
        Missing signals yield +0 points (NOT a veto).
        Contradictory signals subtract modest points.
        """
        score = 0.0
        breakdown: Dict[str, float] = {}

        # ------------------------------------------------------------------
        # 1. Strategy Core Edge (0-35 pts)
        # ------------------------------------------------------------------
        core_score = 0.0
        feat = signal.features

        if signal.strategy == StrategyName.TURTLE_SOUP:
            sweep_depth = feat.get("sweep_depth_pct", 0.0)
            if 0.15 <= sweep_depth <= 0.80:
                core_score += 20.0
            elif sweep_depth > 0:
                core_score += 12.0
            if feat.get("sfp_confirmed", False):
                core_score += 15.0
        elif signal.strategy == StrategyName.VWAP_FVG_REVERSION:
            if feat.get("fvg_confluence", False):
                core_score += 20.0
            vwap_dev = feat.get("vwap_deviation_pct", 1.0)
            if vwap_dev <= 0.80:
                core_score += 15.0
            elif vwap_dev <= 1.20:
                core_score += 10.0
        elif signal.strategy == StrategyName.ABSORPTION:
            if feat.get("absorption_confirmed", False):
                core_score += 20.0
            vel = feat.get("velocity", 0.0)
            if vel >= 1.5:
                core_score += 15.0
            elif vel >= 1.2:
                core_score += 10.0
        elif signal.strategy == StrategyName.TREND_CONTINUATION:
            core_score += 35.0
        else:
            # Fallback structural anchor scoring
            stop_dist_pct = abs(signal.entry - signal.structural_stop) / signal.entry * 100 if signal.entry > 0 else 0.0
            if 0.20 <= stop_dist_pct <= 1.20:
                core_score += 35.0
            elif 0.10 <= stop_dist_pct <= 1.80:
                core_score += 28.0
            else:
                core_score += 18.0

        core_score = max(0.0, min(35.0, core_score))
        breakdown["core_edge"] = core_score
        score += core_score

        # ------------------------------------------------------------------
        # 2. Orderflow & Microstructure (0-20 pts)
        # ------------------------------------------------------------------
        of_score = 0.0
        obi = snapshot.orderbook_imbalance

        if signal.side == OrderSide.BUY:
            if obi >= 1.30:
                of_score += 12.0
            elif obi >= 1.10:
                of_score += 8.0
            elif obi >= 0.95:
                of_score += 4.0
            elif obi < 0.85:
                of_score -= 4.0  # Mild opposing penalty

            # CVD confirmation
            cvd_val = getattr(snapshot, "cvd", 0.0)
            if cvd_val > 0:
                of_score += 8.0
            elif cvd_val < -50000.0:
                of_score -= 3.0
        else:
            if obi <= 0.75:
                of_score += 12.0
            elif obi <= 0.90:
                of_score += 8.0
            elif obi <= 1.05:
                of_score += 4.0
            elif obi > 1.15:
                of_score -= 4.0

            # CVD confirmation
            cvd_val = getattr(snapshot, "cvd", 0.0)
            if cvd_val < 0:
                of_score += 8.0
            elif cvd_val > 50000.0:
                of_score -= 3.0

        of_score = max(-5.0, min(20.0, of_score))
        breakdown["orderflow"] = of_score
        score += of_score

        # ------------------------------------------------------------------
        # 3. Momentum & Technicals (0-15 pts)
        # ------------------------------------------------------------------
        mom_score = 0.0
        wt1 = feat.get("wt1", snapshot.wt1_15m)
        wt1_prev = feat.get("wt1_prev", wt1)
        rsi = feat.get("rsi", 50.0)

        if signal.side == OrderSide.BUY:
            if wt1 < -35.0:
                mom_score += 8.0
            elif wt1 < -15.0 or (wt1 >= wt1_prev and wt1 < 10.0):
                mom_score += 5.0
            elif wt1 > 35.0:
                mom_score -= 3.0
            else:
                mom_score += 2.0

            if 35.0 <= rsi <= 48.0:
                mom_score += 7.0
            elif 30.0 <= rsi <= 55.0:
                mom_score += 4.0
            elif rsi > 65.0:
                mom_score -= 2.0
            else:
                mom_score += 2.0
        else:
            if wt1 > 35.0:
                mom_score += 8.0
            elif wt1 > 15.0 or (wt1 <= wt1_prev and wt1 > -10.0):
                mom_score += 5.0
            elif wt1 < -35.0:
                mom_score -= 3.0
            else:
                mom_score += 2.0

            if 52.0 <= rsi <= 65.0:
                mom_score += 7.0
            elif 45.0 <= rsi <= 70.0:
                mom_score += 4.0
            elif rsi < 35.0:
                mom_score -= 2.0
            else:
                mom_score += 2.0

        mom_score = max(-4.0, min(15.0, mom_score))
        breakdown["momentum"] = mom_score
        score += mom_score

        # ------------------------------------------------------------------
        # 4. Regime & Liquidity Context (0-12 pts)
        # ------------------------------------------------------------------
        reg_score = 0.0
        regime_val = signal.regime.value if hasattr(signal.regime, "value") else str(signal.regime)

        if regime_val in ("TREND_UP", "TREND_DOWN", "RANGE"):
            reg_score += 8.0
        elif regime_val == "LOW_VOL":
            reg_score += 6.0
        elif regime_val == "UNCERTAIN":
            reg_score += 4.0  # UNCERTAIN is a lower score, not an automatic veto
        else:
            reg_score += 3.0

        if snapshot.spread_pct <= 0.04:
            reg_score += 4.0
        elif snapshot.spread_pct <= 0.08:
            reg_score += 2.0

        reg_score = max(0.0, min(12.0, reg_score))
        breakdown["regime_liquidity"] = reg_score
        score += reg_score

        # ------------------------------------------------------------------
        # 5. BTC Beta Context (0-10 pts)
        # ------------------------------------------------------------------
        btc_pts = 4.0  # Baseline neutral
        if btc_score_delta != 0.0:
            btc_pts = 4.0 + btc_score_delta
        elif snapshot.btc_state in ("BULLISH", "SELF") and signal.side == OrderSide.BUY:
            btc_pts = 8.0
        elif snapshot.btc_state in ("BEARISH", "SELF") and signal.side == OrderSide.SELL:
            btc_pts = 8.0
        elif snapshot.btc_state == "NEUTRAL":
            btc_pts = 4.0
        elif snapshot.btc_state == "BEARISH" and signal.side == OrderSide.BUY:
            btc_pts = -3.0
        elif snapshot.btc_state == "BULLISH" and signal.side == OrderSide.SELL:
            btc_pts = -3.0

        btc_pts = max(-6.0, min(10.0, btc_pts))
        breakdown["btc_beta"] = btc_pts
        score += btc_pts

        # ------------------------------------------------------------------
        # 6. Cross-Venue Intelligence & Derivatives (0-8 pts)
        # ------------------------------------------------------------------
        cross_pts = 0.0
        if cross_confidence >= 70.0:
            cross_pts += min(5.0, (cross_confidence - 50.0) / 4.0)
        elif cross_confidence <= 30.0:
            cross_pts -= min(4.0, (50.0 - cross_confidence) / 5.0)

        if signal.side == OrderSide.BUY:
            if derivatives_score >= 25.0:
                cross_pts += 3.0
            elif derivatives_score <= -25.0:
                cross_pts -= 2.0
        else:
            if derivatives_score <= -25.0:
                cross_pts += 3.0
            elif derivatives_score >= 25.0:
                cross_pts -= 2.0

        cross_pts = max(-5.0, min(8.0, cross_pts))
        breakdown["cross_venue"] = cross_pts
        score += cross_pts

        # Record breakdown into signal features for transparency
        signal.features["score_breakdown"] = breakdown

        final_score = max(0.0, min(100.0, round(score, 1)))
        return final_score


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
        if snapshot.microprice > 0 and snapshot.last_price > 0:
            micro_dev_pct = abs(snapshot.last_price - snapshot.microprice) / snapshot.last_price * 100
            if micro_dev_pct < 0.03:
                score += 15.0
            elif micro_dev_pct < 0.08:
                score += 10.0
            else:
                score += 5.0
        else:
            score += 10.0  # Neutral baseline when microprice not available

        return round(score, 1)
