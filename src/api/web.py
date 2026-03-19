import logging
import os
import time
from typing import Dict, List, Any
from dataclasses import asdict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn
import json
from enum import Enum

logger = logging.getLogger(__name__)
from src.utils.sessions import get_session
from src.engine.performance_tuner import PerformanceTuner, RegimeKey, dominance_bucket


class EnumEncoder(json.JSONEncoder):
    """JSON encoder that handles Enum values."""
    def default(self, obj):
        if isinstance(obj, Enum):
            return obj.value
        return super().default(obj)


class DashboardAPI:
    """FastAPI wrapper for the PolyBot dashboard."""
    def __init__(self, bot):
        self.bot = bot
        self.app = FastAPI(title="PolyBot Web Dashboard")
        
        # Enable CORS for frontend development
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        self.active_connections: List[WebSocket] = []
        self._setup_routes()
        
        # Serve static files from the ui directory
        ui_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "ui")
        if os.path.isdir(ui_path):
            self.app.mount("/", StaticFiles(directory=ui_path, html=True), name="ui")

    def _setup_routes(self):
        @self.app.get("/api/v1/summary")
        async def get_summary():
            try:
                # Get data from the database (the source of truth)
                positions = await self.bot.db.get_positions() if self.bot.db else []
                daily_trades = await self.bot.db.get_daily_trades() if self.bot.db else []
                snapshot = await self.bot.db.get_latest_snapshot() if self.bot.db else None
                
                cash = self.bot.risk.cash if self.bot.risk else 0.0
                positions_value = sum(p.cost_usd for p in positions)
                daily_pnl = sum(t.pnl for t in daily_trades)
                
                return {
                    "status": "active" if self.bot.running else "stopped",
                    "mode": "paper" if self.bot.config.paper_trading else "live",
                    "balance": round(cash, 2),
                    "total_value": round(cash + positions_value, 2),
                    "daily_pnl": round(daily_pnl, 2),
                    "open_positions_count": len(positions),
                    "trades_today": len(daily_trades),
                    "cycle_count": self.bot.cycle_count,
                    "last_update": time.time()
                }
            except Exception as e:
                logger.error(f"Summary endpoint error: {e}", exc_info=True)
                return {
                    "status": "error",
                    "mode": "unknown",
                    "balance": 0, "total_value": 0, "daily_pnl": 0,
                    "open_positions_count": 0, "trades_today": 0,
                    "cycle_count": 0, "last_update": time.time(),
                    "error": str(e)
                }

        @self.app.get("/api/v1/positions")
        async def get_positions():
            try:
                positions = await self.bot.db.get_positions() if self.bot.db else []
                return [asdict(p) for p in positions]
            except Exception as e:
                logger.error(f"Positions endpoint error: {e}", exc_info=True)
                return []

        @self.app.get("/api/v1/opportunities")
        async def get_opportunities():
            try:
                signals = getattr(self.bot, 'last_signals', [])
                result = []
                for s in signals[:10]:
                    try:
                        d = asdict(s)
                        result.append(d)
                    except Exception:
                        result.append({
                            "strategy_name": getattr(s, 'strategy_name', ''),
                            "edge": getattr(s, 'edge', 0),
                            "conviction": str(getattr(s, 'conviction', 'LOW')),
                            "target_price": getattr(s, 'target_price', 0),
                            "market": {"question": getattr(s.market, 'question', '') if hasattr(s, 'market') and s.market else ''}
                        })
                return result
            except Exception as e:
                logger.error(f"Opportunities endpoint error: {e}", exc_info=True)
                return []

        @self.app.get("/api/v1/trades")
        async def get_trades(limit: int = 50):
            try:
                trades = await self.bot.db.get_trades(limit) if self.bot.db else []
                return [asdict(t) for t in trades]
            except Exception as e:
                logger.error(f"Trades endpoint error: {e}", exc_info=True)
                return []

        @self.app.get("/api/v1/regime")
        async def get_regime():
            """
            Current regime view used by the UI:
            - Session (UTC bucket)
            - BTC dominance + 1h change (CoinGecko global)
            """
            try:
                session = get_session().name
                btc_dom = 0.0
                btc_dom_chg = 0.0
                if self.bot.crypto_feed:
                    snap = self.bot.crypto_feed.get_snapshot("BTCUSDT")
                    if snap:
                        btc_dom = float(getattr(snap, "btc_dominance_pct", 0.0) or 0.0)
                        btc_dom_chg = float(getattr(snap, "btc_dominance_change_1h_pct", 0.0) or 0.0)
                return {
                    "session": session,
                    "btc_dominance_pct": btc_dom,
                    "btc_dominance_change_1h_pp": btc_dom_chg,
                    "dominance_bucket": dominance_bucket(btc_dom_chg),
                    "last_update": time.time(),
                }
            except Exception as e:
                logger.error(f"Regime endpoint error: {e}", exc_info=True)
                return {
                    "session": "UNKNOWN",
                    "btc_dominance_pct": 0.0,
                    "btc_dominance_change_1h_pp": 0.0,
                    "dominance_bucket": "FLAT",
                    "last_update": time.time(),
                    "error": str(e),
                }

        @self.app.get("/api/v1/performance")
        async def get_performance(limit: int = 800):
            """
            Returns regime performance stats and current tuner settings for crypto_scalper.
            """
            try:
                if not self.bot.db:
                    return {"rows": [], "updated_at": time.time()}

                rows = await self.bot.db.get_recent_settled_trades(
                    limit=limit, strategy_name="crypto_scalper"
                )

                # Aggregate raw stats by regime bucket.
                agg = {}
                for r in rows:
                    sym = (r.get("underlying_symbol") or "").upper() or "UNKNOWN"
                    sess = (r.get("session") or "").upper() or "UNKNOWN"
                    dom = float(r.get("btc_dominance_change_1h_pct") or 0.0)
                    key = (sym, sess, dominance_bucket(dom))
                    st = agg.get(key) or {"n": 0, "wins": 0, "pnl": 0.0, "cost": 0.0}
                    st["n"] += 1
                    pnl = float(r.get("pnl") or 0.0)
                    cost = float(r.get("cost_usd") or 0.0)
                    st["pnl"] += pnl
                    st["cost"] += abs(cost)
                    if (r.get("status") or "").upper() == "WIN":
                        st["wins"] += 1
                    agg[key] = st

                base_min_edge = 0.01
                if getattr(self.bot, "orchestrator", None) and getattr(self.bot.orchestrator, "crypto_scalper", None):
                    base_min_edge = float(getattr(self.bot.orchestrator.crypto_scalper, "min_edge", 0.01) or 0.01)

                tuner = PerformanceTuner(base_min_edge=base_min_edge, min_samples=20)
                tuning = tuner.build(rows)

                out = []
                for (sym, sess, dom_bucket), st in sorted(
                    agg.items(), key=lambda kv: kv[1]["n"], reverse=True
                ):
                    n = st["n"]
                    win_rate = (st["wins"] / n) if n else 0.0
                    roi = (st["pnl"] / st["cost"]) if st["cost"] else 0.0
                    tkey = RegimeKey(sym, sess, dom_bucket)
                    t = tuning.get(tkey)
                    out.append(
                        {
                            "underlying_symbol": sym,
                            "session": sess,
                            "dominance_bucket": dom_bucket,
                            "n": n,
                            "win_rate": win_rate,
                            "roi": roi,
                            "tuned_min_edge": t.min_edge if t else None,
                            "tuned_kelly_mult": t.kelly_mult if t else None,
                        }
                    )

                return {"rows": out, "updated_at": time.time(), "base_min_edge": base_min_edge}
            except Exception as e:
                logger.error(f"Performance endpoint error: {e}", exc_info=True)
                return {"rows": [], "updated_at": time.time(), "error": str(e)}

        @self.app.get("/api/v1/crypto")
        async def get_crypto():
            """Return current crypto prices and technical indicators."""
            try:
                result = {}
                if self.bot.crypto_feed:
                    for symbol in self.bot.crypto_feed.SYMBOLS:
                        snap = self.bot.crypto_feed.get_snapshot(symbol)
                        if snap:
                            from src.crypto.indicators import compute_indicators
                            from src.crypto.probability import CryptoProbabilityEngine
                            indicators = compute_indicators(snap.candles_5m)
                            
                            engine = CryptoProbabilityEngine()
                            prediction = engine.predict(snap)
                            
                            result[symbol] = {
                                "price": snap.price,
                                "last_refresh": snap.last_refresh,
                                "indicators": {
                                    "rsi": indicators.rsi if indicators else 50,
                                    "ema_trend": indicators.ema_trend if indicators else "neutral",
                                    "macd_histogram": indicators.macd_histogram if indicators else 0,
                                    "bollinger_pct_b": indicators.bollinger_pct_b if indicators else 0.5,
                                    "volume_ratio": indicators.volume_ratio if indicators else 1,
                                    "vwap_deviation": indicators.vwap_deviation if indicators else 0,
                                    "consensus": indicators.consensus_direction if indicators else "neutral",
                                    "strength": indicators.consensus_strength if indicators else 0,
                                    "bullish_count": getattr(indicators, 'bullish_count', 0) if indicators else 0,
                                    "bearish_count": getattr(indicators, 'bearish_count', 0) if indicators else 0,
                                } if indicators else None,
                                "prediction": {
                                    "direction": prediction.direction,
                                    "probability": round(prediction.combined_probability, 3),
                                    "edge": round(prediction.edge, 3),
                                    "reasoning": prediction.reasoning,
                                } if prediction else None,
                            }
                return result
            except Exception as e:
                logger.error(f"Crypto endpoint error: {e}", exc_info=True)
                return {}

        @self.app.get("/api/v1/scan")
        async def get_scan(limit: int = 200):
            """
            Latest 5-min scan decisions from the crypto scalper.
            Shows SKIP/SIGNAL with a plain-English reason per market.
            """
            try:
                orch = getattr(self.bot, "orchestrator", None)
                cs = getattr(orch, "crypto_scalper", None) if orch else None
                rows = getattr(cs, "last_scan", []) if cs else []
                rows = list(rows)[-int(limit):]
                rows.reverse()  # newest first
                return {"rows": rows, "updated_at": time.time()}
            except Exception as e:
                logger.error(f"Scan endpoint error: {e}", exc_info=True)
                return {"rows": [], "updated_at": time.time(), "error": str(e)}

        @self.app.websocket("/api/v1/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            self.active_connections.append(websocket)
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                if websocket in self.active_connections:
                    self.active_connections.remove(websocket)

    async def broadcast(self, data: Dict[str, Any]):
        """Broadcast real-time updates to all connected UI clients."""
        try:
            json_data = json.dumps(data, cls=EnumEncoder)
        except Exception as e:
            logger.error(f"JSON encoding error: {e}")
            return

        for connection in list(self.active_connections):
            try:
                await connection.send_text(json_data)
            except Exception as e:
                logger.error(f"WebSocket broadcast error: {e}")
                if connection in self.active_connections:
                    self.active_connections.remove(connection)

    async def run_server(self, host: str = "0.0.0.0", port: int = 8000):
        """Run the Uvicorn server."""
        # Uvicorn can call sys.exit(1) internally on bind failures (e.g. port in use).
        # We never want a dashboard failure to kill the trading engine.
        try:
            config = uvicorn.Config(self.app, host=host, port=port, log_level="info")
            server = uvicorn.Server(config)
            await server.serve()
        except SystemExit as e:
            logger.error(
                f"Dashboard server failed to start on {host}:{port} (SystemExit={e.code}). "
                "Continuing without web dashboard."
            )
            return
        except OSError as e:
            logger.error(
                f"Dashboard server failed to start on {host}:{port} ({e}). "
                "Continuing without web dashboard."
            )
            return
