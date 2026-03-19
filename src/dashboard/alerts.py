"""
Discord webhook alerts for trade notifications.
"""

import logging
import time
from typing import Optional

import httpx

from src.data.models import Trade, Position, PortfolioSnapshot

logger = logging.getLogger(__name__)


class DiscordAlerter:
    """Send alerts to Discord via webhook."""

    def __init__(self, webhook_url: str, enabled: bool = True):
        self.webhook_url = webhook_url
        self.enabled = enabled and bool(webhook_url)
        self._client = httpx.AsyncClient(timeout=10.0)

        if self.enabled:
            logger.info("Discord alerts enabled")
        else:
            logger.info("Discord alerts disabled (no webhook URL)")

    async def close(self):
        await self._client.aclose()

    async def send_trade(self, trade: Trade):
        """Send a trade notification."""
        if not self.enabled:
            return

        mode = "📝 PAPER" if trade.is_paper else "🔴 LIVE"
        emoji = "📈" if trade.side == "BUY" else "📉"

        embed = {
            "title": f"{mode} {emoji} {trade.side} — {trade.strategy_name}",
            "description": trade.market_question[:200] if trade.market_question else "N/A",
            "color": 0x00FF00 if trade.side == "BUY" else 0xFF0000,
            "fields": [
                {"name": "Price", "value": f"${trade.price:.4f}", "inline": True},
                {"name": "Shares", "value": f"{trade.size:.2f}", "inline": True},
                {"name": "Cost", "value": f"${trade.cost_usd:.2f}", "inline": True},
                {"name": "Strategy", "value": trade.strategy_name, "inline": True},
                {"name": "Status", "value": trade.status, "inline": True},
            ],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        await self._send_embed(embed)

    async def send_risk_warning(self, message: str):
        """Send a risk warning."""
        if not self.enabled:
            return

        embed = {
            "title": "⚠️ Risk Warning",
            "description": message,
            "color": 0xFFA500,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        await self._send_embed(embed)

    async def send_kill_switch(self, reason: str):
        """Send kill switch alert."""
        if not self.enabled:
            return

        embed = {
            "title": "🚨 KILL SWITCH ACTIVATED",
            "description": reason,
            "color": 0xFF0000,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        await self._send_embed(embed)

    async def send_daily_summary(self, snapshot: PortfolioSnapshot):
        """Send daily P/L summary."""
        if not self.enabled:
            return

        pnl_emoji = "🟢" if snapshot.daily_pnl >= 0 else "🔴"

        embed = {
            "title": f"📊 Daily Summary {pnl_emoji}",
            "color": 0x00FF00 if snapshot.daily_pnl >= 0 else 0xFF0000,
            "fields": [
                {"name": "Total Value", "value": f"${snapshot.total_value_usd:.2f}", "inline": True},
                {"name": "Cash", "value": f"${snapshot.cash_usd:.2f}", "inline": True},
                {"name": "Positions", "value": f"${snapshot.positions_value_usd:.2f}", "inline": True},
                {"name": "Daily P/L", "value": f"${snapshot.daily_pnl:+.2f}", "inline": True},
                {"name": "Total P/L", "value": f"${snapshot.total_pnl:+.2f}", "inline": True},
                {"name": "Open Positions", "value": str(snapshot.num_open_positions), "inline": True},
                {"name": "Trades Today", "value": str(snapshot.num_trades_today), "inline": True},
            ],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        await self._send_embed(embed)

    async def send_news_signal(self, market_question: str, sentiment: str, score: float, sources: int):
        """Send a significant news event alert."""
        if not self.enabled:
            return

        embed = {
            "title": "📰 News Signal",
            "description": market_question[:200],
            "color": 0x3498DB,
            "fields": [
                {"name": "Sentiment", "value": sentiment, "inline": True},
                {"name": "Score", "value": f"{score:+.2f}", "inline": True},
                {"name": "Sources", "value": str(sources), "inline": True},
            ],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        await self._send_embed(embed)

    async def _send_embed(self, embed: dict):
        """Send a Discord embed via webhook."""
        try:
            payload = {
                "username": "PolyBot 🤖",
                "embeds": [embed],
            }
            resp = await self._client.post(self.webhook_url, json=payload)
            if resp.status_code != 204:
                logger.warning(f"Discord webhook returned {resp.status_code}")
        except Exception as e:
            logger.error(f"Discord alert failed: {e}")
