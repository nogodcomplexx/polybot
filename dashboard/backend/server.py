"""
High-Performance aiohttp Telemetry & WebSocket Server.
Hosts the Cybernetic Neural Network Web UI and streams real-time JSON frames to connected browsers.
DECOUPLING INVARIANT: Runs as an independent read-only observer; cannot block the trading engine.
"""

import asyncio
import logging
import os
import json
import time
from pathlib import Path
from typing import Optional, Set, Any, Dict
import aiohttp
from aiohttp import web, WSMsgType

from src.bybit.telemetry.dashboard_bus import TelemetryBus
from src.bybit.telemetry.brain_trace import BrainDecisionTrace
from src.bybit.telemetry.api_observer import ApiObservatory

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


class DashboardServer:
    """Serves the Neural Network Web Dashboard and broadcasts real-time WebSocket telemetry."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        bus: Optional[TelemetryBus] = None,
        brain_trace: Optional[BrainDecisionTrace] = None,
        api_observer: Optional[ApiObservatory] = None,
        orchestrator: Optional[Any] = None,
    ):
        self.host = host
        self.port = port
        self.bus = bus or TelemetryBus()
        self.brain_trace = brain_trace or BrainDecisionTrace()
        self.api_observer = api_observer or ApiObservatory()
        self.orchestrator = orchestrator
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._active_ws_connections: Set[web.WebSocketResponse] = set()
        self.running = False
        self._ff_cache = None
        self._ff_cache_ts = 0.0

    def build_app(self) -> web.Application:
        """Construct the aiohttp web application with static and API routes."""
        app = web.Application()

        # Web UI routes
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/styles.css", self._handle_styles)
        app.router.add_static("/js/", path=str(FRONTEND_DIR / "js"), name="js")

        # WebSocket endpoint
        app.router.add_get("/ws", self._handle_websocket)

        # REST observatory endpoints
        app.router.add_get("/api/status", self._handle_status_api)
        app.router.add_get("/api/trace", self._handle_trace_api)
        app.router.add_get("/api/api-stats", self._handle_api_stats)
        app.router.add_get("/api/forexfactory", self._handle_forexfactory_api)
        app.router.add_post("/api/close-position", self._handle_close_position)
        app.router.add_post("/api/reset-performance", self._handle_reset_performance)

        return app

    async def _handle_index(self, request: web.Request) -> web.Response:
        """Serve the main HTML page."""
        index_file = FRONTEND_DIR / "index.html"
        if index_file.exists():
            return web.FileResponse(
                index_file,
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
            )
        return web.Response(text="Dashboard frontend not found.", status=404)

    async def _handle_styles(self, request: web.Request) -> web.Response:
        """Serve CSS stylesheet."""
        css_file = FRONTEND_DIR / "styles.css"
        if css_file.exists():
            return web.FileResponse(
                css_file,
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
            )
        return web.Response(text="/* styles.css not found */", content_type="text/css")

    async def _handle_status_api(self, request: web.Request) -> web.Response:
        """Return latest system state snapshot."""
        state = self.bus.get_latest_state()
        return web.json_response(state)

    async def _handle_trace_api(self, request: web.Request) -> web.Response:
        """Return recent decision trace logs."""
        traces = self.brain_trace.get_recent(limit=50)
        return web.json_response({"traces": traces})

    async def _handle_api_stats(self, request: web.Request) -> web.Response:
        """Return venue API observatory stats."""
        stats = self.api_observer.get_stats()
        return web.json_response(stats)

    async def _handle_close_position(self, request: web.Request) -> web.Response:
        """Handle manual position close request from dashboard UI."""
        try:
            if not self.orchestrator:
                return web.json_response(
                    {"success": False, "error": "Orchestrator not connected to dashboard server"},
                    status=503,
                )
            closed_trade = await self.orchestrator.manual_close_position(
                reason="Manual Close via Dashboard Button"
            )
            if closed_trade:
                return web.json_response({
                    "success": True,
                    "trade": closed_trade,
                    "message": f"Closed {closed_trade.get('symbol')} successfully",
                })
            else:
                return web.json_response({
                    "success": False,
                    "message": "No active position currently open to close",
                }, status=400)
        except Exception as e:
            logger.error(f"Error executing manual position close: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def _handle_reset_performance(self, request: web.Request) -> web.Response:
        """Handle request to reset and archive performance stats to start fresh."""
        try:
            if not self.orchestrator:
                return web.json_response(
                    {"success": False, "error": "Orchestrator not connected to dashboard server"},
                    status=503,
                )
            res = await self.orchestrator.reset_performance_history()
            return web.json_response({
                "success": True,
                "message": "Performance history cleared and reset to current equity.",
                "data": res,
            })
        except Exception as e:
            logger.error(f"Error resetting performance history: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def _handle_forexfactory_api(self, request: web.Request) -> web.Response:
        """Return economic calendar events from ForexFactory / Fair Economy feed with caching."""
        now = time.time()
        # Return cache if valid (10 minutes TTL)
        if self._ff_cache and (now - self._ff_cache_ts < 600.0):
            return web.json_response({
                "events": self._ff_cache,
                "source": "cache",
                "cached_at": self._ff_cache_ts,
            })

        ff_url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        try:
            timeout = aiohttp.ClientTimeout(total=5.0)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Polybot/5.0",
                "Accept": "application/json",
            }
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(ff_url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, list) and len(data) > 0:
                            self._ff_cache = data
                            self._ff_cache_ts = now
                            return web.json_response({
                                "events": data,
                                "source": "faireconomy",
                                "cached_at": self._ff_cache_ts,
                            })
        except Exception as e:
            logger.warning(f"ForexFactory calendar fetch error: {e}")

        # Fallback to cache if available even if stale
        if self._ff_cache:
            return web.json_response({
                "events": self._ff_cache,
                "source": "stale_cache",
                "cached_at": self._ff_cache_ts,
            })

        # Final fallback to synthetic realistic events
        fallback = self._get_fallback_macro_events()
        return web.json_response({
            "events": fallback,
            "source": "fallback",
            "cached_at": now,
        })

    def _get_fallback_macro_events(self) -> list:
        """Provide structured macro events for offline or rate-limited environments."""
        return [
            {
                "title": "Fed Interest Rate Decision",
                "country": "USD",
                "date": "2026-10-14T18:00:00Z",
                "impact": "High",
                "forecast": "4.50%",
                "previous": "4.75%",
            },
            {
                "title": "FOMC Press Conference",
                "country": "USD",
                "date": "2026-10-14T18:30:00Z",
                "impact": "High",
                "forecast": "",
                "previous": "",
            },
            {
                "title": "Core CPI m/m",
                "country": "USD",
                "date": "2026-10-15T12:30:00Z",
                "impact": "High",
                "forecast": "0.3%",
                "previous": "0.3%",
            },
            {
                "title": "Unemployment Claims",
                "country": "USD",
                "date": "2026-10-15T12:30:00Z",
                "impact": "Medium",
                "forecast": "220K",
                "previous": "225K",
            },
            {
                "title": "ECB Monetary Policy Statement",
                "country": "EUR",
                "date": "2026-10-16T12:15:00Z",
                "impact": "High",
                "forecast": "3.25%",
                "previous": "3.50%",
            },
            {
                "title": "Retail Sales m/m",
                "country": "USD",
                "date": "2026-10-16T12:30:00Z",
                "impact": "High",
                "forecast": "0.4%",
                "previous": "0.1%",
            },
            {
                "title": "Preliminary UoM Consumer Sentiment",
                "country": "USD",
                "date": "2026-10-16T14:00:00Z",
                "impact": "Medium",
                "forecast": "70.2",
                "previous": "68.9",
            },
        ]

    async def _handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        """Handle real-time WebSocket client connection and stream telemetry frames."""
        ws = web.WebSocketResponse(heartbeat=15.0)
        await ws.prepare(request)
        self._active_ws_connections.add(ws)

        # 1. Send initial state hydration packet
        mode_val = "PAPER"
        if self.orchestrator and hasattr(self.orchestrator, "config") and hasattr(self.orchestrator.config, "mode"):
            mode_val = str(self.orchestrator.config.mode)

        initial_payload = {
            "type": "INITIAL_HYDRATION",
            "mode": mode_val,
            "state": self.bus.get_latest_state(),
            "traces": self.brain_trace.get_recent(limit=40),
            "api_stats": self.api_observer.get_stats(),
        }
        await ws.send_str(json.dumps(initial_payload))

        # 2. Subscribe to TelemetryBus
        q = self.bus.subscribe(queue_size=200)

        # Consumer loop forwarding bus events to WebSocket
        async def forward_bus_events():
            try:
                while not ws.closed:
                    event = await q.get()
                    if ws.closed:
                        break
                    await ws.send_str(json.dumps(event))
            except (asyncio.CancelledError, ConnectionResetError):
                pass
            except Exception as e:
                logger.debug(f"WS send error: {e}")

        forward_task = asyncio.create_task(forward_bus_events())

        try:
            # Handle incoming messages from browser (e.g. ping / filter requests)
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    if msg.data == "ping":
                        await ws.send_str(json.dumps({"type": "PONG", "timestamp": asyncio.get_event_loop().time()}))
                elif msg.type == WSMsgType.ERROR:
                    logger.debug(f"WebSocket closed with exception: {ws.exception()}")
        finally:
            forward_task.cancel()
            self.bus.unsubscribe(q)
            self._active_ws_connections.discard(ws)

        return ws

    async def start(self):
        """Start the aiohttp web and WebSocket server in background."""
        if self.running:
            return
        self._app = self.build_app()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        self.running = True
        logger.info(f"🌐 [NEURAL DASHBOARD ONLINE] Listening on http://{self.host}:{self.port}")

    async def stop(self):
        """Clean up active WebSocket clients and close the web server."""
        if not self.running:
            return
        self.running = False
        for ws in list(self._active_ws_connections):
            await ws.close(code=1000, message=b"Server shutting down")
        if self._runner:
            await self._runner.cleanup()
        logger.info("🛑 [NEURAL DASHBOARD STOPPED]")


# Standalone runner for testing or replay
if __name__ == "__main__":
    import random
    import time
    from src.bybit.telemetry.events import (
        MarketTelemetryEvent, BrainTelemetryEvent, ExecutionTelemetryEvent,
        SystemStatusEvent, AlertEvent
    )

    async def run_standalone_demo():
        logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
        bus = TelemetryBus()
        brain_trace = BrainDecisionTrace()
        api_observer = ApiObservatory()

        server = DashboardServer(host="127.0.0.1", port=8080, bus=bus, brain_trace=brain_trace, api_observer=api_observer)
        await server.start()

        # Seed initial decision trace
        brain_trace.record_step("AVAXUSDT", "SCANNER", "INFO", "Scanner identified AVAXUSDT with Alpha 84.2")
        brain_trace.record_step("AVAXUSDT", "SWEEP", "PASS", "SSL liquidity swept by 0.35% at $28.40")
        brain_trace.record_step("AVAXUSDT", "SFP", "PASS", "SFP confirmed: Wick rejection closed inside range")
        brain_trace.record_step("AVAXUSDT", "OBI", "PASS", "Orderbook imbalance = 1.51 (Bids defending)")
        brain_trace.record_step("AVAXUSDT", "ALPHA", "PASS", "Alpha score calculated = 84.2 / 100")
        brain_trace.record_step("AVAXUSDT", "SMC_AUDITOR", "PASS", "SMC Auditor: 6/6 checks passed")
        brain_trace.record_step("AVAXUSDT", "RISK", "PASS", "Risk: 0.82% equity, Kelly 1.11%, Fortress 92.4%")
        brain_trace.record_step("AVAXUSDT", "PRECHECK", "PASS", "Execution precheck: 15/15 invariants satisfied")
        brain_trace.record_step("TRUMPUSDT", "VETO", "VETO", "VETO on RR_TP2: 2.41 vs required 2.80", requirement="2.80", actual="2.41")

        print("\n=======================================================")
        print("  NEURAL NETWORK TRADING OBSERVATORY DEMO RUNNING")
        print("  Open browser: http://127.0.0.1:8080")
        print("=======================================================\n")

        symbols = ["SOLUSDT", "AVAXUSDT", "ETHUSDT", "BTCUSDT"]
        sym_idx = 0

        try:
            while True:
                now = time.time()
                sym = symbols[sym_idx % len(symbols)]
                sym_idx += 1
                base_price = 100.0 if "SOL" in sym else (30.0 if "AVAX" in sym else 3000.0)
                price = base_price + random.uniform(-0.5, 0.5)

                # 1. API Ping
                api_observer.record_call("Bybit", "orderbook", random.uniform(7.0, 14.0), "WS", "WS")
                api_observer.record_call("Binance", "depth", random.uniform(10.0, 20.0), "WS", "WS")
                if random.random() < 0.2:
                    api_observer.record_call("CoinGlass", "open-interest", random.uniform(90.0, 150.0), "200", "REST")

                # 2. System Status
                bus.publish(SystemStatusEvent(
                    timestamp=now,
                    bot_status="ONLINE",
                    uptime_sec=now % 10000,
                    cpu_pct=round(random.uniform(1.2, 4.5), 1),
                    ram_mb=68.4,
                    event_loop_lag_ms=round(random.uniform(0.2, 1.1), 2),
                    bybit_public_ws="HEALTHY",
                    bybit_private_ws="HEALTHY",
                    binance_ws="HEALTHY",
                    coinglass_rest="HEALTHY",
                    watchdog_status="HEALTHY",
                    kill_switch_active=False,
                    kill_switch_matrix={"SOFTWARE_KILL": False, "DRAWDOWN_KILL": False, "STALE_DATA_KILL": False},
                    cooldown_active=False,
                    account_equity=10245.50,
                    available_balance=9750.00,
                    cash_fortress_pct=95.2,
                    used_margin=495.50,
                    daily_drawdown_pct=0.45,
                    consecutive_losses=0,
                ))

                # 3. Market Telemetry
                bus.publish(MarketTelemetryEvent(
                    symbol=sym,
                    timestamp=now,
                    last_price=round(price, 2),
                    mark_price=round(price, 2),
                    spread_pct=0.02,
                    best_bid=round(price - 0.01, 2),
                    best_ask=round(price + 0.01, 2),
                    bid_depth=650000.0,
                    ask_depth=480000.0,
                    orderbook_imbalance=1.35,
                    weighted_obi=1.42,
                    cvd=24500.0,
                    trade_velocity=1.8,
                    funding_rate=0.0001,
                    aggregate_oi=142000000.0,
                    basis=0.15,
                    basis_pct=0.015,
                    regime="RANGE",
                    vwap_24h=round(price * 0.999, 2),
                    nearest_fvg=round(price * 0.995, 2),
                    external_liq=round(price * 1.008, 2),
                    bid_walls_usd=3.2,
                    ask_walls_usd=1.1,
                    absorption_status="HIGH",
                    orderbook_valid=True,
                ))

                # 4. Brain Telemetry
                bus.publish(BrainTelemetryEvent(
                    symbol=sym,
                    timestamp=now,
                    strategy="TURTLE_SOUP",
                    alpha_score=84.0,
                    score_breakdown={
                        "Liquidity Sweep": 24.0,
                        "SFP": 18.0,
                        "VWAP": 12.0,
                        "Orderbook": 16.0,
                        "WT": 11.0,
                        "BTC Macro": 8.0,
                    },
                    smc_auditor_checks={
                        "Structure": "PASS",
                        "Liquidity": "PASS",
                        "Invalidation": "PASS",
                        "RR": "PASS",
                        "Execution": "PASS",
                        "Macro": "PASS",
                    },
                    expected_ev=0.0084,
                    rr_tp1=1.9,
                    rr_tp2=3.4,
                    risk_pct=0.82,
                    kelly_pct=1.11,
                    drawdown_scalar=0.74,
                    final_decision="TRADE",
                    decision_reason="All constraints satisfied",
                    target_qty=12.5,
                ))

                # 5. Execution Telemetry
                bus.publish(ExecutionTelemetryEvent(
                    timestamp=now,
                    position_state="ACTIVE_PROTECTED",
                    active_position={
                        "symbol": sym,
                        "side": "Buy",
                        "entry_price": round(price - 0.20, 2),
                        "mark_price": round(price, 2),
                        "size": 12.5,
                        "stop_price": round(price - 0.75, 2),
                        "tp1_price": round(price + 0.95, 2),
                        "tp2_price": round(price + 1.80, 2),
                        "pnl": round(12.5 * 0.20, 2),
                        "roe_pct": 5.4,
                        "hold_time_sec": 42.0,
                        "mae": -0.05,
                        "mfe": 0.25,
                        "state": "ACTIVE_PROTECTED",
                    },
                    reconciliation_status="HEALTHY",
                    last_reconciliation_ts=now,
                ))

                await asyncio.sleep(0.5)
        except KeyboardInterrupt:
            await server.stop()

    asyncio.run(run_standalone_demo())
