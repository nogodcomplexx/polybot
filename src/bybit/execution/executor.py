"""
Execution Engine (Brain 3).
Implements Section 63 Minimal Execution Algorithm, Post-Only maker execution,
queue-aware entry, protection-first bracket initialization, partial-fill handling,
and time-based thesis invalidation.
"""

import asyncio
import logging
import time
import uuid
from typing import Dict, Any, List, Optional

from src.bybit.models import (
    ExecutionPlan, ActivePosition, PositionState, OrderSide,
    OrderType, FailureType
)
from src.bybit.exchange.bybit_client import BybitClient
from src.bybit.exchange.account import BybitAccountRepository
from src.bybit.exchange.instruments import BybitInstrumentRepository
from src.bybit.state.state_machine import PositionStateMachine
from src.bybit.execution.execution_policy import ExecutionPolicyEngine
from src.bybit.storage.database import BybitDatabase

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """Production execution orchestrator supporting live and high-fidelity paper trading."""

    def __init__(
        self,
        client: BybitClient,
        account: BybitAccountRepository,
        instrument_repo: BybitInstrumentRepository,
        state_machine: PositionStateMachine,
        exec_policy: ExecutionPolicyEngine,
        db: BybitDatabase,
        mode: str = "paper",
        leverage: int = 10,
        ws: Optional[Any] = None,
        max_thesis_lifetime: float = 3600.0,
        min_take_profit_usd: float = 50.0,
        breakeven_trigger_usd: float = 25.0,
    ):
        self.client = client
        self.account = account
        self.instrument_repo = instrument_repo
        self.state_machine = state_machine
        self.exec_policy = exec_policy
        self.db = db
        self.mode = mode.lower()
        self.is_paper = (self.mode in ("paper", "shadow"))
        self.default_leverage = leverage
        self.ws = ws
        self.max_thesis_lifetime = max_thesis_lifetime
        self.min_take_profit_usd = min_take_profit_usd
        self.breakeven_trigger_usd = breakeven_trigger_usd
        self.brain_trace: Optional[Any] = None

        # Execution deduplication state
        self._processed_exec_ids: set[str] = set()

        # Paper trading simulation state
        self.paper_balance: float = account.total_equity
        self.paper_positions: Dict[str, ActivePosition] = {}
        # Live trading state
        self.live_positions: Dict[str, ActivePosition] = {}

    async def execute_plan(self, plan: ExecutionPlan, strategy_name: Any, regime: Any) -> bool:
        """
        Execute Section 63 Minimal Execution Algorithm:
        verify state -> check no duplicate -> place Post-Only entry ->
        wait fill -> establish hard stop -> place TP1/TP2 -> transition ACTIVE.
        """
        # 1. Verification of state & duplication
        if self.state_machine.state != PositionState.FLAT:
            logger.warning(f"Cannot execute plan: state machine is {self.state_machine.state.value}")
            return False

        if not self.is_paper:
            if len(self.live_positions) > 0 or len(self.account.cached_positions) > 0:
                logger.warning("Cannot execute plan: live desk or exchange already has an active position")
                return False
        else:
            if len(self.paper_positions) > 0:
                logger.warning("Cannot execute plan: paper desk already has an active position")
                return False

        await self.state_machine.transition_to(PositionState.ENTRY_PENDING, f"Placing {plan.side.value} entry on {plan.symbol}")

        # Deterministic client order ID
        client_order_id = plan.client_order_id or f"ord_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
        plan.client_order_id = client_order_id

        # 2. Paper Trading Execution
        if self.is_paper:
            return await self._execute_paper_entry(plan, strategy_name, regime)

        # 3. Live Bybit V5 Execution
        trade_leverage = getattr(plan, "leverage", self.default_leverage) or self.default_leverage
        try:
            # Set leverage & margin mode if needed
            await self.client.set_margin_mode(plan.symbol, is_isolated=True, leverage=trade_leverage)
            await self.client.set_leverage(plan.symbol, trade_leverage)

            rounded_qty = self.instrument_repo.round_qty(plan.symbol, plan.quantity) if hasattr(self.instrument_repo, "round_qty") else plan.quantity
            try:
                rounded_entry = self.instrument_repo.round_price(plan.symbol, plan.entry_price)
                if not isinstance(rounded_entry, (int, float)):
                    rounded_entry = float(plan.entry_price)
            except Exception:
                rounded_entry = float(plan.entry_price)

            try:
                rounded_sl = self.instrument_repo.round_price(plan.symbol, plan.stop_price)
                if not isinstance(rounded_sl, (int, float)):
                    rounded_sl = float(plan.stop_price)
            except Exception:
                rounded_sl = float(plan.stop_price)

            try:
                rounded_tp1 = self.instrument_repo.round_price(plan.symbol, plan.tp1_price)
                if not isinstance(rounded_tp1, (int, float)):
                    rounded_tp1 = float(plan.tp1_price)
            except Exception:
                rounded_tp1 = float(plan.tp1_price)

            try:
                rounded_tp2 = self.instrument_repo.round_price(plan.symbol, plan.tp2_price)
                if not isinstance(rounded_tp2, (int, float)):
                    rounded_tp2 = float(plan.tp2_price)
            except Exception:
                rounded_tp2 = float(plan.tp2_price)

            # Place Post-Only Maker Limit Entry Order with server-side SL attached (TP managed via Reduce-Only brackets)
            res = await self.client.place_order(
                symbol=plan.symbol,
                side=plan.side.value,
                qty=rounded_qty,
                price=rounded_entry,
                order_type="Limit",
                time_in_force="PostOnly",
                order_link_id=client_order_id,
                stop_loss=rounded_sl,
            )

            if res.get("retCode") != 0:
                ret_msg = res.get("retMsg", "Entry rejected by exchange")
                logger.error(f"Bybit entry order rejected: {ret_msg}")
                await self.state_machine.transition_to(PositionState.FLAT, f"Bybit rejected: {ret_msg}")
                return False

            order_id = res.get("result", {}).get("orderId", "")
            logger.info(f"✅ [LIVE ENTRY SENT] {plan.side.value} {rounded_qty} {plan.symbol} @ ${rounded_entry:.2f} (id={order_id})")

            # Track live pending position
            pos = ActivePosition(
                symbol=plan.symbol,
                side=plan.side,
                size=rounded_qty,
                entry_price=rounded_entry,
                stop_price=rounded_sl,
                tp1_price=rounded_tp1,
                tp2_price=rounded_tp2,
                leverage=trade_leverage,
                strategy=strategy_name,
                regime=regime,
                client_order_id=client_order_id,
                entry_time=time.time(),
                state=PositionState.ENTRY_PENDING,
                filled_quantity=0.0,
                notional=rounded_qty * rounded_entry,
                max_thesis_lifetime=self.max_thesis_lifetime,
            )
            self.live_positions[plan.symbol] = pos
            self.state_machine.active_position = pos
            return True

        except Exception as e:
            logger.error(f"Live execution error: {e}", exc_info=True)
            await self.state_machine.transition_to(PositionState.FLAT, "Execution error")
            return False

    async def _execute_paper_entry(self, plan: ExecutionPlan, strategy_name: Any, regime: Any) -> bool:
        """Simulate Post-Only queue matching and initialize protective brackets."""
        is_buy = (plan.side == OrderSide.BUY or plan.side.value == "Buy")
        best_bid = getattr(plan, "best_bid", 0.0)
        best_ask = getattr(plan, "best_ask", 0.0)

        # Fallback to public WS orderbook if available
        if (best_bid <= 0 or best_ask <= 0) and self.ws and hasattr(self.ws, "get_orderbook_summary"):
            ob_summary = self.ws.get_orderbook_summary(plan.symbol)
            if ob_summary and ob_summary.get("is_valid", False):
                best_bid = ob_summary.get("best_bid", 0.0)
                best_ask = ob_summary.get("best_ask", 0.0)

        # 1. PostOnly Spread Crossing Invariant Check
        if plan.post_only:
            if is_buy and best_ask > 0 and plan.entry_price >= best_ask:
                logger.warning(
                    f"❌ [POST-ONLY REJECT] Paper Buy order @ ${plan.entry_price:.4f} crosses best ask ${best_ask:.4f}"
                )
                await self.state_machine.transition_to(PositionState.FLAT, "Post-only crossing rejection")
                return False
            if not is_buy and best_bid > 0 and plan.entry_price <= best_bid:
                logger.warning(
                    f"❌ [POST-ONLY REJECT] Paper Sell order @ ${plan.entry_price:.4f} crosses best bid ${best_bid:.4f}"
                )
                await self.state_machine.transition_to(PositionState.FLAT, "Post-only crossing rejection")
                return False

        # 2. Queue order in ENTRY_PENDING (or instant fill if MARKET)
        is_market = (plan.order_type == OrderType.MARKET)
        trade_leverage = getattr(plan, "leverage", self.default_leverage) or self.default_leverage
        pos = ActivePosition(
            symbol=plan.symbol,
            side=plan.side,
            size=plan.quantity,
            entry_price=plan.entry_price,
            stop_price=plan.stop_price,
            tp1_price=plan.tp1_price,
            tp2_price=plan.tp2_price,
            leverage=trade_leverage,
            strategy=strategy_name,
            regime=regime,
            client_order_id=plan.client_order_id,
            entry_time=time.time(),
            state=PositionState.ACTIVE if is_market else PositionState.ENTRY_PENDING,
            filled_quantity=plan.quantity if is_market else 0.0,
            notional=plan.quantity * plan.entry_price,
            ttl_seconds=plan.ttl_seconds,
            max_thesis_lifetime=self.max_thesis_lifetime,
        )

        self.paper_positions[plan.symbol] = pos
        self.state_machine.active_position = pos

        if is_market:
            taker_fee = pos.notional * 0.00055
            self.paper_balance -= taker_fee
            pos.entry_fee = taker_fee
            pos.fees_paid = taker_fee
            logger.info(
                f"📝 [PAPER MARKET FILL] {plan.side.value} {plan.quantity} {plan.symbol} @ ${plan.entry_price:.2f} "
                f"(Taker fee: ${taker_fee:.4f}, Hard SL: ${plan.stop_price:.2f}, TP1: ${plan.tp1_price:.2f}, TP2: ${plan.tp2_price:.2f})"
            )
            await self.state_machine.transition_to(PositionState.PROTECTION_PENDING, "Paper market order filled")
            await self.state_machine.transition_to(PositionState.ACTIVE, "Protective brackets established")
            return True
        else:
            logger.info(
                f"📝 [PAPER ENTRY QUEUED] {plan.side.value} {plan.quantity} {plan.symbol} @ ${plan.entry_price:.2f} "
                f"(TTL: {plan.ttl_seconds}s, Hard SL: ${plan.stop_price:.2f}, TP1: ${plan.tp1_price:.2f}, TP2: ${plan.tp2_price:.2f})"
            )
            # State machine is already in ENTRY_PENDING from execute_plan
            return True

    def _build_closed_record(
        self,
        pos: ActivePosition,
        exit_price: float,
        exit_reason: str,
        attribution: str,
        status: str,
        gross_pnl: float,
        is_maker_exit: bool = False,
        exact_fees: Optional[float] = None,
        exact_net_pnl: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Construct authoritative closed trade record with net P&L and fee accounting."""
        now = time.time()
        entry_notional = pos.entry_price * pos.size
        exit_notional = exit_price * pos.size

        # Fee accounting: Prioritize exact fees from Bybit API or accumulated WebSocket fills
        if exact_fees is not None:
            total_fees = round(exact_fees, 2)
        elif getattr(pos, "fees_paid", 0.0) > 0 and (getattr(pos, "exit_fee", 0.0) > 0 or pos.fees_paid > (getattr(pos, "entry_fee", 0.0) + 1e-4)):
            total_fees = round(pos.fees_paid, 2)
        else:
            entry_fee = getattr(pos, "entry_fee", 0.0) or (entry_notional * 0.0002)
            exit_fee = getattr(pos, "exit_fee", 0.0) or (exit_notional * (0.0002 if is_maker_exit else 0.00055))
            total_fees = round(entry_fee + exit_fee, 2)

        if exact_net_pnl is not None:
            net_pnl = round(exact_net_pnl, 2)
        else:
            net_pnl = round(gross_pnl - total_fees, 2)
        net_pnl_pct = round((net_pnl / entry_notional) * 100 * pos.leverage, 2) if entry_notional > 0 else 0.0

        return {
            "id": pos.client_order_id,
            "timestamp": now,
            "symbol": pos.symbol,
            "side": pos.side.value if hasattr(pos.side, "value") else str(pos.side),
            "strategy": pos.strategy.value if hasattr(pos.strategy, "value") else str(pos.strategy),
            "regime": pos.regime.value if hasattr(pos.regime, "value") else str(pos.regime),
            "entry": pos.entry_price,
            "exit_price": exit_price,
            "stop_price": pos.stop_price,
            "tp1": pos.tp1_price,
            "tp2": pos.tp2_price,
            "quantity": pos.size,
            "leverage": pos.leverage,
            "fees": total_fees,
            "pnl": net_pnl,
            "pnl_pct": net_pnl_pct,
            "mae": round(pos.mae, 2),
            "mfe": round(pos.mfe, 2),
            "hold_time": round(now - pos.entry_time, 1),
            "exit_reason": exit_reason,
            "attribution": attribution,
            "status": "WIN" if net_pnl >= 0 else "LOSS",
        }

    async def update_positions(self, latest_prices: Dict[str, float]) -> List[Dict[str, Any]]:
        """
        Monitor active positions against live mark/last prices,
        evaluate TP1, TP2, stop loss, time-based invalidation, and damage control.
        """
        if self.is_paper:
            return await self._update_paper_positions(latest_prices)
        else:
            return await self._update_live_positions(latest_prices)

    async def _update_paper_positions(self, latest_prices: Dict[str, float]) -> List[Dict[str, Any]]:
        closed_records = []
        now = time.time()

        for symbol, pos in list(self.paper_positions.items()):
            cur_price = latest_prices.get(symbol, 0.0)
            if cur_price <= 0:
                continue
            pos.mark_price = cur_price

            side = pos.side
            entry = pos.entry_price
            sl = pos.stop_price
            tp1 = pos.tp1_price
            tp2 = pos.tp2_price
            size = pos.size

            # ----------------------------------------------------------
            # 0. Pending Paper Order Queue Matching & TTL Invalidation
            # ----------------------------------------------------------
            if pos.state == PositionState.ENTRY_PENDING:
                ttl = getattr(pos, "ttl_seconds", 45.0)
                if now - pos.entry_time > ttl:
                    logger.info(f"⏳ [PAPER TTL EXPIRED] Order for {symbol} expired unfilled after {now - pos.entry_time:.1f}s")
                    self.paper_positions.pop(symbol, None)
                    self.state_machine.active_position = None
                    await self.state_machine.transition_to(PositionState.FLAT, "Paper entry TTL expired")
                    continue

                # Limit fill condition:
                # Buy limit fills when market trades down to or through entry
                # Sell limit fills when market trades up to or through entry
                can_fill = (cur_price <= entry) if side == OrderSide.BUY else (cur_price >= entry)
                if can_fill:
                    pos.filled_quantity = size
                    maker_fee = pos.notional * 0.0002  # 0.02% VIP0 maker fee
                    self.paper_balance -= maker_fee
                    pos.entry_fee = maker_fee
                    pos.fees_paid = maker_fee
                    pos.entry_time = now  # Reset entry time to fill timestamp
                    await self.state_machine.transition_to(PositionState.PROTECTION_PENDING, "Paper limit fill received")
                    pos.state = PositionState.ACTIVE
                    await self.state_machine.transition_to(PositionState.ACTIVE, "Paper brackets established")
                    logger.info(
                        f"📝 [PAPER FILL MATCHED] {side.value} {size} {symbol} filled @ ${entry:.2f} "
                        f"(Maker fee: ${maker_fee:.4f}, Hard SL: ${sl:.2f}, TP1: ${tp1:.2f}, TP2: ${tp2:.2f})"
                    )
                continue

            # Track MAE / MFE
            pnl_delta = (cur_price - entry) if side == OrderSide.BUY else (entry - cur_price)
            if pnl_delta > pos.mfe:
                pos.mfe = pnl_delta
                pos.time_to_mfe = now - pos.entry_time
            if pnl_delta < pos.mae:
                pos.mae = pnl_delta
                pos.time_to_mae = now - pos.entry_time

            # Calculate current gross trade profit
            if pos.tp1_filled:
                pnl1 = (tp1 - entry) * (size * 0.5) if side == OrderSide.BUY else (entry - tp1) * (size * 0.5)
                rem_pnl = (cur_price - entry) * (size * 0.5) if side == OrderSide.BUY else (entry - cur_price) * (size * 0.5)
                current_trade_gross_pnl = pnl1 + rem_pnl
            else:
                current_trade_gross_pnl = (cur_price - entry) * size if side == OrderSide.BUY else (entry - cur_price) * size

            # ----------------------------------------------------------
            # 0. Break-Even Ratchet Protection (e.g. +$25 profit halfway to target)
            # ----------------------------------------------------------
            if self.breakeven_trigger_usd > 0 and current_trade_gross_pnl >= self.breakeven_trigger_usd and not getattr(pos, "be_moved", False):
                be_buffer = entry * 0.0005
                new_stop = (entry + be_buffer) if side == OrderSide.BUY else (entry - be_buffer)
                if self.state_machine.validate_stop_update(pos.stop_price, new_stop, is_long=(side == OrderSide.BUY)):
                    pos.stop_price = new_stop
                    pos.be_moved = True
                    logger.info(f"🛡️ [PAPER BREAK-EVEN RATIFIED] {symbol} profit reaches ${current_trade_gross_pnl:.2f} (>= ${self.breakeven_trigger_usd:.2f}). Stop moved to ${new_stop:.4f}")
                    if self.brain_trace:
                        self.brain_trace.record_step(
                            symbol=symbol,
                            stage="BREAK_EVEN",
                            status="PASS",
                            message=f"Profit reaches ${current_trade_gross_pnl:.2f} (>= ${self.breakeven_trigger_usd:.2f}). Stop locked at Break-Even (${new_stop:.4f})"
                        )

            # ----------------------------------------------------------
            # 0.5 Target Profit Harvester ($50+ Guaranteed Profit Lock)
            # ----------------------------------------------------------
            if self.min_take_profit_usd > 0 and current_trade_gross_pnl >= self.min_take_profit_usd:
                self.paper_positions.pop(symbol, None)
                closed_trade = self._build_closed_record(
                    pos=pos,
                    exit_price=cur_price,
                    exit_reason=f"Target Profit Reached (${current_trade_gross_pnl:+.2f} >= ${self.min_take_profit_usd:.0f})",
                    attribution=FailureType.NORMAL_WIN.value,
                    status="WIN",
                    gross_pnl=current_trade_gross_pnl,
                    is_maker_exit=False,
                )
                already_credited = (tp1 - entry) * (size * 0.5) if (pos.tp1_filled and side == OrderSide.BUY) else (
                    (entry - tp1) * (size * 0.5) if pos.tp1_filled else 0.0
                )
                entry_fee = getattr(pos, "entry_fee", 0.0) or (pos.notional * 0.0002)
                self.paper_balance += (closed_trade["pnl"] + entry_fee - already_credited)
                closed_records.append(closed_trade)
                await self.state_machine.transition_to(PositionState.CLOSED, f"Target Profit ${self.min_take_profit_usd:.0f} Reached")
                await self.state_machine.transition_to(PositionState.FLAT, "Resetting desk to FLAT")
                logger.info(f"💰 [TARGET PROFIT HARVESTED] {side.value} {symbol} closed @ ${cur_price:.4f} (Net PnL: ${closed_trade['pnl']:+.2f}, Gross: ${current_trade_gross_pnl:+.2f}, Fees: ${closed_trade['fees']:.2f})")
                if self.brain_trace:
                    self.brain_trace.record_step(
                        symbol=symbol,
                        stage="TARGET_PROFIT",
                        status="PASS",
                        message=f"Target Profit Harvested: {side.value} {symbol} closed @ ${cur_price:.4f} (Net: ${closed_trade['pnl']:+.2f}, Gross: ${current_trade_gross_pnl:+.2f})"
                    )
                continue

            # ----------------------------------------------------------
            # 1. Stop Loss Trigger
            # ----------------------------------------------------------
            hit_sl = (cur_price <= sl) if side == OrderSide.BUY else (cur_price >= sl)
            if hit_sl:
                if pos.tp1_filled:
                    gross_pnl = ((tp1 - entry) * (size * 0.5) + (sl - entry) * (size * 0.5)) if side == OrderSide.BUY else (
                        (entry - tp1) * (size * 0.5) + (entry - sl) * (size * 0.5)
                    )
                else:
                    gross_pnl = (sl - entry) * size if side == OrderSide.BUY else (entry - sl) * size

                self.paper_positions.pop(symbol, None)
                closed_trade = self._build_closed_record(
                    pos=pos,
                    exit_price=sl,
                    exit_reason="Stop Loss Hit",
                    attribution=FailureType.NORMAL_LOSS.value,
                    status="LOSS",
                    gross_pnl=gross_pnl,
                    is_maker_exit=False,
                )
                already_credited = (tp1 - entry) * (size * 0.5) if (pos.tp1_filled and side == OrderSide.BUY) else (
                    (entry - tp1) * (size * 0.5) if pos.tp1_filled else 0.0
                )
                entry_fee = getattr(pos, "entry_fee", 0.0) or (pos.notional * 0.0002)
                self.paper_balance += (closed_trade["pnl"] + entry_fee - already_credited)
                closed_records.append(closed_trade)
                await self.state_machine.transition_to(PositionState.CLOSED, "SL Hit")
                await self.state_machine.transition_to(PositionState.FLAT, "Resetting desk to FLAT")
                logger.info(f"🛑 [PAPER SL HIT] {side.value} {symbol} closed @ ${sl:.2f} (Net PnL: ${closed_trade['pnl']:+.2f}, Fees: ${closed_trade['fees']:.2f})")
                continue

            # ----------------------------------------------------------
            # 2. TP1 Trigger (+1.5% to +2.0%, R:R >= 1.5)
            # ----------------------------------------------------------
            hit_tp1 = (cur_price >= tp1) if side == OrderSide.BUY else (cur_price <= tp1)
            if hit_tp1 and not pos.tp1_filled:
                pos.tp1_filled = True
                realized_qty = size * 0.50
                pnl1 = (tp1 - entry) * realized_qty if side == OrderSide.BUY else (entry - tp1) * realized_qty
                self.paper_balance += pnl1

                # Move Stop to Entry + 0.05% fee/rebate buffer (Section 25/50)
                be_buffer = entry * 0.0005
                new_stop = (entry + be_buffer) if side == OrderSide.BUY else (entry - be_buffer)
                if self.state_machine.validate_stop_update(pos.stop_price, new_stop, is_long=(side == OrderSide.BUY)):
                    pos.stop_price = new_stop

                await self.state_machine.transition_to(PositionState.TP1_FILLED, "TP1 reached (50% closed)")
                await self.state_machine.transition_to(PositionState.PROTECTED_RUNNER, "Stop moved to Breakeven + buffer")
                logger.info(f"🎯 [PAPER TP1 HIT] 50% closed @ ${tp1:.2f} (Realized: ${pnl1:+.2f}). Stop moved to ${new_stop:.2f}")

            # ----------------------------------------------------------
            # 3. TP2 Trigger (Runner Target, R:R >= 2.8)
            # ----------------------------------------------------------
            hit_tp2 = (cur_price >= tp2) if side == OrderSide.BUY else (cur_price <= tp2)
            if hit_tp2 and pos.tp1_filled:
                total_gross_pnl = ((tp1 - entry) * (size * 0.5) + (tp2 - entry) * (size * 0.5)) if side == OrderSide.BUY else (
                    (entry - tp1) * (size * 0.5) + (entry - tp2) * (size * 0.5)
                )
                self.paper_positions.pop(symbol, None)
                closed_trade = self._build_closed_record(
                    pos=pos,
                    exit_price=tp2,
                    exit_reason="TP2 Target Reached",
                    attribution=FailureType.NORMAL_WIN.value,
                    status="WIN",
                    gross_pnl=total_gross_pnl,
                    is_maker_exit=True,
                )
                already_credited = (tp1 - entry) * (size * 0.5) if side == OrderSide.BUY else (entry - tp1) * (size * 0.5)
                entry_fee = getattr(pos, "entry_fee", 0.0) or (pos.notional * 0.0002)
                self.paper_balance += (closed_trade["pnl"] + entry_fee - already_credited)
                closed_records.append(closed_trade)
                await self.state_machine.transition_to(PositionState.CLOSED, "TP2 Hit")
                await self.state_machine.transition_to(PositionState.FLAT, "Desk reset to FLAT")
                logger.info(f"🏆 [PAPER TP2 FULL TARGET] {side.value} {symbol} closed @ ${tp2:.2f} (Net PnL: ${closed_trade['pnl']:+.2f}, Fees: ${closed_trade['fees']:.2f})")
                continue

            # ----------------------------------------------------------
            # 4. Time-Based Thesis Invalidation (Section 32/53)
            # ----------------------------------------------------------
            trade_duration = now - pos.entry_time
            if pos.max_thesis_lifetime > 0 and trade_duration > pos.max_thesis_lifetime and not pos.tp1_filled:
                logger.warning(f"⏳ Time-based thesis invalidation for {symbol} ({trade_duration:.0f}s > {pos.max_thesis_lifetime}s)")
                # Proactive close
                gross_pnl = (cur_price - entry) * size if side == OrderSide.BUY else (entry - cur_price) * size
                self.paper_positions.pop(symbol, None)

                closed_trade = self._build_closed_record(
                    pos=pos,
                    exit_price=cur_price,
                    exit_reason="Time-Based Thesis Invalidation",
                    attribution=FailureType.NORMAL_LOSS.value if gross_pnl < 0 else FailureType.NORMAL_WIN.value,
                    status="WIN" if gross_pnl >= 0 else "LOSS",
                    gross_pnl=gross_pnl,
                    is_maker_exit=True,
                )
                entry_fee = getattr(pos, "entry_fee", 0.0) or (pos.notional * 0.0002)
                self.paper_balance += (closed_trade["pnl"] + entry_fee)
                closed_records.append(closed_trade)
                await self.state_machine.transition_to(PositionState.CLOSED, "Time Expiry")
                await self.state_machine.transition_to(PositionState.FLAT, "Desk reset to FLAT")
                logger.info(f"⏳ [PAPER TIME INVALIDATION] {side.value} {symbol} closed @ ${cur_price:.2f} (Net PnL: ${closed_trade['pnl']:+.2f}, Fees: ${closed_trade['fees']:.2f})")

        return closed_records

    async def _place_live_tp_orders(self, pos: ActivePosition):
        """
        Place server-side Reduce-Only Limit Maker orders on Bybit:
        - 50% at tp1_price
        - 50% at tp2_price
        This captures maker rebates and allows the TP2 runner to operate without conflict.
        """
        if pos.tp1_order_id or pos.tp1_filled or pos.size <= 0:
            return

        symbol = pos.symbol
        close_side = "Sell" if (pos.side == OrderSide.BUY or getattr(pos.side, "value", "") == "Buy") else "Buy"
        half_qty = self.instrument_repo.round_qty(symbol, pos.size * 0.50)
        rem_qty = self.instrument_repo.round_qty(symbol, pos.size - half_qty)

        # Place TP1 Limit Reduce-Only Maker Order
        if half_qty > 0 and pos.tp1_price > 0:
            try:
                res1 = await self.client.place_order(
                    symbol=symbol,
                    side=close_side,
                    qty=half_qty,
                    price=pos.tp1_price,
                    order_type="Limit",
                    time_in_force="GTC",
                    reduce_only=True,
                )
                if isinstance(res1, dict) and res1.get("retCode") == 0:
                    pos.tp1_order_id = res1.get("result", {}).get("orderId", "")
                    logger.info(f"🎯 [LIVE TP1 MAKER PLACED] {close_side} {half_qty} {symbol} @ ${pos.tp1_price:.2f} (id={pos.tp1_order_id})")
                elif isinstance(res1, dict):
                    logger.warning(f"Failed to place TP1 limit order: {res1.get('retMsg')}")
            except Exception as e:
                logger.error(f"Error placing TP1 limit order: {e}")

        # Place TP2 Limit Reduce-Only Maker Order
        if rem_qty > 0 and pos.tp2_price > 0:
            try:
                res2 = await self.client.place_order(
                    symbol=symbol,
                    side=close_side,
                    qty=rem_qty,
                    price=pos.tp2_price,
                    order_type="Limit",
                    time_in_force="GTC",
                    reduce_only=True,
                )
                if isinstance(res2, dict) and res2.get("retCode") == 0:
                    pos.tp2_order_id = res2.get("result", {}).get("orderId", "")
                    logger.info(f"🏆 [LIVE TP2 MAKER PLACED] {close_side} {rem_qty} {symbol} @ ${pos.tp2_price:.2f} (id={pos.tp2_order_id})")
                elif isinstance(res2, dict):
                    logger.warning(f"Failed to place TP2 limit order: {res2.get('retMsg')}")
            except Exception as e:
                logger.error(f"Error placing TP2 limit order: {e}")

    async def _update_live_positions(self, latest_prices: Dict[str, float]) -> List[Dict[str, Any]]:
        """
        Monitor active live positions on Bybit exchange,
        detect fills, verify server-side SL brackets, handle partial TP1/TP2 maker close,
        and process time-based invalidations.
        """
        closed_records = []
        now = time.time()

        for symbol, pos in list(self.live_positions.items()):
            cur_price = latest_prices.get(symbol, 0.0)
            if cur_price > 0:
                pos.mark_price = cur_price
            cached_pos = self.account.cached_positions.get(symbol)
            side = pos.side
            entry = pos.entry_price
            sl = pos.stop_price
            tp1 = pos.tp1_price
            tp2 = pos.tp2_price
            size = pos.size

            # 1. Handle Pending Entry Fill or TTL Timeout
            cached_size = float(cached_pos.get("size", 0) or 0) if cached_pos else 0.0
            if pos.state == PositionState.ENTRY_PENDING:
                if cached_size > 0:
                    pos.filled_quantity = cached_size
                    pos.size = cached_size
                    entry_p = float(cached_pos.get("entry_price") or cached_pos.get("avgPrice") or entry)
                    pos.entry_price = entry_p if entry_p > 0 else entry
                    pos.entry_time = now

                    # Cancel any remaining resting entry limit order to prevent orphan fills
                    try:
                        await self.client.cancel_order(symbol, order_link_id=pos.client_order_id)
                    except Exception as e:
                        logger.debug(f"Entry order cancel/cleanup: {e}")

                    # Transition to PROTECTION_PENDING then ACTIVE
                    await self.state_machine.transition_to(PositionState.PROTECTION_PENDING, "Live fill detected")
                    # Ensure server-side stop loss is established on Bybit (SL only, TP managed via Limit Maker orders)
                    stop_loss_val = float(cached_pos.get("stop_loss", 0) or cached_pos.get("stopLoss", 0) or 0)
                    if stop_loss_val == 0:
                        await self.client.set_trading_stop(
                            symbol=symbol,
                            stop_loss=pos.stop_price,
                        )
                    await self.state_machine.transition_to(PositionState.ACTIVE, "Live bracket confirmed")
                    pos.state = PositionState.ACTIVE
                    logger.info(f"⚡ [LIVE FILL CONFIRMED] {side.value} {pos.size} {symbol} @ ${pos.entry_price:.2f}")

                    # Place live TP1 & TP2 Reduce-Only Limit Maker orders
                    await self._place_live_tp_orders(pos)
                elif now - pos.entry_time > 45.0:
                    # TTL expired, cancel unfilled limit order
                    logger.info(f"⏳ Live entry order TTL expired for {symbol}, canceling order...")
                    try:
                        await self.client.cancel_order(symbol, order_link_id=pos.client_order_id)
                    except Exception as err:
                        logger.warning(f"Error canceling expired entry order: {err}")
                    self.live_positions.pop(symbol, None)
                    await self.state_machine.transition_to(PositionState.FLAT, "Entry TTL expired")
                continue

            # Synchronize active size if exchange reports different size (e.g., partial fill or external adjustment)
            if cached_size > 0:
                if not pos.tp1_filled and cached_size != pos.size:
                    logger.info(f"🔄 Syncing position size for {symbol}: {pos.size} -> {cached_size}")
                    pos.size = cached_size

            # Ensure TP limit maker orders are placed if active and not yet queued
            if pos.state == PositionState.ACTIVE and not pos.tp1_filled and not pos.tp1_order_id:
                await self._place_live_tp_orders(pos)

            # If position is in EXITING state, monitor resting exit maker order and TTL fallback
            if pos.state == PositionState.EXITING:
                time_in_exiting = now - (pos.exit_order_time or now)
                if cached_size <= 0:
                    # Successfully filled on Bybit as maker!
                    gross_pnl = (cur_price - entry) * size if side == OrderSide.BUY else (entry - cur_price) * size
                    self.live_positions.pop(symbol, None)
                    try:
                        await self.client.cancel_all_orders(symbol)
                    except Exception:
                        pass
                    closed_trade = self._build_closed_record(
                        pos=pos,
                        exit_price=cur_price if cur_price > 0 else entry,
                        exit_reason="Time-Based Thesis Invalidation (Maker Fill)",
                        attribution=FailureType.NORMAL_LOSS.value if gross_pnl < 0 else FailureType.NORMAL_WIN.value,
                        status="WIN" if gross_pnl >= 0 else "LOSS",
                        gross_pnl=gross_pnl,
                        is_maker_exit=True,
                    )
                    closed_records.append(closed_trade)
                    await self.state_machine.transition_to(PositionState.CLOSED, "Time Expiry Maker Fill")
                    await self.state_machine.transition_to(PositionState.FLAT, "Desk reset to FLAT")
                    logger.info(f"⏳ [LIVE TIME INVALIDATION MAKER FILL] {side.value} {symbol} filled as Maker @ ${cur_price:.2f}")
                    continue
                elif time_in_exiting > 15.0:
                    # 15s TTL expired without fill, cancel maker order and fallback to Market
                    logger.warning(f"⏳ Time invalidation maker order TTL expired for {symbol} ({time_in_exiting:.1f}s > 15s), executing Market fallback")
                    try:
                        if pos.exit_order_id:
                            await self.client.cancel_order(symbol, order_id=pos.exit_order_id)
                    except Exception as e:
                        logger.warning(f"Failed to cancel resting exit maker order: {e}")

                    close_side = "Sell" if (side == OrderSide.BUY or getattr(side, "value", "") == "Buy") else "Buy"
                    exit_qty = self.instrument_repo.round_qty(symbol, cached_size if cached_size > 0 else pos.size)
                    try:
                        await self.client.place_order(
                            symbol=symbol,
                            side=close_side,
                            qty=exit_qty,
                            order_type="Market",
                            reduce_only=True,
                        )
                    except Exception as e:
                        logger.error(f"Failed to execute market fallback for time invalidation: {e}")

                    gross_pnl = (cur_price - entry) * size if side == OrderSide.BUY else (entry - cur_price) * size
                    self.live_positions.pop(symbol, None)
                    closed_trade = self._build_closed_record(
                        pos=pos,
                        exit_price=cur_price if cur_price > 0 else entry,
                        exit_reason="Time-Based Thesis Invalidation (Market Fallback)",
                        attribution=FailureType.NORMAL_LOSS.value if gross_pnl < 0 else FailureType.NORMAL_WIN.value,
                        status="WIN" if gross_pnl >= 0 else "LOSS",
                        gross_pnl=gross_pnl,
                        is_maker_exit=False,
                    )
                    closed_records.append(closed_trade)
                    await self.state_machine.transition_to(PositionState.CLOSED, "Time Expiry Market Fallback")
                    await self.state_machine.transition_to(PositionState.FLAT, "Desk reset to FLAT")
                    logger.info(f"⏳ [LIVE TIME INVALIDATION] {side.value} {symbol} closed via Market fallback @ ${cur_price:.2f}")
                    continue
                else:
                    # Still waiting for maker fill within 15s TTL
                    continue

            if cur_price > 0:
                pnl_delta = (cur_price - entry) if side == OrderSide.BUY else (entry - cur_price)
                if pnl_delta > pos.mfe:
                    pos.mfe = pnl_delta
                    pos.time_to_mfe = now - pos.entry_time
                if pnl_delta < pos.mae:
                    pos.mae = pnl_delta
                    pos.time_to_mae = now - pos.entry_time

                # Current gross profit for active live position
                current_trade_gross_pnl = (cur_price - entry) * size if side == OrderSide.BUY else (entry - cur_price) * size

                # Break-Even Ratchet Protection (e.g. +$25 profit halfway to target)
                if self.breakeven_trigger_usd > 0 and current_trade_gross_pnl >= self.breakeven_trigger_usd and not getattr(pos, "be_moved", False):
                    be_buffer = entry * 0.0005
                    new_stop = (entry + be_buffer) if side == OrderSide.BUY else (entry - be_buffer)
                    new_stop = self.instrument_repo.round_price(symbol, new_stop)
                    if self.state_machine.validate_stop_update(pos.stop_price, new_stop, is_long=(side == OrderSide.BUY)):
                        pos.stop_price = new_stop
                        pos.be_moved = True
                        try:
                            await self.client.set_trading_stop(symbol=symbol, stop_loss=new_stop)
                            logger.info(f"🛡️ [LIVE BREAK-EVEN RATIFIED] {symbol} profit reaches ${current_trade_gross_pnl:.2f} (>= ${self.breakeven_trigger_usd:.2f}). Live stop moved to ${new_stop:.4f}")
                            if self.brain_trace:
                                self.brain_trace.record_step(
                                    symbol=symbol,
                                    stage="BREAK_EVEN",
                                    status="PASS",
                                    message=f"Live profit reaches ${current_trade_gross_pnl:.2f} (>= ${self.breakeven_trigger_usd:.2f}). Stop locked at Break-Even (${new_stop:.4f})"
                                )
                        except Exception as e:
                            logger.error(f"Failed to move live stop to BE: {e}")

                # Target Profit Harvester ($50+ Guaranteed Profit Lock)
                if self.min_take_profit_usd > 0 and current_trade_gross_pnl >= self.min_take_profit_usd:
                    logger.info(f"💰 [LIVE TARGET PROFIT REACHED] {symbol} profit reaches ${current_trade_gross_pnl:.2f} (>= ${self.min_take_profit_usd:.2f}). Executing market close...")
                    close_side = "Sell" if (side == OrderSide.BUY or getattr(side, "value", "") == "Buy") else "Buy"
                    rem_qty = self.instrument_repo.round_qty(symbol, pos.size)
                    try:
                        res = await self.client.place_order(
                            symbol=symbol,
                            side=close_side,
                            qty=rem_qty,
                            order_type="Market",
                            reduce_only=True,
                        )
                        if res.get("retCode") == 0:
                            self.live_positions.pop(symbol, None)
                            closed_trade = self._build_closed_record(
                                pos=pos,
                                exit_price=cur_price,
                                exit_reason=f"Target Profit Reached (${current_trade_gross_pnl:+.2f} >= ${self.min_take_profit_usd:.0f})",
                                attribution=FailureType.NORMAL_WIN.value,
                                status="WIN",
                                gross_pnl=current_trade_gross_pnl,
                                is_maker_exit=False,
                            )
                            closed_records.append(closed_trade)
                            await self.state_machine.transition_to(PositionState.CLOSED, f"Target Profit ${self.min_take_profit_usd:.0f} Reached")
                            await self.state_machine.transition_to(PositionState.FLAT, "Resetting desk to FLAT")
                            logger.info(f"💰 [LIVE TARGET PROFIT HARVESTED] {side.value} {symbol} closed @ ${cur_price:.4f} (Net PnL: ${closed_trade['pnl']:+.2f})")
                            if self.brain_trace:
                                self.brain_trace.record_step(
                                    symbol=symbol,
                                    stage="TARGET_PROFIT",
                                    status="PASS",
                                    message=f"Live Target Profit Harvested: {side.value} {symbol} closed @ ${cur_price:.4f} (Net: ${closed_trade['pnl']:+.2f}, Gross: ${current_trade_gross_pnl:+.2f})"
                                )
                            continue
                    except Exception as err:
                        logger.error(f"Failed to execute live target profit close: {err}")

            # Check if position closed on exchange (SL hit or external close)
            trade_age = now - pos.entry_time
            exchange_closed = False
            if cached_pos is not None:
                exchange_closed = (cached_size <= 0)
            elif trade_age > 10.0 and pos.filled_quantity > 0:
                # Only assume closed if position was previously confirmed and missing from cache >10s
                exchange_closed = True

            if exchange_closed:
                exit_p = cur_price if cur_price > 0 else sl
                actual_closed_size = (size * 0.5) if pos.tp1_filled else size
                gross_pnl = (exit_p - entry) * actual_closed_size if side == OrderSide.BUY else (entry - exit_p) * actual_closed_size
                if pos.tp1_filled:
                    tp1_profit = (tp1 - entry) * (size * 0.5) if side == OrderSide.BUY else (entry - tp1) * (size * 0.5)
                    gross_pnl += tp1_profit

                self.live_positions.pop(symbol, None)
                try:
                    await self.client.cancel_all_orders(symbol)
                except Exception:
                    pass

                closed_trade = self._build_closed_record(
                    pos=pos,
                    exit_price=exit_p,
                    exit_reason="Exchange Position Closed (SL/TP)",
                    attribution=FailureType.NORMAL_WIN.value if gross_pnl >= 0 else FailureType.NORMAL_LOSS.value,
                    status="WIN" if gross_pnl >= 0 else "LOSS",
                    gross_pnl=gross_pnl,
                    is_maker_exit=pos.tp1_filled,
                )
                closed_records.append(closed_trade)
                await self.state_machine.transition_to(PositionState.CLOSED, "Exchange position closed")
                await self.state_machine.transition_to(PositionState.FLAT, "Resetting desk to FLAT")
                logger.info(f"🏁 [LIVE POSITION CLOSED] {side.value} {symbol} closed (Net PnL: ${closed_trade['pnl']:+.2f}, Fees: ${closed_trade['fees']:.2f})")
                continue

            # 3. TP1 Check: Detect fill or price reach, move stop to breakeven + buffer
            if cur_price > 0 and not pos.tp1_filled:
                hit_tp1_price = (cur_price >= tp1) if side == OrderSide.BUY else (cur_price <= tp1)
                partial_fill_detected = (0 < cached_size <= (size * 0.60))

                if hit_tp1_price or partial_fill_detected:
                    realized_qty = self.instrument_repo.round_qty(symbol, size * 0.50)
                    close_side = "Sell" if (side == OrderSide.BUY or getattr(side, "value", "") == "Buy") else "Buy"
                    tp1_success = False

                    if partial_fill_detected:
                        tp1_success = True
                        pos.size = cached_size
                        logger.info(f"🎯 [LIVE TP1 MAKER FILL CONFIRMED] Exchange size reduced to {cached_size}")
                    else:
                        if not pos.tp1_order_id:
                            try:
                                res = await self.client.place_order(
                                    symbol=symbol,
                                    side=close_side,
                                    qty=realized_qty,
                                    order_type="Market",
                                    reduce_only=True,
                                )
                                if res.get("retCode") == 0:
                                    tp1_success = True
                                    pos.size = max(0.0, pos.size - realized_qty)
                                else:
                                    logger.error(f"Bybit rejected TP1 close order: {res.get('retMsg')}")
                            except Exception as e:
                                logger.error(f"Failed to place live TP1 close order: {e}")
                        else:
                            overshoot = (cur_price - tp1) if side == OrderSide.BUY else (tp1 - cur_price)
                            if overshoot > (entry * 0.0005):
                                try:
                                    await self.client.cancel_order(symbol, order_id=pos.tp1_order_id)
                                    res = await self.client.place_order(
                                        symbol=symbol,
                                        side=close_side,
                                        qty=realized_qty,
                                        order_type="Market",
                                        reduce_only=True,
                                    )
                                    if res.get("retCode") == 0:
                                        tp1_success = True
                                        pos.size = max(0.0, pos.size - realized_qty)
                                except Exception as err:
                                    logger.error(f"TP1 overshoot fallback error: {err}")

                    if tp1_success or partial_fill_detected:
                        pos.tp1_filled = True
                        be_buffer = entry * 0.0005
                        new_stop = (entry + be_buffer) if side == OrderSide.BUY else (entry - be_buffer)
                        new_stop = self.instrument_repo.round_price(symbol, new_stop)
                        if self.state_machine.validate_stop_update(pos.stop_price, new_stop, is_long=(side == OrderSide.BUY)):
                            pos.stop_price = new_stop
                            try:
                                await self.client.set_trading_stop(symbol=symbol, stop_loss=new_stop)
                                logger.info(f"🛡️ [LIVE BREAK-EVEN RATIFIED] Server stop moved to ${new_stop:.4f}")
                            except Exception as err:
                                logger.error(f"Failed to move server stop to BE on Bybit: {err}")

                        await self.state_machine.transition_to(PositionState.TP1_FILLED, "TP1 reached (50% live closed)")
                        await self.state_machine.transition_to(PositionState.PROTECTED_RUNNER, "Live stop moved to Breakeven + buffer")
                        logger.info(f"🎯 [LIVE TP1 HIT] 50% closed @ ${tp1:.2f}. Stop moved to ${new_stop:.2f}")

            # 4. TP2 Check: Runner Target Close (100% of remaining position)
            if cur_price > 0 and pos.tp1_filled:
                hit_tp2 = (cur_price >= tp2) if side == OrderSide.BUY else (cur_price <= tp2)
                if hit_tp2:
                    close_side = "Sell" if (side == OrderSide.BUY or getattr(side, "value", "") == "Buy") else "Buy"
                    rem_qty = self.instrument_repo.round_qty(symbol, pos.size)
                    tp2_success = False
                    try:
                        if pos.tp2_order_id:
                            await self.client.cancel_order(symbol, order_id=pos.tp2_order_id)
                        res = await self.client.place_order(
                            symbol=symbol,
                            side=close_side,
                            qty=rem_qty,
                            order_type="Market",
                            reduce_only=True,
                        )
                        if res.get("retCode") == 0:
                            tp2_success = True
                        else:
                            logger.error(f"Bybit rejected TP2 close order: {res.get('retMsg')}")
                    except Exception as e:
                        logger.error(f"Failed to place live TP2 close order: {e}")

                    if tp2_success:
                        total_gross_pnl = ((tp1 - entry) * (size * 0.5) + (tp2 - entry) * rem_qty) if side == OrderSide.BUY else (
                            (entry - tp1) * (size * 0.5) + (entry - tp2) * rem_qty
                        )
                        self.live_positions.pop(symbol, None)
                        try:
                            await self.client.cancel_all_orders(symbol)
                        except Exception:
                            pass

                        closed_trade = self._build_closed_record(
                            pos=pos,
                            exit_price=tp2,
                            exit_reason="TP2 Target Reached (Live)",
                            attribution=FailureType.NORMAL_WIN.value,
                            status="WIN",
                            gross_pnl=total_gross_pnl,
                            is_maker_exit=False,
                        )
                        closed_records.append(closed_trade)
                        await self.state_machine.transition_to(PositionState.CLOSED, "TP2 Hit")
                        await self.state_machine.transition_to(PositionState.FLAT, "Desk reset to FLAT")
                        logger.info(f"🏆 [LIVE TP2 FULL TARGET] {side.value} {symbol} closed @ ${tp2:.2f} (Net PnL: ${closed_trade['pnl']:+.2f}, Fees: ${closed_trade['fees']:.2f})")
                        continue

            # 5. Time-Based Thesis Invalidation
            trade_duration = now - pos.entry_time
            if pos.max_thesis_lifetime > 0 and trade_duration > pos.max_thesis_lifetime and not pos.tp1_filled:
                logger.warning(f"⏳ Time-based thesis invalidation for live position {symbol} ({trade_duration:.0f}s > {pos.max_thesis_lifetime}s)")
                close_side = "Sell" if (side == OrderSide.BUY or getattr(side, "value", "") == "Buy") else "Buy"
                exit_qty = self.instrument_repo.round_qty(symbol, pos.size)

                # Transition to EXITING state
                await self.state_machine.transition_to(PositionState.EXITING, "Time Expiry Exit Initiated")
                pos.state = PositionState.EXITING
                pos.exit_order_time = now

                # Cancel resting TP orders before placing exit order
                try:
                    await self.client.cancel_all_orders(symbol)
                except Exception as err:
                    logger.warning(f"Failed to cancel resting orders for {symbol}: {err}")

                maker_placed = False
                try:
                    rounded_price = self.instrument_repo.round_price(symbol, cur_price)
                    res = await self.client.place_order(
                        symbol=symbol,
                        side=close_side,
                        qty=exit_qty,
                        price=rounded_price,
                        order_type="Limit",
                        time_in_force="PostOnly",
                        reduce_only=True,
                    )
                    if res.get("retCode") == 0:
                        maker_placed = True
                        pos.exit_order_id = res.get("result", {}).get("orderId", "")
                        logger.info(f"⏳ [LIVE TIME INVALIDATION MAKER PLACED] {close_side} {exit_qty} {symbol} @ ${rounded_price} (id={pos.exit_order_id}, TTL 15s)")
                    else:
                        logger.warning(f"PostOnly maker exit rejected ({res.get('retMsg')}), falling back to Market order")
                except Exception as e:
                    logger.warning(f"Failed to submit PostOnly exit for {symbol}: {e}")

                if not maker_placed:
                    try:
                        await self.client.place_order(
                            symbol=symbol,
                            side=close_side,
                            qty=exit_qty,
                            order_type="Market",
                            reduce_only=True,
                        )
                    except Exception as e:
                        logger.error(f"Failed to execute time-invalidation market close: {e}")

                    gross_pnl = (cur_price - entry) * size if side == OrderSide.BUY else (entry - cur_price) * size
                    self.live_positions.pop(symbol, None)

                    closed_trade = self._build_closed_record(
                        pos=pos,
                        exit_price=cur_price,
                        exit_reason="Time-Based Thesis Invalidation",
                        attribution=FailureType.NORMAL_LOSS.value if gross_pnl < 0 else FailureType.NORMAL_WIN.value,
                        status="WIN" if gross_pnl >= 0 else "LOSS",
                        gross_pnl=gross_pnl,
                        is_maker_exit=False,
                    )
                    closed_records.append(closed_trade)
                    await self.state_machine.transition_to(PositionState.CLOSED, "Time Expiry Market")
                    await self.state_machine.transition_to(PositionState.FLAT, "Desk reset to FLAT")
                    logger.info(f"⏳ [LIVE TIME INVALIDATION] {side.value} {symbol} closed @ ${cur_price:.2f} (Net PnL: ${closed_trade['pnl']:+.2f}, Fees: ${closed_trade['fees']:.2f})")
                    continue
                else:
                    continue

        return closed_records

    def handle_execution_fill(self, fill_event: Dict[str, Any]):
        """Handle real-time fill notification from Private WebSocket with idempotency deduplication."""
        exec_id = str(fill_event.get("execId", "") or "")
        if exec_id:
            if exec_id in self._processed_exec_ids:
                logger.debug(f"Ignoring duplicate execution fill: {exec_id}")
                return
            self._processed_exec_ids.add(exec_id)
            if len(self._processed_exec_ids) > 10000:
                self._processed_exec_ids = set(list(self._processed_exec_ids)[-5000:])

        symbol = fill_event.get("symbol", "")
        if symbol in self.live_positions:
            pos = self.live_positions[symbol]
            exec_qty = float(fill_event.get("execQty", 0) or 0)
            exec_price = float(fill_event.get("execPrice", 0) or 0)
            exec_fee = float(fill_event.get("execFee", 0) or 0)
            if exec_fee > 0:
                pos.fees_paid += exec_fee
                if pos.state in (PositionState.ENTRY_PENDING, PositionState.PARTIALLY_FILLED):
                    pos.entry_fee += exec_fee
            if exec_qty > 0 and pos.state in (PositionState.ENTRY_PENDING, PositionState.PARTIALLY_FILLED):
                pos.filled_quantity += exec_qty
                if exec_price > 0:
                    pos.entry_price = exec_price
                logger.info(f"⚡ [PRIVATE WS FILL] {symbol}: filled {exec_qty} (total {pos.filled_quantity}/{pos.size}) @ ${exec_price} (Fee: ${exec_fee:.4f})")

    async def close_active_position(
        self,
        symbol: Optional[str] = None,
        reason: str = "Manual User Close",
        exit_price: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Manually and immediately close the active position (paper or live mode)."""
        now = time.time()
        if self.is_paper:
            targets = list(self.paper_positions.items())
            if not targets:
                return None
            target_sym, pos = targets[0] if not symbol else (symbol, self.paper_positions.get(symbol))
            if not pos:
                return None

            cur_price = exit_price or 0.0
            if cur_price <= 0:
                if self.ws and hasattr(self.ws, "latest_prices") and target_sym in self.ws.latest_prices:
                    cur_price = float(self.ws.latest_prices[target_sym] or 0.0)
                elif getattr(pos, "mark_price", 0.0) > 0:
                    cur_price = float(pos.mark_price)
                else:
                    cur_price = float(pos.entry_price or 0.0)
            if cur_price <= 0:
                cur_price = float(pos.entry_price or 0.0)

            side = pos.side
            entry = pos.entry_price
            size = pos.size

            if pos.tp1_filled:
                gross_pnl = ((pos.tp1_price - entry) * (size * 0.5) + (cur_price - entry) * (size * 0.5)) if side == OrderSide.BUY else (
                    (entry - pos.tp1_price) * (size * 0.5) + (entry - cur_price) * (size * 0.5)
                )
            else:
                gross_pnl = (cur_price - entry) * size if side == OrderSide.BUY else (entry - cur_price) * size

            self.paper_positions.pop(target_sym, None)
            closed_trade = self._build_closed_record(
                pos=pos,
                exit_price=cur_price,
                exit_reason=reason,
                attribution=FailureType.NORMAL_WIN.value if gross_pnl >= 0 else FailureType.NORMAL_LOSS.value,
                status="WIN" if gross_pnl >= 0 else "LOSS",
                gross_pnl=gross_pnl,
                is_maker_exit=False,
            )
            already_credited = (pos.tp1_price - entry) * (size * 0.5) if (pos.tp1_filled and side == OrderSide.BUY) else (
                (entry - pos.tp1_price) * (size * 0.5) if pos.tp1_filled else 0.0
            )
            entry_fee = getattr(pos, "entry_fee", 0.0) or (pos.notional * 0.0002)
            self.paper_balance += (closed_trade["pnl"] + entry_fee - already_credited)
            await self.state_machine.transition_to(PositionState.CLOSED, reason)
            await self.state_machine.transition_to(PositionState.FLAT, "Desk reset to FLAT after manual close")
            logger.info(
                f"🛑 [MANUAL CLOSE] {side.value} {target_sym} closed @ ${cur_price:.2f} "
                f"(Net PnL: ${closed_trade['pnl']:+.2f}, Fees: ${closed_trade['fees']:.2f})"
            )
            return closed_trade
        else:
            targets = list(self.live_positions.items())
            if not targets:
                return None
            target_sym, pos = targets[0] if not symbol else (symbol, self.live_positions.get(symbol))
            if not pos:
                return None

            # Cancel open orders
            try:
                await self.client.cancel_all_orders(target_sym)
            except Exception as err:
                logger.warning(f"Error canceling orders for {target_sym} before manual close: {err}")

            cur_price = exit_price or 0.0
            if cur_price <= 0:
                if self.ws and hasattr(self.ws, "latest_prices") and target_sym in self.ws.latest_prices:
                    cur_price = float(self.ws.latest_prices[target_sym] or 0.0)
                elif getattr(pos, "mark_price", 0.0) > 0:
                    cur_price = float(pos.mark_price)
                else:
                    cached = self.account.cached_positions.get(target_sym, {})
                    cur_price = float(cached.get("markPrice") or cached.get("mark_price") or cached.get("lastPrice") or 0.0)
            if cur_price <= 0:
                cur_price = float(pos.entry_price or 0.0)

            side = pos.side
            close_side = "Sell" if side == OrderSide.BUY else "Buy"
            size = pos.size
            if size > 0:
                try:
                    await self.client.place_order(
                        symbol=target_sym,
                        side=close_side,
                        order_type="Market",
                        qty=self.instrument_repo.round_qty(target_sym, size),
                        reduce_only=True,
                    )
                except Exception as err:
                    logger.error(f"Failed to submit market close order for {target_sym}: {err}")

            if pos.tp1_filled:
                gross_pnl = ((pos.tp1_price - pos.entry_price) * (size * 0.5) + (cur_price - pos.entry_price) * (size * 0.5)) if side == OrderSide.BUY else (
                    (pos.entry_price - pos.tp1_price) * (size * 0.5) + (pos.entry_price - cur_price) * (size * 0.5)
                )
            else:
                gross_pnl = (cur_price - pos.entry_price) * size if side == OrderSide.BUY else (pos.entry_price - cur_price) * size

            self.live_positions.pop(target_sym, None)
            exact_fees = None
            exact_pnl = None
            try:
                await asyncio.sleep(0.3)
                closed_pnl_list = await self.client.get_closed_pnl(symbol=target_sym, limit=1)
                if closed_pnl_list:
                    rec = closed_pnl_list[0]
                    if float(rec.get("closedPnl", 0) or 0) != 0:
                        cur_price = float(rec.get("avgExitPrice", 0) or cur_price)
                        exact_pnl = float(rec.get("closedPnl", 0) or 0)
                executions = await self.client.get_executions(symbol=target_sym, limit=4)
                if executions:
                    total_fee = sum(float(ex.get("execFee", 0) or 0) for ex in executions[:2])
                    if total_fee > 0:
                        exact_fees = round(total_fee, 4)
            except Exception as err:
                logger.debug(f"Could not query Bybit closed PnL / executions on manual close: {err}")

            closed_trade = self._build_closed_record(
                pos=pos,
                exit_price=cur_price,
                exit_reason=reason,
                attribution=FailureType.NORMAL_WIN.value if (exact_pnl if exact_pnl is not None else gross_pnl) >= 0 else FailureType.NORMAL_LOSS.value,
                status="WIN" if (exact_pnl if exact_pnl is not None else gross_pnl) >= 0 else "LOSS",
                gross_pnl=gross_pnl,
                is_maker_exit=False,
                exact_fees=exact_fees,
                exact_net_pnl=exact_pnl,
            )
            await self.state_machine.transition_to(PositionState.CLOSED, reason)
            await self.state_machine.transition_to(PositionState.FLAT, "Desk reset to FLAT after manual close")
            logger.info(
                f"🛑 [LIVE MANUAL CLOSE] {side.value} {target_sym} closed @ ${cur_price:.2f} "
                f"(Net PnL: ${closed_trade['pnl']:+.2f}, Fees: ${closed_trade['fees']:.2f})"
            )
            return closed_trade

