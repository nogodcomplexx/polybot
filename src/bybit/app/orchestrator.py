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
from src.bybit.utils import set_global_observatory
from src.bybit.models import PositionState, ExecutionPlan, TradePlan, OrderType, OrderSide
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
            tunable=config.tunable,
        )

        # 5. Risk & Policy Layer (Brain 2)
        self.sizer = PositionSizer(self.instrument_repo)
        cooldown_on = config.cooldown_enabled if config.cooldown_enabled is not None else (config.mode.lower() == "live")
        kill_switch_on = getattr(config, "kill_switch_enabled", None)
        if kill_switch_on is None:
            kill_switch_on = (config.mode.lower() == "live")
        self.circuit_breakers = CircuitBreakers(
            self.db,
            high_watermark=config.starting_equity,
            mode=config.mode,
            cooldown_enabled=cooldown_on,
            kill_switch_enabled=kill_switch_on,
        )
        self.inter_trade_delay_sec = getattr(config, "inter_trade_delay_sec", 5.0)
        self.symbol_cooldown_sec = getattr(config, "symbol_cooldown_sec", 60.0)
        self.last_trade_closed_time: float = 0.0
        self.symbol_cooldowns: Dict[str, float] = {}
        self.risk_tier_sim = RiskTierSimulator()

        self.policy_engine = PolicyEngine(
            account=self.account,
            sizer=self.sizer,
            circuit_breakers=self.circuit_breakers,
            risk_tier_sim=self.risk_tier_sim,
            regime_engine=self.regime_engine,
            db=self.db,
            safety=config.safety,
            symbol_cooldowns=self.symbol_cooldowns,
            tunable=config.tunable,
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
            max_thesis_lifetime=getattr(config, "max_thesis_lifetime_sec", 3600.0),
            min_take_profit_usd=getattr(config, "min_take_profit_usd", 50.0),
            breakeven_trigger_usd=getattr(config, "breakeven_trigger_usd", 25.0),
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
        self.state_machine.brain_trace = self.brain_trace
        self.reconciler.brain_trace = self.brain_trace
        self.executor.brain_trace = self.brain_trace
        self.api_observer = ApiObservatory(bus=self.telemetry_bus)
        set_global_observatory(self.api_observer)
        self.metrics_collector = SystemMetricsCollector()
        self.telemetry = TelemetryDashboard(
            bus=self.telemetry_bus,
            brain_trace=self.brain_trace,
            api_observer=self.api_observer,
            metrics=self.metrics_collector,
        )

        # Performance & ROI tracking
        self._cached_perf_stats: Dict[str, Any] = {}
        self._baseline_equity: float = 0.0

        # Web Dashboard & Neural Observatory Server (Read-only observer)
        self.dashboard_server = None
        if getattr(config, "dashboard", None) and config.dashboard.enabled:
            self.dashboard_server = DashboardServer(
                host=config.dashboard.host,
                port=config.dashboard.port,
                bus=self.telemetry_bus,
                brain_trace=self.brain_trace,
                api_observer=self.api_observer,
                orchestrator=self,
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
        self.private_ws.on_order(self._handle_private_order)
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
        positions = data if isinstance(data, list) else [data]
        if isinstance(data, list):
            self.account.update_positions(data)

        # Detect external position close (size dropped to 0 from SL/TP bracket or manual close)
        # MUST only trigger if desk was in an active/holding state, NEVER while ENTRY_PENDING!
        active_holding_states = {
            PositionState.ACTIVE, PositionState.TP1_FILLED, PositionState.PROTECTED_RUNNER,
            PositionState.DAMAGE_CONTROL, PositionState.EXITING, PositionState.PARTIALLY_FILLED,
        }
        for p in positions:
            size = float(p.get("size", 0) or 0)
            symbol = p.get("symbol", "").upper()
            if size == 0 and self.state_machine.state in active_holding_states:
                pos = self.state_machine.active_position
                if pos and pos.symbol.upper() == symbol:
                    logger.info(f"ℹ️ [EXTERNAL CLOSE] Position for {symbol} closed externally on Bybit. Recording trade & resetting desk.")
                    self.executor.live_positions.pop(symbol, None)
                    self.executor.paper_positions.pop(symbol, None)
                    self.state_machine.active_position = None
                    now = time.time()
                    self.last_trade_closed_time = now
                    self.symbol_cooldowns[symbol] = now + self.symbol_cooldown_sec
                    self.policy_engine.has_active_position = False
                    # Transition state immediately and synchronously so no stale background callback resets future trades
                    asyncio.create_task(self._reset_to_flat(f"Position {symbol} closed externally", expected_symbol=symbol))
                    # Background task ONLY handles Bybit API accounting and database storage
                    asyncio.create_task(self._handle_external_position_close(pos, symbol))

        self._wake_event.set()

    async def _handle_external_position_close(self, pos: Any, symbol: str):
        """Handle position closed externally on Bybit (SL/TP bracket execution or manual)."""
        exit_price = pos.stop_price
        closed_pnl = None
        exact_fees = None
        exit_reason = "Bybit Exchange Bracket Close"

        # In live/demo mode, query Bybit API for authoritative closed PnL and execution fees
        if not self.executor.is_paper:
            try:
                await asyncio.sleep(0.3)
                closed_pnl_list = await self.client.get_closed_pnl(symbol=symbol, limit=1)
                if closed_pnl_list:
                    rec = closed_pnl_list[0]
                    if float(rec.get("closedPnl", 0) or 0) != 0:
                        exit_price = float(rec.get("avgExitPrice", 0) or exit_price)
                        closed_pnl = float(rec.get("closedPnl", 0) or 0)
                        exit_reason = f"Bybit Server Bracket ({rec.get('execType', 'Trade')})"

                executions = await self.client.get_executions(symbol=symbol, limit=4)
                if executions:
                    total_fee = sum(float(ex.get("execFee", 0) or 0) for ex in executions[:2])
                    if total_fee > 0:
                        exact_fees = round(total_fee, 4)
            except Exception as e:
                logger.warning(f"Failed to fetch authoritative closed PnL from Bybit API: {e}")

        if exact_fees is None and getattr(pos, "fees_paid", 0.0) > 0:
            exact_fees = round(pos.fees_paid, 4)

        side = pos.side
        if closed_pnl is None:
            gross_pnl = (exit_price - pos.entry_price) * pos.size if side == OrderSide.BUY else (pos.entry_price - exit_price) * pos.size
        else:
            gross_pnl = closed_pnl + (exact_fees or 0.0)

        closed_trade = self.executor._build_closed_record(
            pos=pos,
            exit_price=exit_price,
            exit_reason=exit_reason,
            attribution="EXCHANGE_BRACKET",
            status="WIN" if (closed_pnl if closed_pnl is not None else gross_pnl) >= 0 else "LOSS",
            gross_pnl=gross_pnl,
            is_maker_exit=False,
            exact_fees=exact_fees,
            exact_net_pnl=closed_pnl,
        )

        await self.db.save_trade(closed_trade)
        await self.circuit_breakers.record_trade_result(closed_trade["pnl"], self.account.total_equity)
        self._cached_perf_stats = await self.db.get_performance_stats()

        self.brain_trace.record_step(
            symbol=symbol,
            stage="EXECUTION",
            status="PASS" if closed_trade["pnl"] >= 0 else "INFO",
            message=f"Closed {pos.side.value if hasattr(pos.side, 'value') else pos.side} {symbol} via {exit_reason} @ ${exit_price:.4f} (Net PnL: ${closed_trade['pnl']:+.2f}, Fees: ${closed_trade['fees']:.2f})"
        )

    def _handle_private_order(self, data: Any):
        """Sync real-time order updates and handle external cancel/deactivation."""
        orders = data if isinstance(data, list) else [data]
        if isinstance(data, list):
            self.account.update_orders(data)

        for o in orders:
            status = o.get("orderStatus", "")
            symbol = o.get("symbol", "").upper()
            order_link_id = o.get("orderLinkId", "")
            # If resting limit entry order was cancelled or deactivated externally
            if status in ("Cancelled", "Deactivated", "Rejected"):
                if self.state_machine.state == PositionState.ENTRY_PENDING:
                    pos = self.state_machine.active_position
                    if pos and pos.symbol.upper() == symbol:
                        # Verify this cancel update belongs to the active entry order
                        if not order_link_id or order_link_id == pos.client_order_id:
                            logger.info(f"ℹ️ [EXTERNAL CANCEL] Entry order for {symbol} was {status} on Bybit. Resetting desk to FLAT.")
                            self.executor.live_positions.pop(symbol, None)
                            self.executor.paper_positions.pop(symbol, None)
                            self.symbol_cooldowns[symbol] = time.time() + 30.0
                            asyncio.create_task(self._reset_to_flat(f"Order {status} externally", expected_symbol=symbol))

        self._wake_event.set()

    async def _reset_to_flat(self, reason: str, expected_symbol: Optional[str] = None):
        """Cleanly transition state machine and reset desk state to FLAT."""
        old_pos = self.state_machine.active_position
        if expected_symbol and old_pos and old_pos.symbol.upper() != expected_symbol.upper():
            logger.warning(
                f"Ignoring stale _reset_to_flat for {expected_symbol}: "
                f"current active position is on {old_pos.symbol}"
            )
            return

        # If resetting while an entry was pending, make sure any resting order is cancelled on Bybit
        if old_pos and self.state_machine.state == PositionState.ENTRY_PENDING:
            if not expected_symbol or old_pos.symbol.upper() == expected_symbol.upper():
                try:
                    await self.client.cancel_order(old_pos.symbol, order_link_id=old_pos.client_order_id)
                except Exception as e:
                    logger.debug(f"Cleanup cancel during reset_to_flat: {e}")

        if self.state_machine.state != PositionState.FLAT:
            active_states = {
                PositionState.ACTIVE, PositionState.PROTECTION_PENDING,
                PositionState.TP1_FILLED, PositionState.PROTECTED_RUNNER,
                PositionState.DAMAGE_CONTROL, PositionState.EXITING,
                PositionState.PARTIALLY_FILLED,
            }
            if self.state_machine.state in active_states:
                await self.state_machine.transition_to(PositionState.CLOSED, reason)
            await self.state_machine.transition_to(PositionState.FLAT, reason)

        # Set post-trade inter-trade delay and symbol cooldown
        now = time.time()
        self.last_trade_closed_time = now
        if old_pos:
            self.symbol_cooldowns[old_pos.symbol.upper()] = now + self.symbol_cooldown_sec

        self.state_machine.active_position = None
        self.policy_engine.has_active_position = False
        self._trigger_telemetry()


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
        await self.brain_trace.seed_from_db(self.db)
        self._baseline_equity = await self.db.get_or_set_baseline_equity(self.account.starting_equity or 10000.0)
        self._cached_perf_stats = await self.db.get_performance_stats()

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
                    self.brain_trace.record_step(
                        symbol="SYSTEM",
                        stage="SYSTEM",
                        status="INFO",
                        message=f"Dynamic universe discovered {len(self.active_symbols)} symbols (Candidates: {', '.join(self.active_symbols[:5])})"
                    )
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
            if self._baseline_equity <= 1000.0 and self.account.total_equity > 1000.0:
                self._baseline_equity = await self.db.get_or_set_baseline_equity(self.account.total_equity)
            self._cached_perf_stats = await self.db.get_performance_stats()
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

        # Measure event loop lag
        if self.metrics_collector:
            try:
                await self.metrics_collector.measure_event_loop_lag()
            except Exception:
                pass

        # 0. Global Critical Data Source & Trading Freeze Check (Item 7)
        frozen, freeze_reason = self.is_trading_frozen()
        if frozen:
            if not self._last_frozen_state or self._last_freeze_reason != freeze_reason:
                logger.warning(f"🛑 [TRADING FROZEN] Critical data source offline: {freeze_reason}. Halting entries.")
                self._last_frozen_state = True
                self._last_freeze_reason = freeze_reason
                self.brain_trace.record_step(
                    symbol="SYSTEM",
                    stage="SYSTEM",
                    status="WARN",
                    message=f"Trading frozen: {freeze_reason}"
                )
                self._trigger_telemetry()
        else:
            if self._last_frozen_state:
                logger.info("✅ [TRADING RESUMED] All critical data sources healthy and reconnected. Resuming entries.")
                self._last_frozen_state = False
                self._last_freeze_reason = ""
                self.brain_trace.record_step(
                    symbol="SYSTEM",
                    stage="SYSTEM",
                    status="INFO",
                    message="Trading resumed: all critical data sources healthy"
                )
                self._trigger_telemetry()

        # 1. Update Active Positions & Brackets
        if self.executor.is_paper:
            closed_trades = await self.executor.update_positions(self.ws.latest_prices)
            for trade in closed_trades:
                await self.db.save_trade(trade)
                await self.circuit_breakers.record_trade_result(trade["pnl"], self.account.total_equity)
                self._cached_perf_stats = await self.db.get_performance_stats()
                self.policy_engine.has_active_position = False
                sym = trade.get("symbol", "").upper()
                now_ts = time.time()
                self.last_trade_closed_time = now_ts
                if sym:
                    self.symbol_cooldowns[sym] = now_ts + self.symbol_cooldown_sec
                self.brain_trace.record_step(
                    symbol=trade.get("symbol", "POSITION"),
                    stage="EXECUTION",
                    status="PASS" if trade.get("pnl", 0) >= 0 else "INFO",
                    message=f"Closed {trade.get('side', '')} {trade.get('symbol', '')} via {trade.get('exit_reason', '')} @ ${trade.get('exit_price', 0):.4f} (Net PnL: ${trade.get('pnl', 0):+.2f}, Fees: ${trade.get('fees', 0):.2f})"
                )
                self._trigger_telemetry()
        else:
            # Live Mode Continuous Reconciliation & Position Monitoring
            if self.cycle_count % 5 == 0:
                await self.reconciler.reconcile()
            closed_trades = await self.executor.update_positions(self.ws.latest_prices)
            for trade in closed_trades:
                await self.db.save_trade(trade)
                await self.circuit_breakers.record_trade_result(trade["pnl"], self.account.total_equity)
                self._cached_perf_stats = await self.db.get_performance_stats()
                self.policy_engine.has_active_position = False
                sym = trade.get("symbol", "").upper()
                now_ts = time.time()
                self.last_trade_closed_time = now_ts
                if sym:
                    self.symbol_cooldowns[sym] = now_ts + self.symbol_cooldown_sec
                self.brain_trace.record_step(
                    symbol=trade.get("symbol", "POSITION"),
                    stage="EXECUTION",
                    status="PASS" if trade.get("pnl", 0) >= 0 else "INFO",
                    message=f"Closed {trade.get('side', '')} {trade.get('symbol', '')} via {trade.get('exit_reason', '')} @ ${trade.get('exit_price', 0):.4f} (Net PnL: ${trade.get('pnl', 0):+.2f}, Fees: ${trade.get('fees', 0):.2f})"
                )
                self._trigger_telemetry()

        # Active position monitoring heartbeat
        if self.state_machine.active_position and self.cycle_count % 10 == 0:
            pos = self.state_machine.active_position
            side_s = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
            if self.state_machine.state in (
                PositionState.ACTIVE, PositionState.PROTECTION_PENDING,
                PositionState.TP1_FILLED, PositionState.PROTECTED_RUNNER,
                PositionState.DAMAGE_CONTROL
            ):
                snap = self.market_state.get_snapshot(pos.symbol)
                mark = snap.mark_price if (snap and snap.mark_price > 0) else pos.entry_price
                pnl = (mark - pos.entry_price) * pos.size if side_s == "Buy" else (pos.entry_price - mark) * pos.size
                self.brain_trace.record_step(
                    symbol=pos.symbol,
                    stage="POSITION_MONITOR",
                    status="INFO",
                    message=f"Managing {side_s} {pos.symbol} @ ${mark:.4f} | Floating PnL: ${pnl:+.2f} | SL: ${pos.stop_price:.4f} | TP1: ${pos.tp1_price:.4f}"
                )
            elif self.state_machine.state == PositionState.ENTRY_PENDING:
                self.brain_trace.record_step(
                    symbol=pos.symbol,
                    stage="POSITION_MONITOR",
                    status="INFO",
                    message=f"Awaiting maker limit fill for {side_s} {pos.symbol} @ ${pos.entry_price:.4f} (TTL active)"
                )

        # Synchronize active position flag
        self.policy_engine.has_active_position = (
            self.state_machine.state not in (PositionState.FLAT, PositionState.CLOSED)
        )

        # 2. Opportunity Utilization: Scan market ONLY IF NOT FROZEN and FLAT (Item 7)
        now_ts = time.time()
        time_since_last_close = now_ts - self.last_trade_closed_time
        in_inter_trade_delay = (time_since_last_close < self.inter_trade_delay_sec)

        if (
            not frozen
            and self.state_machine.state == PositionState.FLAT
            and not self.circuit_breakers.is_cooldown_active()
            and not self.reconciler.reconcile_frozen
            and not in_inter_trade_delay
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
                        is_buy = (candidate.side == OrderSide.BUY or getattr(candidate.side, "value", "").lower() == "buy")
                        # When post-only is enabled, ensure order rests as maker without crossing spread
                        if getattr(self.config, "post_only", False):
                            if is_buy and snapshot.best_bid > 0:
                                entry_p = min(entry_p, snapshot.best_bid)
                            elif not is_buy and snapshot.best_ask > 0:
                                entry_p = max(entry_p, snapshot.best_ask)
                            entry_p = self.instrument_repo.round_price(candidate.symbol, entry_p)

                        stop_p = self.instrument_repo.round_price(candidate.symbol, candidate.structural_stop)
                        tp1_p = self.instrument_repo.round_price(candidate.symbol, candidate.tp1)
                        tp2_p = self.instrument_repo.round_price(candidate.symbol, candidate.tp2)
                        rounded_qty = self.instrument_repo.round_qty(candidate.symbol, qty)

                        # Canonical Market Structure Risk-to-Reward Ratios (RR1 >= 1.55, RR2 >= 2.85)
                        stop_dist = abs(entry_p - stop_p)
                        if stop_dist > 0 and rounded_qty > 0:
                            is_buy = (candidate.side == OrderSide.BUY or getattr(candidate.side, "value", "").lower() == "buy")
                            if is_buy:
                                cand_tp1_dist = max(0.0, candidate.tp1 - entry_p)
                                cand_tp2_dist = max(0.0, candidate.tp2 - entry_p)
                                tp1_dist = max(cand_tp1_dist, stop_dist * 1.55)
                                tp2_dist = max(cand_tp2_dist, tp1_dist + (stop_dist * 1.30), stop_dist * 2.85)
                                tp1_p = self.instrument_repo.round_price(candidate.symbol, entry_p + tp1_dist)
                                tp2_p = self.instrument_repo.round_price(candidate.symbol, entry_p + tp2_dist)
                            else:
                                cand_tp1_dist = max(0.0, entry_p - candidate.tp1)
                                cand_tp2_dist = max(0.0, entry_p - candidate.tp2)
                                tp1_dist = max(cand_tp1_dist, stop_dist * 1.55)
                                tp2_dist = max(cand_tp2_dist, tp1_dist + (stop_dist * 1.30), stop_dist * 2.85)
                                tp1_p = self.instrument_repo.round_price(candidate.symbol, entry_p - tp1_dist)
                                tp2_p = self.instrument_repo.round_price(candidate.symbol, entry_p - tp2_dist)

                        chosen_leverage = getattr(decision, "leverage", self.config.default_leverage) or self.config.default_leverage
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
                            leverage=chosen_leverage,
                        )

                        exec_safe, exec_reason = self.exec_policy.evaluate_execution_precheck(
                            exec_plan, snapshot, self.ws.connected
                        )

                        if exec_safe:
                            self.brain_trace.record_step(candidate.symbol, "PRECHECK", "PASS", "Execution pre-check 15/15 invariants satisfied")
                            # Acquire Centralized Atomic Lock & Execute
                            async with self.policy_engine._position_lock:
                                # Hard Invariant Check: Verify desk and exchange have ZERO active positions
                                if len(self.account.cached_positions) > 0 or len(self.executor.live_positions) > 0 or len(self.executor.paper_positions) > 0:
                                    logger.warning(
                                        f"Aborting execution of {candidate.symbol}: desk or exchange already has active position "
                                        f"(cached={len(self.account.cached_positions)}, live={len(self.executor.live_positions)}, paper={len(self.executor.paper_positions)})"
                                    )
                                    break
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
                                        stop_p,
                                        leverage=chosen_leverage,
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
            else:
                if self.cycle_count % 15 == 0:
                    self.brain_trace.record_step(
                        symbol="UNIVERSE",
                        stage="SCANNER",
                        status="INFO",
                        message=f"Evaluated {len(self.active_symbols)} symbols across 4 alpha strategies. Awaiting conviction >= 70.0"
                    )
        elif in_inter_trade_delay and self.state_machine.state == PositionState.FLAT:
            rem = max(0.0, self.inter_trade_delay_sec - time_since_last_close)
            if self.cycle_count % 10 == 0:
                self.brain_trace.record_step(
                    symbol="SETTLING",
                    stage="INTER_TRADE_DELAY",
                    status="INFO",
                    message=f"Post-trade processing delay active ({rem:.1f}s remaining) — settling exchange state"
                )

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

    def _compute_performance_payload(self, active_pos, snapshots: Dict[str, Any]) -> Dict[str, Any]:
        """Compute real-time performance, dollar P&L growth, and ROI metrics."""
        curr_equity = self.account.total_equity

        active_holding_states = {
            PositionState.ACTIVE, PositionState.PROTECTION_PENDING,
            PositionState.TP1_FILLED, PositionState.PROTECTED_RUNNER,
            PositionState.DAMAGE_CONTROL, PositionState.EXITING,
            PositionState.PARTIALLY_FILLED,
        }
        is_holding = (self.state_machine.state in active_holding_states)

        unrealized = self.account.unrealised_pnl if is_holding else 0.0
        if is_holding and active_pos and hasattr(active_pos, 'entry_price') and active_pos.entry_price > 0:
            snap = snapshots.get(active_pos.symbol)
            mark = snap.mark_price if snap else active_pos.entry_price
            if hasattr(active_pos, 'side') and active_pos.side.value == "Buy":
                unrealized = (mark - active_pos.entry_price) * active_pos.size
            else:
                unrealized = (active_pos.entry_price - mark) * active_pos.size

        realized = float(self._cached_perf_stats.get("total_pnl", 0.0))
        total_pnl_usd = round(realized + unrealized, 2)
        daily_pnl = round(float(self._cached_perf_stats.get("daily_pnl", 0.0)) + unrealized, 2)

        # Baseline equity: prioritize persisted baseline ($10,000.0) or starting capital
        baseline = float(self._baseline_equity or self.account.starting_equity or 10000.0)
        if baseline <= 0:
            baseline = 10000.0

        # True capital growth relative to initial starting equity
        total_pnl_usd = round(curr_equity - baseline, 2)
        total_roi_pct = round((total_pnl_usd / baseline) * 100.0, 2) if baseline > 0 else 0.0

        daily_pnl = round(float(self._cached_perf_stats.get("daily_pnl", 0.0)) + unrealized, 2)
        daily_roi_pct = round((daily_pnl / baseline) * 100.0, 2) if baseline > 0 else 0.0

        total_fees = float(self._cached_perf_stats.get("total_fees", 0.0))
        if is_holding and active_pos:
            entry_fee = getattr(active_pos, "entry_fee", 0.0) or getattr(active_pos, "fees_paid", 0.0)
            if entry_fee == 0.0 and hasattr(active_pos, 'entry_price') and active_pos.entry_price > 0:
                entry_fee = (active_pos.entry_price * active_pos.size) * 0.0002
            total_fees += entry_fee

        return {
            "starting_equity": round(baseline, 2),
            "current_equity": round(curr_equity, 2),
            "total_pnl_usd": total_pnl_usd,
            "total_roi_pct": total_roi_pct,
            "daily_pnl_usd": daily_pnl,
            "daily_roi_pct": daily_roi_pct,
            "realized_pnl_usd": round(curr_equity - baseline - unrealized, 2),
            "unrealized_pnl_usd": round(unrealized, 2),
            "win_count": self._cached_perf_stats.get("win_count", 0),
            "loss_count": self._cached_perf_stats.get("loss_count", 0),
            "total_trades": self._cached_perf_stats.get("total_trades", 0),
            "win_rate_pct": self._cached_perf_stats.get("win_rate_pct", 0.0),
            "profit_factor": self._cached_perf_stats.get("profit_factor", 0.0),
            "total_fees": round(total_fees, 2),
            "expectancy": self._cached_perf_stats.get("expectancy", 0.0),
            "max_drawdown_pct": round(self.circuit_breakers.daily_drawdown_pct, 2),
            "strategy_stats": self._cached_perf_stats.get("strategy_stats", {}),
        }

    def _trigger_telemetry(self):
        """Render rich operational telemetry dashboard."""
        snapshots = {}
        for sym in self.active_symbols:
            snap = self.market_state.get_snapshot(sym)
            c15m = self.candle_mgr.get_candles(sym, "15m")
            if c15m and len(c15m) >= 25:
                snap.regime = self.regime_engine.classify(
                    c15m, snap.atr_15m, snap.spread_pct, snap.orderbook_imbalance
                )
            snapshots[sym] = snap
        active_holding_states = {
            PositionState.ACTIVE, PositionState.PROTECTION_PENDING,
            PositionState.TP1_FILLED, PositionState.PROTECTED_RUNNER,
            PositionState.DAMAGE_CONTROL, PositionState.EXITING,
            PositionState.PARTIALLY_FILLED,
        }
        is_holding = (self.state_machine.state in active_holding_states)
        active_pos = self.state_machine.active_position if is_holding else None

        cd_rem = max(0.0, self.circuit_breakers.cooldown_until - time.time()) if self.circuit_breakers.is_cooldown_active() else 0.0

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
        perf_payload = self._compute_performance_payload(active_pos, snapshots)
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
            performance_stats=perf_payload,
        )

    def _broadcast_frame(self):
        """High-frequency non-blocking broadcast to WebSocket bus."""
        if not self.telemetry_bus:
            return

        active_holding_states = {
            PositionState.ACTIVE, PositionState.PROTECTION_PENDING,
            PositionState.TP1_FILLED, PositionState.PROTECTED_RUNNER,
            PositionState.DAMAGE_CONTROL, PositionState.EXITING,
            PositionState.PARTIALLY_FILLED,
        }
        is_holding = (self.state_machine.state in active_holding_states)
        active_pos = self.state_machine.active_position if is_holding else None
        cd_rem = max(0.0, self.circuit_breakers.cooldown_until - time.time()) if self.circuit_breakers.is_cooldown_active() else 0.0
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

        perf_payload = self._compute_performance_payload(active_pos, snapshots)
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
            performance_stats=perf_payload,
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

    async def manual_close_position(self, reason: str = "Manual User Close via Dashboard") -> Optional[Dict[str, Any]]:
        """Manually close the active position from dashboard or API."""
        exit_price = None
        if self.executor.is_paper:
            syms = list(self.executor.paper_positions.keys())
            if syms and self.ws and hasattr(self.ws, "latest_prices"):
                exit_price = self.ws.latest_prices.get(syms[0])
        else:
            syms = list(self.executor.live_positions.keys())
            if syms and self.ws and hasattr(self.ws, "latest_prices"):
                exit_price = self.ws.latest_prices.get(syms[0])

        trade = await self.executor.close_active_position(reason=reason, exit_price=exit_price)
        if trade:
            await self.db.save_trade(trade)
            await self.circuit_breakers.record_trade_result(trade["pnl"], self.account.total_equity)
            self._cached_perf_stats = await self.db.get_performance_stats()
            self.policy_engine.has_active_position = False
            sym = trade.get("symbol", "").upper()
            now_ts = time.time()
            self.last_trade_closed_time = now_ts
            if sym:
                self.symbol_cooldowns[sym] = now_ts + self.symbol_cooldown_sec
            self.brain_trace.record_step(
                symbol=trade.get("symbol", "POSITION"),
                stage="MANUAL_CLOSE",
                status="PASS" if trade.get("pnl", 0) >= 0 else "INFO",
                message=f"Manual Close: {trade.get('side', '')} {trade.get('symbol', '')} via {reason} @ ${trade.get('exit_price', 0):.4f} (Net PnL: ${trade.get('pnl', 0):+.2f})"
            )
            self._trigger_telemetry()
            self._wake_event.set()
        return trade

    async def reset_performance_history(self) -> Dict[str, Any]:
        """Archive all existing trades and reset performance history cleanly to current account equity."""
        await self.db.clear_trade_history()
        self.circuit_breakers.consecutive_losses = 0
        self.circuit_breakers.daily_loss = 0.0
        self.circuit_breakers.high_watermark = self.account.total_equity
        self._baseline_equity = self.account.total_equity
        await self.db.set_state("baseline_equity", self.account.total_equity)
        self._cached_perf_stats = await self.db.get_performance_stats()
        self.brain_trace.record_step(
            symbol="SYSTEM",
            stage="RESET",
            status="INFO",
            message=f"Performance history reset. Baseline re-anchored to ${self.account.total_equity:.2f}"
        )
        self._trigger_telemetry()
        self._broadcast_frame()
        self._wake_event.set()
        return {"baseline": self.account.total_equity}

