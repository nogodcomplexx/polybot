"""
SQLite database layer for trade history, markets, and portfolio tracking.
"""

import aiosqlite
import json
import logging
import time
from pathlib import Path
from typing import List, Optional

from src.data.models import Trade, Position, PortfolioSnapshot

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent.parent / "polybot.db"


class Database:
    """Async SQLite database for the bot."""

    def __init__(self, db_path: str = str(DB_PATH)):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self):
        """Open the database connection and create tables."""
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._create_tables()
        await self._migrate_tables()
        logger.info(f"Database connected: {self.db_path}")

    async def _migrate_tables(self):
        try:
            await self._db.execute("ALTER TABLE trades ADD COLUMN slug TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            await self._db.execute("ALTER TABLE trades ADD COLUMN underlying_symbol TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            await self._db.execute("ALTER TABLE trades ADD COLUMN session TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            await self._db.execute("ALTER TABLE trades ADD COLUMN btc_dominance_pct REAL DEFAULT 0.0")
        except Exception:
            pass
        try:
            await self._db.execute("ALTER TABLE trades ADD COLUMN btc_dominance_change_1h_pct REAL DEFAULT 0.0")
        except Exception:
            pass
        try:
            await self._db.execute("ALTER TABLE trades ADD COLUMN intended_cost_usd REAL DEFAULT 0.0")
        except Exception:
            pass
        try:
            await self._db.execute("ALTER TABLE trades ADD COLUMN replace_count INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await self._db.execute("ALTER TABLE trades ADD COLUMN model_confidence REAL DEFAULT 0.0")
        except Exception:
            pass
        try:
            await self._db.execute("ALTER TABLE trades ADD COLUMN model_edge REAL DEFAULT 0.0")
        except Exception:
            pass
        try:
            await self._db.execute("ALTER TABLE positions ADD COLUMN slug TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            await self._db.execute("ALTER TABLE positions ADD COLUMN underlying_symbol TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            await self._db.execute("ALTER TABLE trades ADD COLUMN direction TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            await self._db.execute("ALTER TABLE positions ADD COLUMN direction TEXT DEFAULT ''")
        except Exception:
            pass
        await self._db.commit()

    async def close(self):
        if self._db:
            await self._db.close()

    async def _create_tables(self):
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY,
                strategy_name TEXT,
                market_question TEXT,
                condition_id TEXT,
                token_id TEXT,
                side TEXT,
                order_type TEXT,
                price REAL,
                size REAL,
                cost_usd REAL,
                status TEXT,
                order_id TEXT,
                filled_at REAL,
                created_at REAL,
                pnl REAL,
                is_paper INTEGER DEFAULT 1,
                slug TEXT DEFAULT '',
                underlying_symbol TEXT DEFAULT '',
                session TEXT DEFAULT '',
                btc_dominance_pct REAL DEFAULT 0.0,
                btc_dominance_change_1h_pct REAL DEFAULT 0.0,
                intended_cost_usd REAL DEFAULT 0.0,
                replace_count INTEGER DEFAULT 0,
                model_confidence REAL DEFAULT 0.0,
                model_edge REAL DEFAULT 0.0,
                direction TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS positions (
                condition_id TEXT,
                token_id TEXT PRIMARY KEY,
                market_question TEXT,
                side TEXT,
                entry_price REAL,
                current_price REAL,
                size REAL,
                cost_usd REAL,
                unrealized_pnl REAL,
                stop_loss_price REAL,
                take_profit_price REAL,
                strategy_name TEXT,
                opened_at REAL,
                slug TEXT DEFAULT '',
                underlying_symbol TEXT DEFAULT '',
                direction TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                timestamp REAL PRIMARY KEY,
                total_value_usd REAL,
                cash_usd REAL,
                positions_value_usd REAL,
                daily_pnl REAL,
                total_pnl REAL,
                num_open_positions INTEGER,
                num_trades_today INTEGER
            );

            CREATE TABLE IF NOT EXISTS news_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                source TEXT,
                url TEXT,
                sentiment_score REAL,
                sentiment_label TEXT,
                matched_market TEXT,
                created_at REAL
            );
        """)
        await self._db.commit()

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    async def save_trade(self, trade: Trade):
        await self._db.execute(
            """INSERT OR REPLACE INTO trades
               (id, strategy_name, market_question, condition_id, token_id,
                side, order_type, price, size, cost_usd, status, order_id,
                filled_at, created_at, pnl, is_paper, slug,
                underlying_symbol, session, btc_dominance_pct, btc_dominance_change_1h_pct,
                intended_cost_usd, replace_count, model_confidence, model_edge, direction)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade.id, trade.strategy_name, trade.market_question,
                trade.condition_id, trade.token_id, trade.side, trade.order_type,
                trade.price, trade.size, trade.cost_usd, trade.status,
                trade.order_id, trade.filed_at if hasattr(trade, 'filed_at') else getattr(trade, 'filled_at', 0.0), trade.created_at, trade.pnl,
                1 if trade.is_paper else 0, trade.slug,
                trade.underlying_symbol,
                trade.session,
                float(trade.btc_dominance_pct or 0.0),
                float(trade.btc_dominance_change_1h_pct or 0.0),
                float(getattr(trade, "intended_cost_usd", 0.0) or 0.0),
                int(getattr(trade, "replace_count", 0) or 0),
                float(getattr(trade, "model_confidence", 0.0) or 0.0),
                float(getattr(trade, "model_edge", 0.0) or 0.0),
                getattr(trade, "direction", "") or "",
            ),
        )
        await self._db.commit()

    async def get_trades(self, limit: int = 50) -> List[Trade]:
        cursor = await self._db.execute(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        return [self._row_to_trade(r) for r in rows]

    async def get_recent_settled_trades(
        self,
        limit: int = 500,
        strategy_name: str = "crypto_scalper",
    ) -> List[dict]:
        """
        Lightweight rows for tuning. Uses only resolved trades.
        Returns dicts with: status, pnl, cost_usd, underlying_symbol, session, btc_dominance_change_1h_pct
        """
        cursor = await self._db.execute(
            """
            SELECT status, pnl, cost_usd, underlying_symbol, session, btc_dominance_change_1h_pct,
                   model_confidence, model_edge, strategy_name
            FROM trades
            WHERE status IN ('WIN', 'LOSS')
              AND strategy_name = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (strategy_name, limit),
        )
        rows = await cursor.fetchall()
        result = []
        for r in rows:
            result.append(
                {
                    "status": r[0],
                    "pnl": r[1],
                    "cost_usd": r[2],
                    "underlying_symbol": r[3],
                    "session": r[4],
                    "btc_dominance_change_1h_pct": r[5],
                    "model_confidence": r[6],
                    "model_edge": r[7],
                    "strategy_name": r[8],
                }
            )
        return result

    async def get_daily_trades(self) -> List[Trade]:
        today_start = time.time() - (time.time() % 86400)
        cursor = await self._db.execute(
            "SELECT * FROM trades WHERE created_at >= ? ORDER BY created_at DESC",
            (today_start,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_trade(r) for r in rows]

    async def get_unsettled_trades(self) -> List[Trade]:
        """Fetch paper trades that are filled (not yet WIN/LOSS) with no PnL applied."""
        cursor = await self._db.execute(
            "SELECT * FROM trades WHERE is_paper = 1 AND status IN ('FILLED','PARTIALLY_FILLED') AND pnl = 0.0"
        )
        rows = await cursor.fetchall()
        return [self._row_to_trade(r) for r in rows]

    async def get_open_paper_orders(self) -> List[Trade]:
        """Fetch paper orders that are OPEN (not filled yet)."""
        cursor = await self._db.execute(
            "SELECT * FROM trades WHERE is_paper = 1 AND status = 'OPEN'"
        )
        rows = await cursor.fetchall()
        return [self._row_to_trade(r) for r in rows]

    def _row_to_trade(self, row) -> Trade:
        return Trade(
            id=row[0], strategy_name=row[1], market_question=row[2],
            condition_id=row[3], token_id=row[4], side=row[5],
            order_type=row[6], price=row[7], size=row[8], cost_usd=row[9],
            status=row[10], order_id=row[11], filed_at=row[12],
            created_at=row[13], pnl=row[14], is_paper=bool(row[15]),
            slug=row[16] if len(row) > 16 else "",
            underlying_symbol=row[17] if len(row) > 17 else "",
            session=row[18] if len(row) > 18 else "",
            btc_dominance_pct=float(row[19]) if len(row) > 19 and row[19] is not None else 0.0,
            btc_dominance_change_1h_pct=float(row[20]) if len(row) > 20 and row[20] is not None else 0.0,
            intended_cost_usd=float(row[21]) if len(row) > 21 and row[21] is not None else 0.0,
            replace_count=int(row[22]) if len(row) > 22 and row[22] is not None else 0,
            model_confidence=float(row[23]) if len(row) > 23 and row[23] is not None else 0.0,
            model_edge=float(row[24]) if len(row) > 24 and row[24] is not None else 0.0,
            direction=row[25] if len(row) > 25 and row[25] is not None else "",
        )

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    async def save_position(self, pos: Position):
        await self._db.execute(
            """INSERT OR REPLACE INTO positions
               (condition_id, token_id, market_question, side, entry_price,
                current_price, size, cost_usd, unrealized_pnl,
                stop_loss_price, take_profit_price, strategy_name, opened_at, slug, underlying_symbol, direction)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pos.condition_id, pos.token_id, pos.market_question, pos.side,
                pos.entry_price, pos.current_price, pos.size, pos.cost_usd,
                pos.unrealized_pnl, pos.stop_loss_price, pos.take_profit_price,
                pos.strategy_name, pos.opened_at, pos.slug, getattr(pos, "underlying_symbol", "") or "",
                getattr(pos, "direction", "") or "",
            ),
        )
        await self._db.commit()

    async def get_positions(self) -> List[Position]:
        cursor = await self._db.execute("SELECT * FROM positions")
        rows = await cursor.fetchall()
        return [
            Position(
                condition_id=r[0], token_id=r[1], market_question=r[2],
                side=r[3], entry_price=r[4], current_price=r[5], size=r[6],
                cost_usd=r[7], unrealized_pnl=r[8], stop_loss_price=r[9],
                take_profit_price=r[10], strategy_name=r[11], opened_at=r[12],
                slug=r[13] if len(r) > 13 else "",
                underlying_symbol=r[14] if len(r) > 14 else "",
                direction=r[15] if len(r) > 15 and r[15] is not None else "",
            )
            for r in rows
        ]

    async def delete_position(self, token_id: str):
        await self._db.execute("DELETE FROM positions WHERE token_id = ?", (token_id,))
        await self._db.commit()

    # ------------------------------------------------------------------
    # Portfolio Snapshots
    # ------------------------------------------------------------------

    async def save_snapshot(self, snap: PortfolioSnapshot):
        await self._db.execute(
            """INSERT INTO portfolio_snapshots
               (timestamp, total_value_usd, cash_usd, positions_value_usd,
                daily_pnl, total_pnl, num_open_positions, num_trades_today)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snap.timestamp, snap.total_value_usd, snap.cash_usd,
                snap.positions_value_usd, snap.daily_pnl, snap.total_pnl,
                snap.num_open_positions, snap.num_trades_today,
            ),
        )
        await self._db.commit()

    async def get_latest_snapshot(self) -> Optional[PortfolioSnapshot]:
        cursor = await self._db.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY timestamp DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return PortfolioSnapshot(
            timestamp=row[0], total_value_usd=row[1], cash_usd=row[2],
            positions_value_usd=row[3], daily_pnl=row[4], total_pnl=row[5],
            num_open_positions=row[6], num_trades_today=row[7],
        )

    async def get_daily_pnl(self) -> float:
        """Calculate today's P/L from trades."""
        trades = await self.get_daily_trades()
        return sum(t.pnl for t in trades)

    async def get_win_rate(self) -> float:
        """Calculate the all-time Win Rate."""
        cursor = await self._db.execute(
            "SELECT status FROM trades WHERE status IN ('WIN', 'LOSS')"
        )
        rows = await cursor.fetchall()
        if not rows:
            return 0.5  # Default 50% if no history
            
        wins = sum(1 for r in rows if r[0] == 'WIN')
        return wins / len(rows)
