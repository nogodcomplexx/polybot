"""
Realistic Research & Event-Driven Backtesting Engine.
Implements causal bar-by-bar simulation with fee schedules, spread, slippage,
50/50 TP1/TP2 split, Break-Even ratchet, time-based thesis invalidation,
and post-trade performance analytics (including Sharpe Ratio & Profit Factor).
"""

import math
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from src.bybit.models import (
    MarketSnapshot, MarketRegime, OrderSide, StrategyName, Signal
)
from src.bybit.market.candles import Candle
from src.bybit.market.volatility import calculate_atr
from src.bybit.market.vwap import calculate_vwap
from src.bybit.alpha.indicators import calculate_wavetrend, calculate_rsi
from src.bybit.regime.regime_engine import MarketRegimeEngine
from src.bybit.alpha.strategies import AlphaStrategySuite
from src.bybit.alpha.scorer import AlphaScorer
from src.bybit.config import TunableParameterRegistry


@dataclass
class BacktestMetrics:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    total_fees: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    expectancy: float = 0.0
    sharpe_ratio: float = 0.0
    strategy_breakdown: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class BybitBacktester:
    """Simulates realistic execution over historical multi-timeframe candles."""

    def __init__(
        self,
        starting_balance: float = 1000.0,
        maker_fee: float = 0.0002,      # 0.02% Bybit Maker
        taker_fee: float = 0.00055,     # 0.055% Bybit Taker
        slippage_pct: float = 0.0003,   # 0.03% slippage on market/SL exits
        risk_per_trade_pct: float = 1.0,
        default_leverage: int = 10,
        max_thesis_lifetime_sec: float = 3600.0,
        symbol: str = "BTCUSDT",
        tunable: Optional[TunableParameterRegistry] = None,
        enabled_strategies: Optional[List[str]] = None,
    ):
        self.starting_balance = starting_balance
        self.balance = starting_balance
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.slippage_pct = slippage_pct
        self.risk_per_trade_pct = risk_per_trade_pct
        self.default_leverage = default_leverage
        self.max_thesis_lifetime_sec = max_thesis_lifetime_sec
        self.symbol = symbol.upper()
        self.enabled_strategies = [s.upper() for s in enabled_strategies] if enabled_strategies else ["TURTLE_SOUP", "TREND_CONTINUATION", "ABSORPTION"]

        self.tunable = tunable or TunableParameterRegistry()
        self.regime_engine = MarketRegimeEngine()
        self.strategy_suite = AlphaStrategySuite(tunable=self.tunable)
        self.scorer = AlphaScorer(tunable=self.tunable)

        self.trades: List[Dict[str, Any]] = []

    def _build_synthetic_snapshot(self, window: List[Candle], symbol: str) -> MarketSnapshot:
        """
        Construct a causal MarketSnapshot from historical OHLCV bars.
        Approximates L2 orderbook imbalance (OBI), CVD, and velocity from intrabar price/volume action.
        """
        last_bar = window[-1]
        price = last_bar.close
        bar_range = max(last_bar.high - last_bar.low, price * 0.0001)

        # Close Location Value (CLV) in [-1.0, +1.0] measures intrabar buying vs selling pressure
        clv = ((last_bar.close - last_bar.low) - (last_bar.high - last_bar.close)) / bar_range

        # Volume expansion relative to 20-bar average
        recent_vols = [c.volume for c in window[-20:]]
        avg_vol = (sum(recent_vols) / len(recent_vols)) if recent_vols else 1.0
        vol_ratio = (last_bar.volume / avg_vol) if avg_vol > 0 else 1.0

        # Synthetic OBI: scales around 1.0 based on intrabar pressure and volume surge
        synthetic_obi = round(max(0.45, min(2.20, 1.0 + (clv * 0.45 * min(vol_ratio, 2.0)))), 3)

        # Synthetic rolling 10-bar CVD
        synthetic_cvd = 0.0
        for c in window[-10:]:
            r = max(c.high - c.low, 1e-8)
            bar_clv = ((c.close - c.low) - (c.high - c.close)) / r
            synthetic_cvd += c.volume * bar_clv

        synthetic_velocity = round(max(0.5, min(10.0, vol_ratio * 1.35)), 2)

        atr_15m = calculate_atr(window, 14)
        atr_5m = atr_15m * 0.55
        atr_1m = atr_15m * 0.25

        vwap_15m = calculate_vwap(window[-20:])
        vwap_24h = calculate_vwap(window[-96:]) if len(window) >= 96 else vwap_15m
        wt1, wt2, wt1_prev, wt_curling = calculate_wavetrend(window)
        rsi = calculate_rsi(window, 14)

        spread_pct = 0.02
        spread = price * (spread_pct / 100.0)
        best_bid = price - (spread / 2.0)
        best_ask = price + (spread / 2.0)

        regime = self.regime_engine.classify(window, atr_15m, spread_pct, synthetic_obi)

        return MarketSnapshot(
            timestamp=last_bar.close_time if last_bar.close_time > 0 else last_bar.timestamp + 900.0,
            symbol=symbol,
            last_price=price,
            mark_price=price,
            index_price=price,
            spread=spread,
            spread_pct=spread_pct,
            best_bid=best_bid,
            best_ask=best_ask,
            bid_depth=last_bar.volume * 0.5,
            ask_depth=last_bar.volume * 0.5,
            orderbook_imbalance=synthetic_obi,
            microprice=price,
            cvd=round(synthetic_cvd, 2),
            trade_velocity=synthetic_velocity,
            volume_velocity=round(last_bar.volume / 900.0, 2),
            vwap_1m=price,
            vwap_5m=vwap_15m,
            vwap_15m=vwap_15m,
            vwap_24h=vwap_24h,
            atr_1m=atr_1m,
            atr_5m=atr_5m,
            atr_15m=atr_15m,
            rsi_15m=rsi,
            wt1_15m=wt1,
            wt2_15m=wt2,
            wt1_prev=wt1_prev,
            wt_curling=wt_curling,
            btc_state="SELF" if symbol == "BTCUSDT" else "NEUTRAL",
            regime=regime,
            spoof_probability=0.10,
            orderbook_valid=True,
        )

    def run_simulation(self, candles_15m: List[Candle], symbol: Optional[str] = None) -> BacktestMetrics:
        """
        Run event-driven causal backtest across 15m candles and compute institutional metrics.
        If self.trades was pre-populated externally (e.g. unit tests) and candles_15m is empty/short,
        computes metrics directly from self.trades.
        """
        target_sym = (symbol or self.symbol).upper()

        # Run bar-by-bar simulation if historical candles are provided and trades weren't pre-injected
        if candles_15m and len(candles_15m) >= 30 and not self.trades:
            self._simulate_bars(candles_15m, target_sym)

        return self._compute_metrics()

    def _simulate_bars(self, candles_15m: List[Candle], symbol: str):
        """Step through candles causally without look-ahead bias."""
        self.balance = self.starting_balance
        active_pos: Optional[Dict[str, Any]] = None
        last_exit_ts: float = 0.0

        for i in range(25, len(candles_15m)):
            window = candles_15m[: i + 1]
            bar = candles_15m[i]
            bar_ts = bar.close_time if bar.close_time > 0 else (bar.timestamp + 900.0)

            # ----------------------------------------------------------
            # 1. Manage Open Position on Current Bar (Intrabar High/Low)
            # ----------------------------------------------------------
            if active_pos is not None:
                side: OrderSide = active_pos["side"]
                entry: float = active_pos["entry"]
                sl: float = active_pos["sl"]
                tp1: float = active_pos["tp1"]
                tp2: float = active_pos["tp2"]
                initial_qty: float = active_pos["initial_qty"]
                rem_qty: float = active_pos["rem_qty"]

                # Update MAE / MFE
                if side == OrderSide.BUY:
                    favorable = bar.high - entry
                    adverse = bar.low - entry
                else:
                    favorable = entry - bar.low
                    adverse = entry - bar.high

                active_pos["mfe"] = max(active_pos["mfe"], favorable)
                active_pos["mae"] = min(active_pos["mae"], adverse)

                # A) Conservative Stop-Loss Check First (Worst-case intrabar assumption)
                hit_sl = (bar.low <= sl) if side == OrderSide.BUY else (bar.high >= sl)
                if hit_sl:
                    # Apply adverse slippage on Taker Stop-Loss exit
                    sl_exec = sl * (1.0 - self.slippage_pct) if side == OrderSide.BUY else sl * (1.0 + self.slippage_pct)
                    rem_pnl = (sl_exec - entry) * rem_qty if side == OrderSide.BUY else (entry - sl_exec) * rem_qty
                    exit_fee = (sl_exec * rem_qty) * self.taker_fee

                    total_gross = active_pos["realized_gross"] + rem_pnl
                    total_fees = active_pos["fees_paid"] + exit_fee
                    net_pnl = total_gross - total_fees

                    self.trades.append({
                        "symbol": symbol,
                        "side": side.value,
                        "strategy": active_pos["strategy"],
                        "regime": active_pos["regime"],
                        "entry": entry,
                        "exit_price": round(sl_exec, 4),
                        "pnl": round(net_pnl, 2),
                        "fees": round(total_fees, 2),
                        "mae": round(active_pos["mae"], 4),
                        "mfe": round(active_pos["mfe"], 4),
                        "hold_time": round(bar_ts - active_pos["entry_time"], 1),
                        "exit_reason": "Stop Loss Hit (Runner BE)" if active_pos["tp1_filled"] else "Stop Loss Hit",
                    })
                    active_pos = None
                    last_exit_ts = bar_ts
                    continue

                # B) TP1 Check (50% Partial Close @ Maker Fee + Move SL to BE)
                if not active_pos["tp1_filled"]:
                    hit_tp1 = (bar.high >= tp1) if side == OrderSide.BUY else (bar.low <= tp1)
                    if hit_tp1:
                        tp1_qty = initial_qty * 0.50
                        pnl1 = (tp1 - entry) * tp1_qty if side == OrderSide.BUY else (entry - tp1) * tp1_qty
                        fee1 = (tp1 * tp1_qty) * self.maker_fee

                        active_pos["tp1_filled"] = True
                        active_pos["rem_qty"] = initial_qty - tp1_qty
                        active_pos["realized_gross"] += pnl1
                        active_pos["fees_paid"] += fee1

                        # Move Stop Loss to Break-Even + 0.05% buffer
                        be_buffer = entry * 0.0005
                        active_pos["sl"] = (entry + be_buffer) if side == OrderSide.BUY else (entry - be_buffer)
                        active_pos["be_moved"] = True

                # C) TP2 Check (Remaining 50% Runner @ Maker Fee)
                if active_pos["tp1_filled"]:
                    hit_tp2 = (bar.high >= tp2) if side == OrderSide.BUY else (bar.low <= tp2)
                    if hit_tp2:
                        rem_qty = active_pos["rem_qty"]
                        pnl2 = (tp2 - entry) * rem_qty if side == OrderSide.BUY else (entry - tp2) * rem_qty
                        fee2 = (tp2 * rem_qty) * self.maker_fee

                        total_gross = active_pos["realized_gross"] + pnl2
                        total_fees = active_pos["fees_paid"] + fee2
                        net_pnl = total_gross - total_fees

                        self.trades.append({
                            "symbol": symbol,
                            "side": side.value,
                            "strategy": active_pos["strategy"],
                            "regime": active_pos["regime"],
                            "entry": entry,
                            "exit_price": round(tp2, 4),
                            "pnl": round(net_pnl, 2),
                            "fees": round(total_fees, 2),
                            "mae": round(active_pos["mae"], 4),
                            "mfe": round(active_pos["mfe"], 4),
                            "hold_time": round(bar_ts - active_pos["entry_time"], 1),
                            "exit_reason": "TP2 Target Reached",
                        })
                        active_pos = None
                        last_exit_ts = bar_ts
                        continue

                # D) Break-Even Ratchet if MFE >= 0.50R before TP1
                if not active_pos["be_moved"]:
                    if favorable >= active_pos["initial_stop_dist"] * 0.50:
                        be_buffer = entry * 0.0005
                        active_pos["sl"] = (entry + be_buffer) if side == OrderSide.BUY else (entry - be_buffer)
                        active_pos["be_moved"] = True

                # E) Time-Based Thesis Invalidation (if trade hasn't hit TP1 within max_thesis_lifetime_sec)
                hold_sec = bar_ts - active_pos["entry_time"]
                if self.max_thesis_lifetime_sec > 0 and hold_sec >= self.max_thesis_lifetime_sec and not active_pos["tp1_filled"]:
                    exit_p = bar.close
                    rem_pnl = (exit_p - entry) * rem_qty if side == OrderSide.BUY else (entry - exit_p) * rem_qty
                    exit_fee = (exit_p * rem_qty) * self.maker_fee

                    total_gross = active_pos["realized_gross"] + rem_pnl
                    total_fees = active_pos["fees_paid"] + exit_fee
                    net_pnl = total_gross - total_fees

                    self.trades.append({
                        "symbol": symbol,
                        "side": side.value,
                        "strategy": active_pos["strategy"],
                        "regime": active_pos["regime"],
                        "entry": entry,
                        "exit_price": round(exit_p, 4),
                        "pnl": round(net_pnl, 2),
                        "fees": round(total_fees, 2),
                        "mae": round(active_pos["mae"], 4),
                        "mfe": round(active_pos["mfe"], 4),
                        "hold_time": round(hold_sec, 1),
                        "exit_reason": "Time-Based Thesis Invalidation",
                    })
                    active_pos = None
                    last_exit_ts = bar_ts
                    continue

            # ----------------------------------------------------------
            # 2. Scan for New Entry Signal When FLAT
            # ----------------------------------------------------------
            if active_pos is None:
                # Enforce 60s symbol cooldown
                if (bar_ts - last_exit_ts) < 60.0:
                    continue

                snapshot = self._build_synthetic_snapshot(window, symbol)
                regime = snapshot.regime

                raw_signals: List[Signal] = []
                if "TURTLE_SOUP" in self.enabled_strategies and self.regime_engine.is_strategy_allowed(StrategyName.TURTLE_SOUP, regime):
                    s1 = self.strategy_suite.evaluate_turtle_soup(snapshot, window)
                    if s1:
                        raw_signals.append(s1)

                if "VWAP_FVG_REVERSION" in self.enabled_strategies and self.regime_engine.is_strategy_allowed(StrategyName.VWAP_FVG_REVERSION, regime):
                    s2 = self.strategy_suite.evaluate_vwap_fvg_reversion(snapshot, window)
                    if s2:
                        raw_signals.append(s2)

                if "ABSORPTION" in self.enabled_strategies and self.regime_engine.is_strategy_allowed(StrategyName.ABSORPTION, regime):
                    s3 = self.strategy_suite.evaluate_absorption(snapshot)
                    if s3:
                        raw_signals.append(s3)

                if "TREND_CONTINUATION" in self.enabled_strategies and self.regime_engine.is_strategy_allowed(StrategyName.TREND_CONTINUATION, regime):
                    s4 = self.strategy_suite.evaluate_trend_continuation(snapshot, window)
                    if s4:
                        raw_signals.append(s4)

                valid_candidates: List[Signal] = []
                for sig in raw_signals:
                    # Counter-trend Long gate (matching PolicyEngine check 7b)
                    if sig.side == OrderSide.BUY and regime in (MarketRegime.TREND_DOWN, MarketRegime.HIGH_VOL):
                        continue

                    sig.alpha_score = self.scorer.score_signal(sig, snapshot)
                    sig.expected_ev = self.scorer.calculate_ev(sig, snapshot)
                    exec_score = self.scorer.evaluate_execution_quality(sig, snapshot)
                    sig.features["execution_score"] = exec_score

                    if (
                        sig.alpha_score >= self.scorer.MIN_ALPHA_SCORE
                        and sig.expected_ev >= self.scorer.MIN_EXPECTED_EV
                        and exec_score >= self.scorer.MIN_EXECUTION_SCORE
                    ):
                        valid_candidates.append(sig)

                if valid_candidates:
                    valid_candidates.sort(
                        key=lambda s: s.expected_ev * s.features.get("execution_score", 50.0) * s.alpha_score,
                        reverse=True,
                    )
                    best = valid_candidates[0]
                    stop_dist = abs(best.entry - best.structural_stop)
                    if stop_dist <= 0:
                        continue

                    # Risk-based position sizing (matching PositionSizer)
                    score_scalar = 0.75 if best.alpha_score < 75.0 else (1.0 if best.alpha_score < 85.0 else 1.25)
                    eff_risk_pct = self.risk_per_trade_pct * score_scalar * best.confidence
                    risk_usd = self.starting_balance * (eff_risk_pct / 100.0)
                    qty = risk_usd / stop_dist

                    entry_notional = qty * best.entry
                    entry_fee = entry_notional * self.maker_fee

                    active_pos = {
                        "side": best.side,
                        "strategy": best.strategy.value,
                        "regime": best.regime.value,
                        "entry": best.entry,
                        "sl": best.structural_stop,
                        "tp1": best.tp1,
                        "tp2": best.tp2,
                        "initial_stop_dist": stop_dist,
                        "initial_qty": qty,
                        "rem_qty": qty,
                        "entry_time": bar_ts,
                        "tp1_filled": False,
                        "be_moved": False,
                        "realized_gross": 0.0,
                        "fees_paid": entry_fee,
                        "mae": 0.0,
                        "mfe": 0.0,
                    }

    def _compute_metrics(self) -> BacktestMetrics:
        """Compute comprehensive performance and risk-adjusted metrics from self.trades."""
        self.balance = self.starting_balance
        gross_profit = 0.0
        gross_loss = 0.0
        total_fees = 0.0
        peak_balance = self.starting_balance
        max_dd = 0.0
        returns_pct: List[float] = []
        strat_stats: Dict[str, Dict[str, Any]] = {}

        for t in self.trades:
            pnl = float(t.get("pnl", 0.0))
            fee = float(t.get("fees", 0.0))
            strat = str(t.get("strategy", "UNKNOWN"))

            prev_bal = max(self.balance, 1.0)
            returns_pct.append(pnl / prev_bal)

            self.balance += pnl
            total_fees += fee

            if pnl > 0:
                gross_profit += pnl
            else:
                gross_loss += abs(pnl)

            if self.balance > peak_balance:
                peak_balance = self.balance
            dd = (peak_balance - self.balance) / peak_balance * 100.0 if peak_balance > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

            st = strat_stats.setdefault(strat, {"trades": 0, "wins": 0, "pnl": 0.0, "fees": 0.0})
            st["trades"] += 1
            if pnl > 0:
                st["wins"] += 1
            st["pnl"] = round(st["pnl"] + pnl, 2)
            st["fees"] = round(st["fees"] + fee, 2)

        total_trades = len(self.trades)
        wins = sum(1 for t in self.trades if float(t.get("pnl", 0.0)) > 0)
        losses = sum(1 for t in self.trades if float(t.get("pnl", 0.0)) < 0)

        win_rate = (wins / total_trades) if total_trades > 0 else 0.0
        pf = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
        exp = ((gross_profit - gross_loss) / total_trades) if total_trades > 0 else 0.0

        # Annualized Sharpe Ratio (assuming ~5-10 trades/day -> sqrt(365 * trades_per_day) or per-trade annualization)
        sharpe = 0.0
        if len(returns_pct) >= 2:
            mean_ret = sum(returns_pct) / len(returns_pct)
            variance = sum((r - mean_ret) ** 2 for r in returns_pct) / (len(returns_pct) - 1)
            std_ret = math.sqrt(variance)
            if std_ret > 1e-9:
                sharpe = (mean_ret / std_ret) * math.sqrt(min(total_trades, 365))

        for st in strat_stats.values():
            st["win_rate_pct"] = round((st["wins"] / st["trades"]) * 100.0, 1) if st["trades"] > 0 else 0.0

        return BacktestMetrics(
            total_trades=total_trades,
            winning_trades=wins,
            losing_trades=losses,
            win_rate=round(win_rate, 3),
            total_pnl=round(self.balance - self.starting_balance, 2),
            total_fees=round(total_fees, 2),
            profit_factor=round(pf, 2),
            max_drawdown_pct=round(max_dd, 2),
            expectancy=round(exp, 2),
            sharpe_ratio=round(sharpe, 2),
            strategy_breakdown=strat_stats,
        )
