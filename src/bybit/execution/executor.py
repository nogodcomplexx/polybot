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

        await self.state_machine.transition_to(PositionState.ENTRY_PENDING, f"Placing {plan.side.value} entry on {plan.symbol}")

        # Deterministic client order ID
        client_order_id = plan.client_order_id or f"ord_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
        plan.client_order_id = client_order_id

        # 2. Paper Trading Execution
        if self.is_paper:
            return await self._execute_paper_entry(plan, strategy_name, regime)

        # 3. Live Bybit V5 Execution
        try:
            # Set leverage & margin mode if needed
            await self.client.set_margin_mode(plan.symbol, is_isolated=True, leverage=self.default_leverage)
            await self.client.set_leverage(plan.symbol, self.default_leverage)

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

            # Place Post-Only Maker Limit Entry Order with server-side SL/TP attached
            res = await self.client.place_order(
                symbol=plan.symbol,
                side=plan.side.value,
                qty=rounded_qty,
                price=rounded_entry,
                order_type="Limit",
                time_in_force="PostOnly",
                order_link_id=client_order_id,
                take_profit=rounded_tp1,
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
                leverage=self.default_leverage,
                strategy=strategy_name,
                regime=regime,
                client_order_id=client_order_id,
                entry_time=time.time(),
                state=PositionState.ENTRY_PENDING,
                filled_quantity=0.0,
                notional=rounded_qty * rounded_entry,
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
        pos = ActivePosition(
            symbol=plan.symbol,
            side=plan.side,
            size=plan.quantity,
            entry_price=plan.entry_price,
            stop_price=plan.stop_price,
            tp1_price=plan.tp1_price,
            tp2_price=plan.tp2_price,
            leverage=self.default_leverage,
            strategy=strategy_name,
            regime=regime,
            client_order_id=plan.client_order_id,
            entry_time=time.time(),
            state=PositionState.ACTIVE if is_market else PositionState.ENTRY_PENDING,
            filled_quantity=plan.quantity if is_market else 0.0,
            notional=plan.quantity * plan.entry_price,
            ttl_seconds=plan.ttl_seconds,
        )

        self.paper_positions[plan.symbol] = pos
        self.state_machine.active_position = pos

        if is_market:
            taker_fee = pos.notional * 0.00055
            self.paper_balance -= taker_fee
            logger.info(
                f"📝 [PAPER MARKET FILL] {plan.side.value} {plan.quantity} {plan.symbol} @ ${plan.entry_price:.2f} "
                f"(Taker fee: ${taker_fee:.4f}, Hard SL: ${plan.stop_price:.2f}, TP1: ${plan.tp1_price:.2f}, TP2: ${plan.tp2_price:.2f})"
            )
            await self.state_machine.transition_to(PositionState.PROTECTION_PENDING, "Paper market order filled")
            await self.state_machine.transition_to(PositionState.ACTIVE, "Protective brackets established")
        else:
            logger.info(
                f"📝 [PAPER ENTRY QUEUED] {plan.side.value} {plan.quantity} {plan.symbol} @ ${plan.entry_price:.2f} "
                f"(TTL: {plan.ttl_seconds}s, Hard SL: ${plan.stop_price:.2f}, TP1: ${plan.tp1_price:.2f}, TP2: ${plan.tp2_price:.2f})"
            )
            # State machine is already in ENTRY_PENDING from execute_plan

        return True

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

            # ----------------------------------------------------------
            # 1. Stop Loss Trigger
            # ----------------------------------------------------------
            hit_sl = (cur_price <= sl) if side == OrderSide.BUY else (cur_price >= sl)
            if hit_sl:
                pnl = (sl - entry) * size if side == OrderSide.BUY else (entry - sl) * size
                pnl_pct = (pnl / (entry * size)) * 100 * pos.leverage
                self.paper_balance += pnl
                self.paper_positions.pop(symbol, None)

                closed_trade = {
                    "id": pos.client_order_id,
                    "timestamp": now,
                    "symbol": symbol,
                    "side": side.value,
                    "strategy": pos.strategy.value if hasattr(pos.strategy, "value") else str(pos.strategy),
                    "regime": pos.regime.value if hasattr(pos.regime, "value") else str(pos.regime),
                    "entry": entry,
                    "exit_price": sl,
                    "stop_price": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "quantity": size,
                    "leverage": pos.leverage,
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "mae": round(pos.mae, 2),
                    "mfe": round(pos.mfe, 2),
                    "hold_time": round(now - pos.entry_time, 1),
                    "exit_reason": "Stop Loss Hit",
                    "attribution": FailureType.NORMAL_LOSS.value,
                    "status": "LOSS",
                }
                closed_records.append(closed_trade)
                await self.state_machine.transition_to(PositionState.CLOSED, "SL Hit")
                await self.state_machine.transition_to(PositionState.FLAT, "Resetting desk to FLAT")
                logger.info(f"🛑 [PAPER SL HIT] {side.value} {symbol} closed @ ${sl:.2f} (PnL: ${pnl:+.2f})")
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
                rem_qty = size * 0.50
                pnl2 = (tp2 - entry) * rem_qty if side == OrderSide.BUY else (entry - tp2) * rem_qty
                total_pnl = ((tp1 - entry) * (size * 0.5) + (tp2 - entry) * (size * 0.5)) if side == OrderSide.BUY else (
                    (entry - tp1) * (size * 0.5) + (entry - tp2) * (size * 0.5)
                )
                self.paper_balance += pnl2
                self.paper_positions.pop(symbol, None)

                closed_trade = {
                    "id": pos.client_order_id,
                    "timestamp": now,
                    "symbol": symbol,
                    "side": side.value,
                    "strategy": pos.strategy.value if hasattr(pos.strategy, "value") else str(pos.strategy),
                    "regime": pos.regime.value if hasattr(pos.regime, "value") else str(pos.regime),
                    "entry": entry,
                    "exit_price": tp2,
                    "stop_price": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "quantity": size,
                    "leverage": pos.leverage,
                    "pnl": round(total_pnl, 2),
                    "pnl_pct": round((total_pnl / (entry * size)) * 100 * pos.leverage, 2),
                    "mae": round(pos.mae, 2),
                    "mfe": round(pos.mfe, 2),
                    "hold_time": round(now - pos.entry_time, 1),
                    "exit_reason": "TP2 Target Reached",
                    "attribution": FailureType.NORMAL_WIN.value,
                    "status": "WIN",
                }
                closed_records.append(closed_trade)
                await self.state_machine.transition_to(PositionState.CLOSED, "TP2 Hit")
                await self.state_machine.transition_to(PositionState.FLAT, "Desk reset to FLAT")
                logger.info(f"🏆 [PAPER TP2 FULL TARGET] {side.value} {symbol} closed @ ${tp2:.2f} (Total PnL: ${total_pnl:+.2f})")
                continue

            # ----------------------------------------------------------
            # 4. Time-Based Thesis Invalidation (Section 32/53)
            # ----------------------------------------------------------
            trade_duration = now - pos.entry_time
            if trade_duration > pos.max_thesis_lifetime and not pos.tp1_filled:
                logger.warning(f"⏳ Time-based thesis invalidation for {symbol} ({trade_duration:.0f}s > {pos.max_thesis_lifetime}s)")
                # Proactive close
                pnl = (cur_price - entry) * size if side == OrderSide.BUY else (entry - cur_price) * size
                self.paper_balance += pnl
                self.paper_positions.pop(symbol, None)

                closed_trade = {
                    "id": pos.client_order_id,
                    "timestamp": now,
                    "symbol": symbol,
                    "side": side.value,
                    "strategy": pos.strategy.value if hasattr(pos.strategy, "value") else str(pos.strategy),
                    "regime": pos.regime.value if hasattr(pos.regime, "value") else str(pos.regime),
                    "entry": entry,
                    "exit_price": cur_price,
                    "stop_price": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "quantity": size,
                    "leverage": pos.leverage,
                    "pnl": round(pnl, 2),
                    "pnl_pct": round((pnl / (entry * size)) * 100 * pos.leverage, 2),
                    "mae": round(pos.mae, 2),
                    "mfe": round(pos.mfe, 2),
                    "hold_time": round(trade_duration, 1),
                    "exit_reason": "Time-Based Thesis Invalidation",
                    "attribution": FailureType.NORMAL_LOSS.value if pnl < 0 else FailureType.NORMAL_WIN.value,
                    "status": "WIN" if pnl >= 0 else "LOSS",
                }
                closed_records.append(closed_trade)
                await self.state_machine.transition_to(PositionState.CLOSED, "Time Expiry")
                await self.state_machine.transition_to(PositionState.FLAT, "Desk reset to FLAT")

        return closed_records

    async def _update_live_positions(self, latest_prices: Dict[str, float]) -> List[Dict[str, Any]]:
        """
        Monitor active live positions on Bybit exchange,
        detect fills, verify server-side SL/TP brackets, handle partial TP1 close,
        and process time-based invalidations.
        """
        closed_records = []
        now = time.time()

        for symbol, pos in list(self.live_positions.items()):
            cur_price = latest_prices.get(symbol, 0.0)
            cached_pos = self.account.cached_positions.get(symbol)
            side = pos.side
            entry = pos.entry_price
            sl = pos.stop_price
            tp1 = pos.tp1_price
            tp2 = pos.tp2_price
            size = pos.size

            # 1. Handle Pending Entry Fill or TTL Timeout
            if pos.state == PositionState.ENTRY_PENDING:
                if cached_pos and cached_pos.get("size", 0) > 0:
                    pos.filled_quantity = cached_pos["size"]
                    pos.size = cached_pos["size"]
                    pos.entry_price = cached_pos.get("entry_price", entry)
                    pos.entry_time = now

                    # Cancel any remaining resting entry limit order to prevent orphan fills
                    try:
                        await self.client.cancel_order(symbol, order_link_id=pos.client_order_id)
                    except Exception as e:
                        logger.debug(f"Entry order cancel/cleanup: {e}")

                    # Transition to PROTECTION_PENDING then ACTIVE
                    await self.state_machine.transition_to(PositionState.PROTECTION_PENDING, "Live fill detected")
                    # Ensure server-side stop loss is established on Bybit
                    if not cached_pos.get("stop_loss") or cached_pos.get("stop_loss") == 0:
                        await self.client.set_trading_stop(
                            symbol=symbol,
                            stop_loss=pos.stop_price,
                            take_profit=pos.tp1_price,
                        )
                    await self.state_machine.transition_to(PositionState.ACTIVE, "Live bracket confirmed")
                    pos.state = PositionState.ACTIVE
                    logger.info(f"⚡ [LIVE FILL CONFIRMED] {side.value} {pos.size} {symbol} @ ${pos.entry_price:.2f}")
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
            if cached_pos and cached_pos.get("size", 0) > 0:
                if not pos.tp1_filled and cached_pos["size"] != pos.size:
                    logger.info(f"🔄 Syncing position size for {symbol}: {pos.size} -> {cached_pos['size']}")
                    pos.size = cached_pos["size"]

            # 2. Monitor Active / Protected Live Positions
            if cur_price > 0:
                pnl_delta = (cur_price - entry) if side == OrderSide.BUY else (entry - cur_price)
                if pnl_delta > pos.mfe:
                    pos.mfe = pnl_delta
                    pos.time_to_mfe = now - pos.entry_time
                if pnl_delta < pos.mae:
                    pos.mae = pnl_delta
                    pos.time_to_mae = now - pos.entry_time

            # Check if position closed on exchange (SL hit or external close)
            exchange_closed = (cached_pos is None or cached_pos.get("size", 0) <= 0)
            if exchange_closed:
                exit_p = cur_price if cur_price > 0 else sl
                pnl = (exit_p - entry) * size if side == OrderSide.BUY else (entry - exit_p) * size
                pnl_pct = (pnl / (entry * size)) * 100 * pos.leverage if (entry * size) > 0 else 0.0
                self.live_positions.pop(symbol, None)

                closed_trade = {
                    "id": pos.client_order_id,
                    "timestamp": now,
                    "symbol": symbol,
                    "side": side.value,
                    "strategy": pos.strategy.value if hasattr(pos.strategy, "value") else str(pos.strategy),
                    "regime": pos.regime.value if hasattr(pos.regime, "value") else str(pos.regime),
                    "entry": entry,
                    "exit_price": exit_p,
                    "stop_price": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "quantity": size,
                    "leverage": pos.leverage,
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "mae": round(pos.mae, 2),
                    "mfe": round(pos.mfe, 2),
                    "hold_time": round(now - pos.entry_time, 1),
                    "exit_reason": "Exchange Position Closed (SL/TP)",
                    "attribution": FailureType.NORMAL_WIN.value if pnl >= 0 else FailureType.NORMAL_LOSS.value,
                    "status": "WIN" if pnl >= 0 else "LOSS",
                }
                closed_records.append(closed_trade)
                await self.state_machine.transition_to(PositionState.CLOSED, "Exchange position closed")
                await self.state_machine.transition_to(PositionState.FLAT, "Resetting desk to FLAT")
                logger.info(f"🏁 [LIVE POSITION CLOSED] {side.value} {symbol} closed (Estimated PnL: ${pnl:+.2f})")
                continue

            # 3. TP1 Check: partial close 50% & move stop to breakeven + buffer
            if cur_price > 0:
                hit_tp1 = (cur_price >= tp1) if side == OrderSide.BUY else (cur_price <= tp1)
                if hit_tp1 and not pos.tp1_filled:
                    realized_qty = self.instrument_repo.round_qty(symbol, size * 0.50)
                    close_side = "Sell" if side == OrderSide.BUY else "Buy"
                    tp1_success = False
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
                        else:
                            logger.error(f"Bybit rejected TP1 close order: {res.get('retMsg')}")
                    except Exception as e:
                        logger.error(f"Failed to place live TP1 close order: {e}")

                    if tp1_success:
                        pos.tp1_filled = True
                        pos.size = max(0.0, pos.size - realized_qty)
                        be_buffer = entry * 0.0005
                        new_stop = (entry + be_buffer) if side == OrderSide.BUY else (entry - be_buffer)
                        new_stop = self.instrument_repo.round_price(symbol, new_stop)
                        if self.state_machine.validate_stop_update(pos.stop_price, new_stop, is_long=(side == OrderSide.BUY)):
                            pos.stop_price = new_stop
                            try:
                                await self.client.set_trading_stop(symbol=symbol, stop_loss=new_stop)
                            except Exception as err:
                                logger.error(f"Failed to move server stop to BE on Bybit: {err}")

                        await self.state_machine.transition_to(PositionState.TP1_FILLED, "TP1 reached (50% live closed)")
                        await self.state_machine.transition_to(PositionState.PROTECTED_RUNNER, "Live stop moved to Breakeven + buffer")
                        logger.info(f"🎯 [LIVE TP1 HIT] 50% closed @ ${tp1:.2f}. Stop moved to ${new_stop:.2f}")

            # 4. TP2 Check: Runner Target Close (100% of remaining position)
            if cur_price > 0:
                hit_tp2 = (cur_price >= tp2) if side == OrderSide.BUY else (cur_price <= tp2)
                if hit_tp2 and pos.tp1_filled:
                    close_side = "Sell" if side == OrderSide.BUY else "Buy"
                    rem_qty = self.instrument_repo.round_qty(symbol, pos.size)
                    tp2_success = False
                    try:
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
                        total_pnl = ((tp1 - entry) * (size * 0.5) + (tp2 - entry) * rem_qty) if side == OrderSide.BUY else (
                            (entry - tp1) * (size * 0.5) + (entry - tp2) * rem_qty
                        )
                        pnl_pct = (total_pnl / (entry * size)) * 100 * pos.leverage if (entry * size) > 0 else 0.0
                        self.live_positions.pop(symbol, None)

                        closed_trade = {
                            "id": pos.client_order_id,
                            "timestamp": now,
                            "symbol": symbol,
                            "side": side.value,
                            "strategy": pos.strategy.value if hasattr(pos.strategy, "value") else str(pos.strategy),
                            "regime": pos.regime.value if hasattr(pos.regime, "value") else str(pos.regime),
                            "entry": entry,
                            "exit_price": tp2,
                            "stop_price": sl,
                            "tp1": tp1,
                            "tp2": tp2,
                            "quantity": size,
                            "leverage": pos.leverage,
                            "pnl": round(total_pnl, 2),
                            "pnl_pct": round(pnl_pct, 2),
                            "mae": round(pos.mae, 2),
                            "mfe": round(pos.mfe, 2),
                            "hold_time": round(now - pos.entry_time, 1),
                            "exit_reason": "TP2 Target Reached (Live)",
                            "attribution": FailureType.NORMAL_WIN.value,
                            "status": "WIN",
                        }
                        closed_records.append(closed_trade)
                        await self.state_machine.transition_to(PositionState.CLOSED, "TP2 Hit")
                        await self.state_machine.transition_to(PositionState.FLAT, "Desk reset to FLAT")
                        logger.info(f"🏆 [LIVE TP2 FULL TARGET] {side.value} {symbol} closed @ ${tp2:.2f} (Total PnL: ${total_pnl:+.2f})")
                        continue

            # 5. Time-Based Thesis Invalidation
            trade_duration = now - pos.entry_time
            if trade_duration > pos.max_thesis_lifetime and not pos.tp1_filled:
                logger.warning(f"⏳ Time-based thesis invalidation for live position {symbol} ({trade_duration:.0f}s > {pos.max_thesis_lifetime}s)")
                close_side = "Sell" if side == OrderSide.BUY else "Buy"
                exit_qty = self.instrument_repo.round_qty(symbol, pos.size)
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

                pnl = (cur_price - entry) * size if side == OrderSide.BUY else (entry - cur_price) * size
                pnl_pct = (pnl / (entry * size)) * 100 * pos.leverage if (entry * size) > 0 else 0.0
                self.live_positions.pop(symbol, None)

                closed_trade = {
                    "id": pos.client_order_id,
                    "timestamp": now,
                    "symbol": symbol,
                    "side": side.value,
                    "strategy": pos.strategy.value if hasattr(pos.strategy, "value") else str(pos.strategy),
                    "regime": pos.regime.value if hasattr(pos.regime, "value") else str(pos.regime),
                    "entry": entry,
                    "exit_price": cur_price,
                    "stop_price": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "quantity": size,
                    "leverage": pos.leverage,
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "mae": round(pos.mae, 2),
                    "mfe": round(pos.mfe, 2),
                    "hold_time": round(trade_duration, 1),
                    "exit_reason": "Time-Based Thesis Invalidation",
                    "attribution": FailureType.NORMAL_LOSS.value if pnl < 0 else FailureType.NORMAL_WIN.value,
                    "status": "WIN" if pnl >= 0 else "LOSS",
                }
                closed_records.append(closed_trade)
                await self.state_machine.transition_to(PositionState.CLOSED, "Time Expiry")
                await self.state_machine.transition_to(PositionState.FLAT, "Desk reset to FLAT")

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
            if exec_qty > 0 and pos.state in (PositionState.ENTRY_PENDING, PositionState.PARTIALLY_FILLED):
                pos.filled_quantity += exec_qty
                if exec_price > 0:
                    pos.entry_price = exec_price
                logger.info(f"⚡ [PRIVATE WS FILL] {symbol}: filled {exec_qty} (total {pos.filled_quantity}/{pos.size}) @ ${exec_price}")

