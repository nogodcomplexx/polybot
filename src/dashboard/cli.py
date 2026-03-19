"""
Rich CLI dashboard — live overview of bot status.
"""

import logging
from typing import List

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich import box

from src.data.models import Trade, Position, PortfolioSnapshot, TradeSignal

logger = logging.getLogger(__name__)
console = Console()


class Dashboard:
    """Rich terminal dashboard for bot monitoring."""

    def __init__(self):
        self.console = console

    def render(
        self,
        snapshot: PortfolioSnapshot,
        positions: List[Position],
        recent_trades: List[Trade],
        opportunities: List[TradeSignal],
        is_paper: bool = True,
    ):
        """Render the full dashboard."""
        self.console.clear()

        mode = "[yellow]📝 PAPER TRADING[/]" if is_paper else "[red]🔴 LIVE TRADING[/]"
        self.console.print(Panel(
            f"[bold cyan]🤖 POLYBOT[/] — Polymarket Trading Bot  {mode}",
            style="bold blue",
        ))

        # Portfolio summary
        self._render_portfolio(snapshot)

        # Open positions
        if positions:
            self._render_positions(positions)

        # Recent trades
        if recent_trades:
            self._render_trades(recent_trades[:10])

        # Top opportunities
        if opportunities:
            self._render_opportunities(opportunities[:5])

        self.console.print()

    def _render_portfolio(self, snap: PortfolioSnapshot):
        """Render portfolio summary."""
        pnl_color = "green" if snap.daily_pnl >= 0 else "red"
        total_color = "green" if snap.total_pnl >= 0 else "red"

        table = Table(title="💰 Portfolio", box=box.ROUNDED, show_header=False)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", justify="right")

        table.add_row("Total Value", f"${snap.total_value_usd:.2f}")
        table.add_row("Cash", f"${snap.cash_usd:.2f}")
        table.add_row("Positions", f"${snap.positions_value_usd:.2f}")
        table.add_row("Daily P/L", f"[{pnl_color}]${snap.daily_pnl:+.2f}[/]")
        table.add_row("Total P/L", f"[{total_color}]${snap.total_pnl:+.2f}[/]")
        table.add_row("Open Positions", str(snap.num_open_positions))
        table.add_row("Trades Today", str(snap.num_trades_today))

        self.console.print(table)

    def _render_positions(self, positions: List[Position]):
        """Render open positions table."""
        table = Table(title="📊 Open Positions", box=box.ROUNDED)
        table.add_column("Market", max_width=40)
        table.add_column("Side", justify="center")
        table.add_column("Entry", justify="right")
        table.add_column("Current", justify="right")
        table.add_column("Size", justify="right")
        table.add_column("P/L", justify="right")
        table.add_column("Strategy", justify="center")

        for pos in positions:
            pnl = (pos.current_price - pos.entry_price) * pos.size
            pnl_color = "green" if pnl >= 0 else "red"
            table.add_row(
                pos.market_question[:40],
                pos.side,
                f"${pos.entry_price:.3f}",
                f"${pos.current_price:.3f}",
                f"{pos.size:.1f}",
                f"[{pnl_color}]${pnl:+.2f}[/]",
                pos.strategy_name,
            )

        self.console.print(table)

    def _render_trades(self, trades: List[Trade]):
        """Render recent trades table."""
        table = Table(title="📋 Recent Trades", box=box.ROUNDED)
        table.add_column("Market", max_width=35)
        table.add_column("Side", justify="center")
        table.add_column("Price", justify="right")
        table.add_column("Cost", justify="right")
        table.add_column("Status", justify="center")
        table.add_column("Strategy", justify="center")

        for trade in trades:
            side_color = "green" if trade.side == "BUY" else "red"
            table.add_row(
                trade.market_question[:35],
                f"[{side_color}]{trade.side}[/]",
                f"${trade.price:.3f}",
                f"${trade.cost_usd:.2f}",
                trade.status,
                trade.strategy_name,
            )

        self.console.print(table)

    def _render_opportunities(self, signals: List[TradeSignal]):
        """Render top trading opportunities."""
        table = Table(title="🎯 Top Opportunities", box=box.ROUNDED)
        table.add_column("Market", max_width=40)
        table.add_column("Edge", justify="right")
        table.add_column("Conviction", justify="center")
        table.add_column("Strategy", justify="center")

        for sig in signals:
            edge_color = "green" if sig.edge > 0 else "red"
            conv_color = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "white"}.get(
                sig.conviction.value, "white"
            )
            table.add_row(
                sig.market.question[:40] if sig.market else "N/A",
                f"[{edge_color}]{sig.edge*100:+.1f}%[/]",
                f"[{conv_color}]{sig.conviction.value}[/]",
                sig.strategy_name,
            )

        self.console.print(table)
