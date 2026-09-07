"""
SQLite Database Layer for Bybit V5 Linear Perpetual Scalper.
Persists all trades, orders, fills, signals, policy decisions, state transitions,
rejections, and system state (4-hour cooldown, high watermark, drawdown).
"""

import aiosqlite
import json
import logging
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class BybitDatabase:
    """Production-grade asynchronous SQLite storage engine."""

    def __init__(self, db_path: str = "polybot.db"):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self):
        """Connect to SQLite with WAL mode and busy timeout."""
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._create_tables()
        logger.info(f"Bybit Database connected: {self.db_path}")

    async def close(self):
        if self._db:
            await self._db.close()
            self._db = None

    async def _create_tables(self):
        """Create all required schema tables (Section 46/63)."""
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS bybit_trades (
                id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                strategy TEXT NOT NULL,
                regime TEXT NOT NULL,
                entry REAL NOT NULL,
                exit_price REAL DEFAULT 0.0,
                stop_price REAL NOT NULL,
                tp1 REAL NOT NULL,
                tp2 REAL NOT NULL,
                quantity REAL NOT NULL,
                leverage INTEGER DEFAULT 10,
                fees REAL DEFAULT 0.0,
                slippage REAL DEFAULT 0.0,
                pnl REAL DEFAULT 0.0,
                pnl_pct REAL DEFAULT 0.0,
                mae REAL DEFAULT 0.0,
                mfe REAL DEFAULT 0.0,
                hold_time REAL DEFAULT 0.0,
                exit_reason TEXT DEFAULT '',
                attribution TEXT DEFAULT 'NORMAL_WIN',
                alpha_score REAL DEFAULT 0.0,
                execution_score REAL DEFAULT 0.0,
                expected_ev REAL DEFAULT 0.0,
                btc_state TEXT DEFAULT '',
                obi REAL DEFAULT 1.0,
                cvd REAL DEFAULT 0.0,
                oi REAL DEFAULT 0.0,
                funding REAL DEFAULT 0.0,
                policy_version TEXT DEFAULT 'v1.0',
                strategy_version TEXT DEFAULT 'v1.0',
                execution_version TEXT DEFAULT 'v1.0',
                status TEXT DEFAULT 'CLOSED'
            );

            CREATE TABLE IF NOT EXISTS bybit_orders (
                client_order_id TEXT PRIMARY KEY,
                order_id TEXT DEFAULT '',
                timestamp REAL NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                price REAL,
                quantity REAL NOT NULL,
                filled_quantity REAL DEFAULT 0.0,
                time_in_force TEXT DEFAULT 'PostOnly',
                reduce_only INTEGER DEFAULT 0,
                status TEXT DEFAULT 'NEW'
            );

            CREATE TABLE IF NOT EXISTS bybit_signals (
                id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                strategy TEXT NOT NULL,
                regime TEXT NOT NULL,
                entry REAL NOT NULL,
                structural_stop REAL NOT NULL,
                tp1 REAL NOT NULL,
                tp2 REAL NOT NULL,
                alpha_score REAL NOT NULL,
                expected_ev REAL NOT NULL,
                confidence REAL NOT NULL,
                thesis_expiry REAL NOT NULL,
                features TEXT DEFAULT '{}',
                reasoning TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS bybit_policy_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                symbol TEXT NOT NULL,
                approved INTEGER NOT NULL,
                risk_pct REAL DEFAULT 0.0,
                risk_usd REAL DEFAULT 0.0,
                rejection_reason TEXT DEFAULT '',
                checks TEXT DEFAULT '{}',
                policy_version TEXT DEFAULT 'v1.0'
            );

            CREATE TABLE IF NOT EXISTS bybit_rejections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                symbol TEXT NOT NULL,
                strategy TEXT DEFAULT '',
                score REAL DEFAULT 0.0,
                ev REAL DEFAULT 0.0,
                reason TEXT NOT NULL,
                checks TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS bybit_state_transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                symbol TEXT NOT NULL,
                from_state TEXT NOT NULL,
                to_state TEXT NOT NULL,
                reason TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS bybit_system_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                severity TEXT NOT NULL,
                component TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bybit_system_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
        """)
        await self._db.commit()

    # ------------------------------------------------------------------
    # System State Persistence (Cooldown, High Watermark, Drawdown)
    # ------------------------------------------------------------------

    async def get_state(self, key: str, default: Any = None) -> Any:
        cursor = await self._db.execute("SELECT value FROM bybit_system_state WHERE key = ?", (key,))
        row = await cursor.fetchone()
        if row:
            try:
                return json.loads(row[0])
            except Exception:
                return row[0]
        return default

    async def set_state(self, key: str, value: Any):
        val_str = json.dumps(value) if not isinstance(value, str) else value
        await self._db.execute(
            "INSERT OR REPLACE INTO bybit_system_state (key, value, updated_at) VALUES (?, ?, ?)",
            (key, val_str, time.time()),
        )
        await self._db.commit()

    async def load_governor_state(self) -> Dict[str, Any]:
        """Load persisted governor metrics across restarts."""
        consecutive_losses = await self.get_state("consecutive_losses", 0)
        cooldown_until = await self.get_state("cooldown_until", 0.0)
        high_watermark = await self.get_state("high_watermark", 1000.0)
        daily_loss = await self.get_state("daily_loss", 0.0)
        kill_switch = await self.get_state("kill_switch_active", False)
        return {
            "consecutive_losses": int(consecutive_losses),
            "cooldown_until": float(cooldown_until),
            "high_watermark": float(high_watermark),
            "daily_loss": float(daily_loss),
            "kill_switch_active": bool(kill_switch),
        }

    async def save_governor_state(
        self, consecutive_losses: int, cooldown_until: float, high_watermark: float, daily_loss: float, kill_switch: bool
    ):
        """Save governor metrics atomically in a single transaction."""
        now = time.time()
        await self._db.executemany(
            "INSERT OR REPLACE INTO bybit_system_state (key, value, updated_at) VALUES (?, ?, ?)",
            [
                ("consecutive_losses", json.dumps(consecutive_losses), now),
                ("cooldown_until", json.dumps(cooldown_until), now),
                ("high_watermark", json.dumps(high_watermark), now),
                ("daily_loss", json.dumps(daily_loss), now),
                ("kill_switch_active", json.dumps(kill_switch), now),
            ]
        )
        await self._db.commit()

    # ------------------------------------------------------------------
    # Trade & Execution Logging
    # ------------------------------------------------------------------

    async def save_trade(self, trade: Dict[str, Any]):
        await self._db.execute(
            """INSERT OR REPLACE INTO bybit_trades (
                id, timestamp, symbol, side, strategy, regime, entry, exit_price,
                stop_price, tp1, tp2, quantity, leverage, fees, slippage, pnl, pnl_pct,
                mae, mfe, hold_time, exit_reason, attribution, alpha_score, execution_score,
                expected_ev, btc_state, obi, cvd, oi, funding, policy_version,
                strategy_version, execution_version, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade.get("id"), trade.get("timestamp", time.time()), trade.get("symbol"),
                trade.get("side"), trade.get("strategy"), trade.get("regime"),
                trade.get("entry", 0.0), trade.get("exit_price", 0.0), trade.get("stop_price", 0.0),
                trade.get("tp1", 0.0), trade.get("tp2", 0.0), trade.get("quantity", 0.0),
                trade.get("leverage", 10), trade.get("fees", 0.0), trade.get("slippage", 0.0),
                trade.get("pnl", 0.0), trade.get("pnl_pct", 0.0), trade.get("mae", 0.0),
                trade.get("mfe", 0.0), trade.get("hold_time", 0.0), trade.get("exit_reason", ""),
                trade.get("attribution", "NORMAL_WIN"), trade.get("alpha_score", 0.0),
                trade.get("execution_score", 0.0), trade.get("expected_ev", 0.0),
                trade.get("btc_state", ""), trade.get("obi", 1.0), trade.get("cvd", 0.0),
                trade.get("oi", 0.0), trade.get("funding", 0.0), trade.get("policy_version", "v1.0"),
                trade.get("strategy_version", "v1.0"), trade.get("execution_version", "v1.0"),
                trade.get("status", "CLOSED")
            )
        )
        await self._db.commit()

    async def save_rejection(self, symbol: str, reason: str, strategy: str = "", score: float = 0.0, ev: float = 0.0, checks: Dict[str, bool] = None):
        await self._db.execute(
            "INSERT INTO bybit_rejections (timestamp, symbol, strategy, score, ev, reason, checks) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.time(), symbol, strategy, score, ev, reason, json.dumps(checks or {}))
        )
        await self._db.commit()

    async def save_state_transition(self, symbol: str, from_state: str, to_state: str, reason: str = ""):
        await self._db.execute(
            "INSERT INTO bybit_state_transitions (timestamp, symbol, from_state, to_state, reason) VALUES (?, ?, ?, ?, ?)",
            (time.time(), symbol, from_state, to_state, reason)
        )
        await self._db.commit()

    async def save_system_event(self, severity: str, component: str, event_type: str, message: str):
        await self._db.execute(
            "INSERT INTO bybit_system_events (timestamp, severity, component, event_type, message) VALUES (?, ?, ?, ?, ?)",
            (time.time(), severity, component, event_type, message)
        )
        await self._db.commit()

    async def get_recent_closed_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        cursor = await self._db.execute(
            "SELECT id, timestamp, symbol, side, strategy, regime, entry, exit_price, pnl, pnl_pct, exit_reason, hold_time FROM bybit_trades ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0], "timestamp": r[1], "symbol": r[2], "side": r[3],
                "strategy": r[4], "regime": r[5], "entry": r[6], "exit_price": r[7],
                "pnl": r[8], "pnl_pct": r[9], "exit_reason": r[10], "hold_time": r[11]
            }
            for r in rows
        ]

    async def get_daily_pnl(self) -> float:
        today_start = time.time() - (time.time() % 86400)
        cursor = await self._db.execute(
            "SELECT SUM(pnl) FROM bybit_trades WHERE timestamp >= ?", (today_start,)
        )
        row = await cursor.fetchone()
        return float(row[0] or 0.0)
