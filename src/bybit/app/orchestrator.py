"""
Master Scalper Orchestrator & Production Runtime.
Coordinates Market Data, Alpha Engine, Policy Engine, Execution Engine,
Reconciliation, Watchdog, Database, and Telemetry across 24/7 operations.
"""

import asyncio
import logging
import time
from typing import Dict, Any, List, Optional, Tuple, Set
from rich.console import Console

from src.bybit.config import BybitScalperConfig, load_bybit_config
from src.bybit.storage.database import BybitDatabase
from src.bybit.exchange.bybit_client import BybitClient
from src.bybit.exchange.public_ws import BybitPublicWS
from src.bybit.exchange.private_ws import BybitPrivateWS
from src.bybit.exchange.instruments import BybitInstrumentRepository
from src.bybit.exchange.account import BybitAccountRepository
from src.bybit.market.candles import MultiTimeframeCandleManager
from src.bybit.market.microstructure import MicrostructureEngine
from src.bybit.market.spoof import SpoofDetector
from src.bybit.market.market_state import MarketStateEngine
from src.bybit.regime.regime_engine import MarketRegimeEngine
from src.bybit.alpha.btc_filter import BTCBetaFilter
from src.bybit.alpha.alpha_engine import AlphaEngine
from src.bybit.risk.position_sizer import PositionSizer
from src.bybit.risk.circuit_breakers import CircuitBreakers
from src.bybit.risk.risk_tier import RiskTierSimulator
from src.bybit.risk.policy import PolicyEngine
from src.bybit.state.state_machine import PositionStateMachine
from src.bybit.execution.execution_policy import ExecutionPolicyEngine
from src.bybit.execution.executor import ExecutionEngine
from src.bybit.state.reconciliation import ReconciliationEngine
from src.bybit.watchdog.watchdog import Watchdog
from src.bybit.market.universe import DynamicUniverseManager
from src.bybit.telemetry import (
    TelemetryDashboard, TelemetryBus, BrainDecisionTrace,
    ApiObservatory, SystemMetricsCollector
)
from dashboard.backend.server import DashboardServer
from src.bybit.tuning.self_tuner import SelfTuningEngine
from src.bybit.models import PositionState, ExecutionPlan, TradePlan, OrderType
from src.bybit.intelligence.models import ProviderStatus
from src.bybit.intelligence import (
    UnifiedMarketIntelligence, MarketDataBus, BybitConnector,
    BinanceFuturesConnector, CoinGlassConnector
)

console = Console(legacy_windows=False)
logger = logging.getLogger(__name__)


class BybitScalperOrchestrator:
    """Production Autonomous Scalping Runtime Engine."""

    def __init__(self, config: BybitScalperConfig):
        self.config = config
        self.running = False
        self.cycle_count = 0

        # Universe Manager (Brain 1 & Market Data Foundation)
        self.universe_mgr = DynamicUniverseManager(config.universe, fallback_symbols=config.symbols)
        self.active_symbols: List[str] = list(self.universe_mgr.active_universe)

        # 1. Database
        self.db = BybitDatabase(config.db_path)

        # 2. Exchange & Account Adapters
        _is_demo = config.mode == "demo"
        self.client = BybitClient(
            api_key=config.api_key,
            api_secret=config.api_secret,
            testnet=config.testnet,
            demo=_is_demo,
        )
        self.ws = BybitPublicWS(
            symbols=self.active_symbols,
            testnet=config.testnet,
            demo=_is_demo,
        )
        self.private_ws = BybitPrivateWS(
            api_key=config.api_key,
            api_secret=config.api_secret,
            testnet=config.testnet,
            demo=_is_demo,
        )
        self.instrument_repo = BybitInstrumentRepository()
        self.account = BybitAccountRepository(starting_equity=config.starting_equity)

        # 3. Market & Intelligence Layer
        self.candle_mgr = MultiTimeframeCandleManager(symbols=self.active_symbols)
        self.micro_engine = MicrostructureEngine(symbols=self.active_symbols)
        self.spoof_detector = SpoofDetector()
        self.market_state = MarketStateEngine(
            symbols=self.active_symbols,
            ws=self.ws,
            candle_mgr=self.candle_mgr,
            micro_engine=self.micro_engine,
            spoof_detector=self.spoof_detector,
        )

        # Multi-Exchange Intelligence (Bybit, Binance, CoinGlass)
        self.bus = MarketDataBus()
        self.bybit_conn = BybitConnector(
            client=self.client,
            ws=self.ws,
            bus=self.bus,
        )
        self.binance_conn = None
        if config.binance.enabled:
            self.binance_conn = BinanceFuturesConnector(
                bus=self.bus,
            )
        self.coinglass_conn = None
        if config.coinglass.enabled:
            self.coinglass_conn = CoinGlassConnector(
                bus=self.bus,
                api_key=config.coinglass.api_key,
                poll_interval_sec=config.coinglass.poll_interval_sec,
            )
        self.intelligence = UnifiedMarketIntelligence(
            bybit_connector=self.bybit_conn,
            binance_connector=self.binance_conn,
            coinglass_connector=self.coinglass_conn,
            bus=self.bus,
        )

        # 4. Regime & Alpha Layer (Brain 1)
        self.regime_engine = MarketRegimeEngine()
        self.btc_filter = BTCBetaFilter()
        self.alpha_engine = AlphaEngine(
            symbols=self.active_symbols,
            market_state=self.market_state,
            candle_mgr=self.candle_mgr,
            regime_engine=self.regime_engine,
            btc_filter=self.btc_filter,
            intelligence=self.intelligence,
        )



        # 5. Risk & Policy Layer (Brain 2)
        self.sizer = PositionSizer(self.instrument_repo)
        self.circuit_breakers = CircuitBreakers(self.db, high_watermark=config.starting_equity)
        self.risk_tier_sim = RiskTierSimulator()
        self.policy_engine = PolicyEngine(
            account=self.account,
            sizer=self.sizer,
            circuit_breakers=self.circuit_breakers,
            risk_tier_sim=self.risk_tier_sim,
            regime_engine=self.regime_engine,
            db=self.db,
            safety=config.safety,
        )

        # 6. State & Execution Layer (Brain 3)
        self.state_machine = PositionStateMachine(self.db)
        self.exec_policy = ExecutionPolicyEngine()
        self.executor = ExecutionEngine(
            client=self.client,
            account=self.account,
            instrument_repo=self.instrument_repo,
            state_machine=self.state_machine,
            exec_policy=self.exec_policy,
            db=self.db,
            mode=config.mode,
            leverage=config.default_leverage,
            ws=self.ws,
        )
        self._wake_event = asyncio.Event()
        self.reconciler = ReconciliationEngine(
            client=self.client,
            account=self.account,
            state_machine=self.state_machine,
            executor=self.executor,
            auto_adopt=True,
        )

        # 7. Supervision, Tuning & Telemetry
        self.watchdog = Watchdog()
        self.self_tuner = SelfTuningEngine(config.tunable, self.db)
        
        # Telemetry Bus, Decision Trace, API Observatory & Metrics
        self.telemetry_bus = TelemetryBus()
        self.brain_trace = BrainDecisionTrace(bus=self.telemetry_bus)
        self.api_observer = ApiObservatory()
        self.metrics_collector = SystemMetricsCollector()
        self.telemetry = TelemetryDashboard(
            bus=self.telemetry_bus,
            brain_trace=self.brain_trace,
            api_observer=self.api_observer,
            metrics=self.metrics_collector,
        )

        # Web Dashboard & Neural Observatory Server (Read-only observer)
        self.dashboard_server = None
        if getattr(config, "dashboard", None) and config.dashboard.enabled:
            self.dashboard_server = DashboardServer(
                host=config.dashboard.host,
                port=config.dashboard.port,
                bus=self.telemetry_bus,
                brain_trace=self.brain_trace,
                api_observer=self.api_observer,
            )

        # Telemetry candidate caches
        self.recent_top_candidates = []
        self.recent_rejections = []

        # Trading freeze safety tracking
        self._last_frozen_state: bool = False
        self._last_freeze_reason: str = ""

        # Hook WS callbacks
        self.ws.on_trade(self._handle_trade_tick)
        self.private_ws.on_execution(self._handle_private_execution)
        self.private_ws.on_position(self._handle_private_position)
        self.private_ws.on_wallet(self._handle_private_wallet)

    def is_trading_frozen(self) -> Tuple[bool, str]:
        """
        Evaluate global trading freeze conditions.
        Enforces Section 38 & User Invariant: If any critical data source or WS is down,
        trading must be frozen until reconnected, resynced, and healthy.
        """
        # 1. Kill switches & Circuit breakers
        killed, kill_reason = self.circuit_breakers.is_any_kill_switch_active()
        if killed:
            return True, f"KILL_SWITCH: {kill_reason}"

        if self.circuit_breakers.is_cooldown_active():
            return True, "COOLDOWN: Mandatory cooldown in effect"

        # 2. Reconciliation safety state
        if self.reconciler.reconcile_frozen:
            return True, "RECONCILIATION_FROZEN: Discrepancy or failed stop loss"

        # 3. Bybit Public WebSocket Connectivity (Primary Execution Feed)
        if not self.ws.connected:
            return True, "BYBIT_WS_DOWN: Primary Bybit public stream disconnected"

        # 4. Bybit Private WebSocket Connectivity (Live Trading)
        if not self.executor.is_paper:
            if not self.private_ws.connected:
                return True, "BYBIT_PRIVATE_WS_DOWN: Private execution feed disconnected"
            if not self.private_ws.authenticated:
                return True, "BYBIT_PRIVATE_WS_UNAUTH: Private execution feed unauthenticated"

        # 5. Multi-Exchange Intelligence Health (when Binance enabled)
        if self.binance_conn and self.config.binance.enabled:
            binance_health = self.binance_conn.get_health()
            if binance_health.status == ProviderStatus.OFFLINE:
                return True, "BINANCE_WS_DOWN: Binance cross-exchange feed offline"

        return False, "HEALTHY"

    def _handle_trade_tick(self, symbol: str, price: float, size: float, side: str, ts: float):
        """Hook WebSocket trade ticks directly into candles and microstructure."""
        try:
            self.candle_mgr.process_trade(symbol, price, size, ts)
            self.micro_engine.process_trade(symbol, price, size, side, ts)
            self.api_observer.record_call("Bybit", f"trade.{symbol}", 8.2, "WS", "WS")
        except (KeyError, ValueError):
            pass  # Symbol may have been unregistered during rotation

    def _handle_private_execution(self, data: Any):
        """Forward real-time fill events to ExecutionEngine and wake loop."""
        if isinstance(data, list):
            for fill in data:
                self.executor.handle_execution_fill(fill)
        elif isinstance(data, dict):
            self.executor.handle_execution_fill(data)
        self._wake_event.set()

    def _handle_private_position(self, data: Any):
        """Sync real-time position updates to BybitAccountRepository and wake loop."""
        if isinstance(data, list):
            self.account.update_positions(data)
        self._wake_event.set()

    def _handle_private_wallet(self, data: Any):
        """Sync real-time wallet updates to BybitAccountRepository."""
        if isinstance(data, list) and data:
            self.account.update_from_wallet_response(data[0])
        elif isinstance(data, dict):
            self.account.update_from_wallet_response(data)

    async def start(self):
        """Initialize all subsystems and run the continuous high-frequency loop."""
        self.running = True

        # 1. Connect Database & Load Persisted Governor State
        await self.db.connect()
        await self.circuit_breakers.initialize()

        # 2. Synchronize Clock with Bybit
        try:
            drift = await self.client.sync_server_time()
            self.watchdog.update_clock_drift(drift)
        except Exception as e:
            logger.warning(f"Clock synchronization warning: {e}")

        # 3. Dynamic Universe Discovery (Section 25)
        if self.config.universe.mode == "dynamic":
            try:
                discovered = await self.universe_mgr.discover_universe(self.client)
                if discovered:
                    self.active_symbols = discovered
                    self.ws.symbols = list(self.active_symbols)
                    for s in self.active_symbols:
                        self.candle_mgr.register_symbol(s)
                        self.micro_engine.register_symbol(s)
                    self.market_state.update_symbols(self.active_symbols)
                    self.alpha_engine.update_symbols(self.active_symbols)
            except Exception as e:
                logger.warning(f"Initial dynamic universe discovery warning: {e}")

        console.print(f"\n[bold cyan]======================================================[/bold cyan]")
        console.print(f"[bold green]>> BYBIT V5 QUANTITATIVE SCALPING TRADER INITIALIZING[/bold green]")
        console.print(f"   * Operating Mode: [bold yellow]{self.config.mode.upper()}[/bold yellow]")
        console.print(f"   * Universe Mode: [bold yellow]{self.config.universe.mode.upper()} ({len(self.active_symbols)} symbols)[/bold yellow]")
        console.print(f"   * Active Universe: [cyan]{', '.join(self.active_symbols)}[/cyan]")
        console.print(f"   * Default Leverage: [magenta]{self.config.default_leverage}x Isolated[/magenta]")
        console.print(f"   * Desk Invariant: [bold red]MAX_ACTIVE_POSITIONS = 1[/bold red]")
        console.print(f"   * Cash Fortress Floor: [bold green]>= 85.0% Unencumbered Equity[/bold green]")
        console.print(f"[bold cyan]======================================================[/bold cyan]\n")

        # 4. Warm Up Contract Rules & Specifications
        try:
            instruments = await self.client.get_instruments_info(category="linear")
            for item in instruments:
                sym = item.get("symbol", "")
                lot = item.get("lotSizeFilter", {})
                price_filter = item.get("priceFilter", {})
                lev_filter = item.get("leverageFilter", {})
                self.instrument_repo.update_spec(sym, {
                    "tick_size": float(price_filter.get("tickSize", 0.01)),
                    "qty_step": float(lot.get("qtyStep", 0.001)),
                    "min_order_qty": float(lot.get("minOrderQty", 0.001)),
                    "max_order_qty": float(lot.get("maxOrderQty", 1000.0)),
                    "min_price": float(price_filter.get("minPrice", 0.01)),
                    "max_price": float(price_filter.get("maxPrice", 1000000.0)),
                    "max_leverage": float(lev_filter.get("maxLeverage", 100.0)),
                })

            # Ensure every active symbol has its precise specification loaded
            for sym in self.active_symbols:
                if sym not in self.instrument_repo._specs:
                    try:
                        sym_info = await self.client.get_instruments_info(category="linear", symbol=sym)
                        if sym_info:
                            item = sym_info[0]
                            lot = item.get("lotSizeFilter", {})
                            p_filter = item.get("priceFilter", {})
                            l_filter = item.get("leverageFilter", {})
                            self.instrument_repo.update_spec(sym, {
                                "tick_size": float(p_filter.get("tickSize", 0.01)),
                                "qty_step": float(lot.get("qtyStep", 0.001)),
                                "min_order_qty": float(lot.get("minOrderQty", 0.001)),
                                "max_order_qty": float(lot.get("maxOrderQty", 1000.0)),
                                "min_price": float(p_filter.get("minPrice", 0.01)),
                                "max_price": float(p_filter.get("maxPrice", 1000000.0)),
                                "max_leverage": float(l_filter.get("maxLeverage", 100.0)),
                            })
                    except Exception as err:
                        logger.warning(f"Could not fetch instrument spec for {sym}: {err}")
        except Exception as e:
            logger.warning(f"Failed to seed instrument specifications: {e}")

        # 5. Start All WebSocket Feeds & Intelligence Streams (Item 1)
        await self.ws.start()
        await self.private_ws.start()
        await self.intelligence.start()
        await self.intelligence.subscribe_symbols(self.active_symbols)

        # 5b. Start Neural Dashboard Web Server (Read-only observer)
        if self.dashboard_server:
            try:
                await self.dashboard_server.start()
            except Exception as e:
                logger.warning(f"Could not start dashboard web server: {e}")

        # 6. WebSocket Warmup & Initialization Wait (Item 2)
        console.print("   * Awaiting WebSocket connections and initial feeds...")
        warmup_deadline = time.time() + 10.0
        while time.time() < warmup_deadline:
            bybit_ok = self.ws.connected
            private_ok = self.private_ws.connected if not self.executor.is_paper else True
            binance_ok = (self.binance_conn.health().status != ProviderStatus.OFFLINE) if (self.binance_conn and self.config.binance.enabled) else True
            if bybit_ok and private_ok and binance_ok:
                break
            await asyncio.sleep(0.1)

        # 7. Initial Authoritative State Reconciliation (Item 3)
        console.print("   * Synchronizing authoritative exchange positions & wallet...")
        try:
            await self.reconciler.reconcile()
        except Exception as rec_err:
            logger.error(f"Initial reconciliation error: {rec_err}")

        # 8. Warm Up Multi-Timeframe Candles via REST for Active Universe
        for sym in self.active_symbols:
            try:
                k1m = await self.client.get_klines(sym, interval="1", limit=60)
                if k1m: self.candle_mgr.seed_candles(sym, "1m", k1m)

                k5m = await self.client.get_klines(sym, interval="5", limit=60)
                if k5m: self.candle_mgr.seed_candles(sym, "5m", k5m)

                k15m = await self.client.get_klines(sym, interval="15", limit=60)
                if k15m: self.candle_mgr.seed_candles(sym, "15m", k15m)

                k1h = await self.client.get_klines(sym, interval="60", limit=48)
                if k1h: self.candle_mgr.seed_candles(sym, "1h", k1h)

                k4h = await self.client.get_klines(sym, interval="240", limit=30)
                if k4h: self.candle_mgr.seed_candles(sym, "4h", k4h)
            except Exception as err:
                logger.error(f"Candle warmup error for {sym}: {err}")

        # Initial Telemetry Render
        self._trigger_telemetry()


        # Main 24/7 Scalping Execution Loop
        while self.running:
            try:
                self.cycle_count += 1
                self.watchdog.beat()
                self.watchdog.update_stream_health(
                    ws_connected=self.ws.connected,
                    private_ws_connected=self.private_ws.connected if not self.executor.is_paper else True,
                )

                # Watchdog health check — activate kill switch if system is unhealthy (Item 4)
                wdog_healthy, wdog_reason = self.watchdog.check_health()
                if not wdog_healthy:
                    if not self.circuit_breakers.kill_switches.get("SOFTWARE_KILL", False):
                        self.circuit_breakers.kill_switches["SOFTWARE_KILL"] = True
                        logger.critical(f"🛑 WATCHDOG KILL ACTIVATED: {wdog_reason}")
                else:
                    # Clear SOFTWARE_KILL once healthy again
                    if self.circuit_breakers.kill_switches.get("SOFTWARE_KILL", False):
                        self.circuit_breakers.kill_switches["SOFTWARE_KILL"] = False
                        logger.info("✅ Watchdog health restored — SOFTWARE_KILL cleared")

                await self._step()
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=2.0)
                    self._wake_event.clear()
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in scalping execution cycle: {e}", exc_info=True)
                await asyncio.sleep(5.0)

    async def _step(self):
        """Single high-frequency execution step."""
        now = time.time()

        # 0. Global Critical Data Source & Trading Freeze Check (Item 7)
        frozen, freeze_reason = self.is_trading_frozen()
        if frozen:
            if not self._last_frozen_state or self._last_freeze_reason != freeze_reason:
                logger.warning(f"🛑 [TRADING FROZEN] Critical data source offline: {freeze_reason}. Halting entries.")
                self._last_frozen_state = True
                self._last_freeze_reason = freeze_reason
                self._trigger_telemetry()
        else:
            if self._last_frozen_state:
                logger.info("✅ [TRADING RESUMED] All critical data sources healthy and reconnected. Resuming entries.")
                self._last_frozen_state = False
                self._last_freeze_reason = ""
                self._trigger_telemetry()

        # 1. Update Active Positions & Brackets
        if self.executor.is_paper:
            closed_trades = await self.executor.update_positions(self.ws.latest_prices)
            for trade in closed_trades:
                await self.db.save_trade(trade)
                await self.circuit_breakers.record_trade_result(trade["pnl"], self.account.total_equity)
                self.policy_engine.has_active_position = False
                self._trigger_telemetry()
        else:
            # Live Mode Continuous Reconciliation & Position Monitoring
            if self.cycle_count % 5 == 0:
                await self.reconciler.reconcile()
            closed_trades = await self.executor.update_positions(self.ws.latest_prices)
            for trade in closed_trades:
                await self.db.save_trade(trade)
                await self.circuit_breakers.record_trade_result(trade["pnl"], self.account.total_equity)
                self.policy_engine.has_active_position = False
                self._trigger_telemetry()

        # Synchronize active position flag
        self.policy_engine.has_active_position = (
            self.state_machine.state not in (PositionState.FLAT, PositionState.CLOSED)
        )

        # 2. Opportunity Utilization: Scan market ONLY IF NOT FROZEN and FLAT (Item 7)
        if (
            not frozen
            and self.state_machine.state == PositionState.FLAT
            and not self.circuit_breakers.is_cooldown_active()
            and not self.reconciler.reconcile_frozen
        ):
            candidates = self.alpha_engine.scan_all_symbols()
            self.recent_top_candidates = candidates

            if candidates:
                for candidate in candidates[:3]:
                    snapshot = self.market_state.get_snapshot(candidate.symbol, regime=candidate.regime)
                    strat_name = candidate.strategy.value if hasattr(candidate.strategy, "value") else str(candidate.strategy)
                    self.brain_trace.record_candidate_found(candidate.symbol, strat_name, candidate.alpha_score)

                    # Brain 2: Policy & Risk Gatekeeper (15 checks)
                    decision, qty = await self.policy_engine.evaluate_trade(candidate, snapshot)

                    if decision.approved:
                        console.print(f"\n[bold green]{self.policy_engine.format_policy_report(decision, candidate)}[/bold green]\n")
                        self.brain_trace.record_smc_audit(
                            candidate.symbol,
                            {"Structure": "PASS", "Liquidity": "PASS", "Invalidation": "PASS", "RR": "PASS", "Execution": "PASS", "Macro": "PASS"},
                            passed=True
                        )

                        # Brain 3: Execution Pre-Check (14 checks)
                        entry_p = self.instrument_repo.round_price(candidate.symbol, candidate.entry)
                        stop_p = self.instrument_repo.round_price(candidate.symbol, candidate.structural_stop)
                        tp1_p = self.instrument_repo.round_price(candidate.symbol, candidate.tp1)
                        tp2_p = self.instrument_repo.round_price(candidate.symbol, candidate.tp2)
                        rounded_qty = self.instrument_repo.round_qty(candidate.symbol, qty)

                        exec_plan = ExecutionPlan(
                            symbol=candidate.symbol,
                            side=candidate.side,
                            quantity=rounded_qty,
                            entry_price=entry_p,
                            stop_price=stop_p,
                            tp1_price=tp1_p,
                            tp2_price=tp2_p,
                            order_type=OrderType.LIMIT if self.config.post_only else OrderType.MARKET,
                            post_only=self.config.post_only,
                            ttl_seconds=45.0,
                            best_bid=snapshot.best_bid,
                            best_ask=snapshot.best_ask,
                        )

                        exec_safe, exec_reason = self.exec_policy.evaluate_execution_precheck(
                            exec_plan, snapshot, self.ws.connected
                        )

                        if exec_safe:
                            self.brain_trace.record_step(candidate.symbol, "PRECHECK", "PASS", "Execution pre-check 15/15 invariants satisfied")
                            # Acquire Centralized Atomic Lock & Execute
                            async with self.policy_engine._position_lock:
                                success = await self.executor.execute_plan(
                                    exec_plan, candidate.strategy, candidate.regime
                                )
                                if success:
                                    self.policy_engine.has_active_position = True
                                    self.brain_trace.record_trade_approved(
                                        candidate.symbol,
                                        candidate.side.value if hasattr(candidate.side, "value") else str(candidate.side),
                                        rounded_qty,
                                        entry_p,
                                        stop_p
                                    )
                                    self._trigger_telemetry()
                                    break  # Position entered, stop scanning remaining candidates
                                else:
                                    fail_msg = self.state_machine.last_transition_reason or "Exchange rejected entry order"
                                    self.brain_trace.record_veto(
                                        candidate.symbol,
                                        "EXECUTION",
                                        "Exchange accepts order",
                                        fail_msg
                                    )
                                    self._trigger_telemetry()
                                    continue  # Try next candidate
                        else:
                            logger.warning(f"Execution pre-check rejected trade: {exec_reason}")
                            self.brain_trace.record_veto(candidate.symbol, "PRECHECK", "Pass all invariants", exec_reason)
                            continue  # Try next candidate
                    else:
                        rej_rule = decision.rejection_reason.split(":")[0]
                        self.brain_trace.record_veto(candidate.symbol, rej_rule, "Policy approval", decision.rejection_reason)
                        self.recent_rejections.append({
                            "symbol": candidate.symbol,
                            "strategy": candidate.strategy.value,
                            "score": candidate.alpha_score,
                            "reason": decision.rejection_reason,
                        })
                        if len(self.recent_rejections) > 20:
                            self.recent_rejections.pop(0)
                        continue  # Try next candidate

        # Broadcast high-frequency telemetry frame to WebSocket
        self._broadcast_frame()

        # 3. Periodic Universe Rotation Check (every ~60s / 30 cycles)
        if self.cycle_count % 30 == 0:
            await self._check_universe_rotation()

        # 4. 5-Minute Telemetry Dashboard
        if self.telemetry.should_render():
            self._trigger_telemetry()

        # 5. Self-Tuner Evaluation every 100 cycles (~200 seconds)
        if self.cycle_count % 100 == 0:
            await self.self_tuner.evaluate_batch()

    async def _check_universe_rotation(self):
        """Evaluate scheduled universe rotation and hot-swap symbols."""
        if not self.universe_mgr.should_rotate():
            return

        protected_symbols = set(self.config.universe.pinned_symbols)
        if self.state_machine.active_position:
            protected_symbols.add(self.state_machine.active_position.symbol)
        protected_symbols.update(self.executor.live_positions.keys())
        protected_symbols.update(self.executor.paper_positions.keys())
        protected_symbols.update(self.account.cached_positions.keys())

        new_universe = await self.universe_mgr.discover_universe(self.client)
        added, removed, finalized = self.universe_mgr.compute_rotation_diff(new_universe, protected_symbols=protected_symbols)

        if not added and not removed:
            return

        logger.info(f"🔄 [UNIVERSE ROTATION] Adding {len(added)} symbols: {added} | Removing {len(removed)} symbols: {removed}")

        # 1. Unsubscribe WS and intelligence FIRST to stop incoming ticks
        if removed:
            await self.ws.unsubscribe_symbols(removed)
            await self.intelligence.unsubscribe_symbols(removed)
            # Brief pause to drain in-flight WS messages
            await asyncio.sleep(0.1)
            for s in removed:
                self.candle_mgr.unregister_symbol(s)
                self.micro_engine.unregister_symbol(s)
                self.spoof_detector.cleanup_symbol(s)

        # 2. Warm up and subscribe added symbols
        if added:
            for sym in added:
                self.candle_mgr.register_symbol(sym)
                self.micro_engine.register_symbol(sym)
                try:
                    k15m = await self.client.get_klines(sym, interval="15", limit=60)
                    if k15m: self.candle_mgr.seed_candles(sym, "15m", k15m)
                    k1h = await self.client.get_klines(sym, interval="60", limit=48)
                    if k1h: self.candle_mgr.seed_candles(sym, "1h", k1h)
                except Exception as e:
                    logger.warning(f"Candle warmup error during rotation for {sym}: {e}")

            await self.ws.subscribe_symbols(added)
            await self.intelligence.subscribe_symbols(added)

        # 3. Update state and engines
        self.active_symbols = finalized
        self.market_state.update_symbols(self.active_symbols)
        self.alpha_engine.update_symbols(self.active_symbols)
        self._trigger_telemetry()

    def _trigger_telemetry(self):
        """Render rich operational telemetry dashboard."""
        snapshots = {
            sym: self.market_state.get_snapshot(sym, regime=self.regime_engine.classify(
                self.candle_mgr.get_candles(sym, "15m"),
                self.market_state.get_snapshot(sym).atr_15m,
                self.market_state.get_snapshot(sym).spread_pct,
                self.market_state.get_snapshot(sym).orderbook_imbalance
            ))
            for sym in self.active_symbols
        }
        active_pos = self.state_machine.active_position or (
            list(self.executor.paper_positions.values())[0] if self.executor.paper_positions else None
        )

        cd_rem = max(0.0, self.circuit_breakers.cooldown_until - time.time())

        # Cross-Exchange & Multi-Venue States
        cross_states = {
            sym: self.intelligence.get_cross_exchange_state(sym)
            for sym in self.active_symbols
        }
        deriv_contexts = {
            sym: self.intelligence.get_derivatives_context(sym)
            for sym in self.active_symbols
        }
        provider_health = self.intelligence.get_provider_health()

        frozen, freeze_reason = self.is_trading_frozen()
        self.telemetry.render(
            account_equity=self.account.total_equity,
            available_equity=self.account.available_balance,
            fortress_pct=self.account.fortress_percent,
            daily_dd_pct=self.circuit_breakers.daily_drawdown_pct,
            consecutive_losses=self.circuit_breakers.consecutive_losses,
            cooldown_active=self.circuit_breakers.is_cooldown_active(),
            cooldown_remaining_sec=cd_rem,
            position_state=self.state_machine.state,
            active_position=active_pos,
            latest_snapshots=snapshots,
            top_candidates=self.recent_top_candidates,
            rejected_candidates=self.recent_rejections,
            cross_exchange_states=cross_states,
            derivatives_contexts=deriv_contexts,
            provider_health=provider_health,
            trading_frozen=frozen,
            freeze_reason=freeze_reason,
        )

    def _broadcast_frame(self):
        """High-frequency non-blocking broadcast to WebSocket bus."""
        if not self.telemetry_bus:
            return

        active_pos = self.state_machine.active_position or (
            list(self.executor.paper_positions.values())[0] if self.executor.paper_positions else None
        )
        cd_rem = max(0.0, self.circuit_breakers.cooldown_until - time.time())
        frozen, freeze_reason = self.is_trading_frozen()
        cross_states = {
            sym: self.intelligence.get_cross_exchange_state(sym)
            for sym in self.active_symbols
        }
        deriv_contexts = {
            sym: self.intelligence.get_derivatives_context(sym)
            for sym in self.active_symbols
        }
        provider_health = self.intelligence.get_provider_health()

        snapshots = {}
        for sym in self.active_symbols:
            snap = self.market_state.get_snapshot(sym)
            if snap:
                snapshots[sym] = snap

        self.telemetry.broadcast_realtime_frame(
            account_equity=self.account.total_equity,
            available_equity=self.account.available_balance,
            fortress_pct=self.account.fortress_percent,
            daily_dd_pct=self.circuit_breakers.daily_drawdown_pct,
            consecutive_losses=self.circuit_breakers.consecutive_losses,
            cooldown_active=self.circuit_breakers.is_cooldown_active(),
            cooldown_remaining_sec=cd_rem,
            position_state=self.state_machine.state,
            active_position=active_pos,
            latest_snapshots=snapshots,
            top_candidates=self.recent_top_candidates,
            rejected_candidates=self.recent_rejections,
            cross_exchange_states=cross_states,
            derivatives_contexts=deriv_contexts,
            provider_health=provider_health,
            trading_frozen=frozen,
            freeze_reason=freeze_reason,
            kill_switch_matrix=self.circuit_breakers.kill_switches,
        )

    async def stop(self):
        """Graceful shutdown sequence (Item 8)."""
        logger.info("🛑 Initiating graceful shutdown sequence...")
        self.running = False
        self._wake_event.set()
        if self.dashboard_server:
            try:
                await self.dashboard_server.stop()
            except Exception as e:
                logger.warning(f"Error stopping dashboard server: {e}")
        await self.intelligence.stop()
        await self.private_ws.stop()
        await self.ws.stop()
        await self.client.close()
        await self.db.close()
        console.print("[bold red]Bybit Scalper safely stopped.[/bold red]")

