"""
Institutional Telemetry Dashboard & Candidate Scan Reporter.
Displays rich operational telemetry every 5 minutes and on state transitions (Sections 33, 47, 89).
"""

import time
from typing import Dict, Any, List, Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from src.bybit.models import ActivePosition, MarketSnapshot, Signal, PositionState

console = Console(legacy_windows=False)


class TelemetryDashboard:
    """Renders institutional 5-minute telemetry tables and candidate opportunity rankings."""

    def __init__(self):
        self.last_telemetry_time: float = 0.0
        self.telemetry_interval_sec: float = 300.0  # 5 minutes

    def should_render(self) -> bool:
        return (time.time() - self.last_telemetry_time) >= self.telemetry_interval_sec

    def render(
        self,
        account_equity: float,
        available_equity: float,
        fortress_pct: float,
        daily_dd_pct: float,
        consecutive_losses: int,
        cooldown_active: bool,
        cooldown_remaining_sec: float,
        position_state: PositionState,
        active_position: Optional[ActivePosition],
        latest_snapshots: Dict[str, MarketSnapshot],
        top_candidates: List[Signal],
        rejected_candidates: List[Dict[str, Any]],
        cross_exchange_states: Optional[Dict[str, Any]] = None,
        derivatives_contexts: Optional[Dict[str, Any]] = None,
        provider_health: Optional[Dict[str, Any]] = None,
        trading_frozen: bool = False,
        freeze_reason: str = "",
    ):
        """Render the complete institutional telemetry dashboard."""
        self.last_telemetry_time = time.time()

        # 1. Desk & Capital Fortress Overview
        cd_str = f"[bold red]ACTIVE ({cooldown_remaining_sec/60:.1f}m)[/bold red]" if cooldown_active else "[bold green]INACTIVE[/bold green]"
        state_color = "green" if position_state == PositionState.FLAT else "yellow"
        trading_status_str = f"[bold red]FROZEN ({freeze_reason})[/bold red]" if trading_frozen else "[bold green]ONLINE[/bold green]"

        venues_str = ""
        if provider_health:
            v_parts = []
            for name, h in provider_health.items():
                status_val = getattr(h, "status", h)
                status_str = status_val.value if hasattr(status_val, "value") else str(status_val)
                color = "green" if status_str == "HEALTHY" else ("yellow" if status_str == "DEGRADED" else "red")
                v_parts.append(f"{name.upper()}: [{color}]{status_str}[/{color}]")
            venues_str = "\nVenue Health: " + " | ".join(v_parts)

        header_text = (
            f"[bold cyan]BYBIT V5 INSTITUTIONAL QUANTITATIVE SCALPING TRADER OS v1.0[/bold cyan]\n"
            f"Desk State: [{state_color}]{position_state.value}[/{state_color}] | "
            f"Trading: {trading_status_str} | "
            f"Total Equity: [bold green]${account_equity:,.2f}[/bold green] | "
            f"Available Cash: [bold green]${available_equity:,.2f}[/bold green] | "
            f"Cash Fortress: [{'green' if fortress_pct >= 85 else 'red'}]{fortress_pct:.1f}%[/] | "
            f"Daily DD: [{'yellow' if daily_dd_pct >= 2.0 else 'green'}]{daily_dd_pct:.2f}%[/] | "
            f"Loss Streak: {consecutive_losses} | Cooldown: {cd_str}"
            f"{venues_str}"
        )
        console.print(Panel(header_text, border_style="cyan"))


        # 2. Active Position Telemetry (if in trade)
        if active_position and position_state != PositionState.FLAT:
            pos_table = Table(title="ACTIVE POSITION MONITOR", border_style="yellow")
            pos_table.add_column("Symbol", style="bold")
            pos_table.add_column("Side")
            pos_table.add_column("Entry")
            pos_table.add_column("Mark")
            pos_table.add_column("SL Price")
            pos_table.add_column("TP1 Price")
            pos_table.add_column("TP2 Price")
            pos_table.add_column("Hold Time")
            pos_table.add_column("MAE / MFE")
            pos_table.add_column("State")

            snap = latest_snapshots.get(active_position.symbol)
            mark = snap.mark_price if snap else active_position.entry_price
            hold = time.time() - active_position.entry_time

            side_color = "green" if active_position.side.value == "Buy" else "red"
            pos_table.add_row(
                active_position.symbol,
                f"[{side_color}]{active_position.side.value}[/{side_color}]",
                f"${active_position.entry_price:.2f}",
                f"${mark:.2f}",
                f"${active_position.stop_price:.2f}",
                f"${active_position.tp1_price:.2f}",
                f"${active_position.tp2_price:.2f}",
                f"{hold:.0f}s",
                f"${active_position.mae:.2f} / ${active_position.mfe:.2f}",
                f"[bold magenta]{active_position.state.value}[/bold magenta]",
            )
            console.print(pos_table)

        # 3. Market Universes & Microstructure Telemetry
        market_table = Table(title="MARKET UNIVERSE MICROSTRUCTURE TELEMETRY", border_style="blue")
        market_table.add_column("Symbol")
        market_table.add_column("Price")
        market_table.add_column("Mark")
        market_table.add_column("Spread %")
        market_table.add_column("OBI (B/A)")
        market_table.add_column("CVD")
        market_table.add_column("15M WT1/WT2")
        market_table.add_column("Regime")
        market_table.add_column("Spoof Prob")

        for sym, snap in latest_snapshots.items():
            obi_color = "green" if snap.orderbook_imbalance >= 1.30 else ("red" if snap.orderbook_imbalance <= 0.75 else "white")
            market_table.add_row(
                sym,
                f"${snap.last_price:.2f}",
                f"${snap.mark_price:.2f}",
                f"{snap.spread_pct:.3f}%",
                f"[{obi_color}]{snap.orderbook_imbalance:.2f}[/{obi_color}]",
                f"{snap.cvd:+.1f}",
                f"{snap.wt1_15m:.1f} / {snap.wt2_15m:.1f}",
                f"[cyan]{snap.regime.value}[/cyan]",
                f"[{'red' if snap.spoof_probability >= 0.50 else 'green'}]{snap.spoof_probability:.2f}[/]",
            )
        console.print(market_table)

        # 3b. Multi-Exchange Cross-Venue & Derivatives Fusion (Sections 21-28)
        if cross_exchange_states:
            multi_table = Table(title="MULTI-EXCHANGE INTELLIGENCE (BYBIT + BINANCE + COINGLASS)", border_style="magenta")
            multi_table.add_column("Symbol")
            multi_table.add_column("Bybit P")
            multi_table.add_column("Binance P")
            multi_table.add_column("Basis ($ / %)")
            multi_table.add_column("Sync State")
            multi_table.add_column("Bybit CVD")
            multi_table.add_column("Binance CVD")
            multi_table.add_column("Flow Conf")
            multi_table.add_column("Agg OI")
            multi_table.add_column("Deriv Score")
            multi_table.add_column("Cross Conf")

            for sym, cross in cross_exchange_states.items():
                deriv = derivatives_contexts.get(sym) if derivatives_contexts else None
                sync_color = "green" if cross.price_sync.value == "SYNCED" else ("yellow" if "LEADING" in cross.price_sync.value else "red")
                flow_color = "green" if "CONFIRMED" in cross.orderflow_confirmation.value else ("yellow" if "LOCAL" in cross.orderflow_confirmation.value else "white")

                bin_p_str = f"${cross.binance_price:.2f}" if cross.binance_price > 0 else "N/A"
                basis_str = f"{cross.basis:+.2f} ({cross.basis_pct:+.2f}%)" if cross.binance_price > 0 else "N/A"
                agg_oi_str = f"${deriv.aggregate_oi:,.0f}" if deriv and deriv.aggregate_oi > 0 else "N/A"
                deriv_score_str = f"{deriv.derivatives_context_score:+.1f}" if deriv else "0.0"

                multi_table.add_row(
                    sym,
                    f"${cross.bybit_price:.2f}",
                    bin_p_str,
                    basis_str,
                    f"[{sync_color}]{cross.price_sync.value}[/{sync_color}]",
                    f"{cross.bybit_cvd:+.1f}",
                    f"{cross.binance_cvd:+.1f}",
                    f"[{flow_color}]{cross.orderflow_confirmation.value}[/{flow_color}]",
                    agg_oi_str,
                    deriv_score_str,
                    f"{cross.confidence_score:.0f}%",
                )
            console.print(multi_table)

        # 4. Top Opportunity Candidates (Section 103)
        if top_candidates:

            cand_table = Table(title="TOP OPPORTUNITY CANDIDATES (RANKED BY EV * EXEC * CONF)", border_style="green")
            cand_table.add_column("Rank")
            cand_table.add_column("Symbol")
            cand_table.add_column("Strategy")
            cand_table.add_column("Side")
            cand_table.add_column("Score")
            cand_table.add_column("Net EV")
            cand_table.add_column("Exec Score")
            cand_table.add_column("Entry")
            cand_table.add_column("Stop")
            cand_table.add_column("TP1 / TP2")

            for idx, c in enumerate(top_candidates[:5], 1):
                side_c = "green" if c.side.value == "Buy" else "red"
                cand_table.add_row(
                    str(idx),
                    c.symbol,
                    c.strategy.value,
                    f"[{side_c}]{c.side.value}[/{side_c}]",
                    f"{c.alpha_score:.1f}",
                    f"{c.expected_ev * 100:+.2f}%",
                    f"{c.features.get('execution_score', 0):.1f}",
                    f"${c.entry:.2f}",
                    f"${c.structural_stop:.2f}",
                    f"${c.tp1:.2f} / ${c.tp2:.2f}",
                )
            console.print(cand_table)

        # 5. Rejected Candidates Log (Sections 33/65)
        if rejected_candidates:
            rej_table = Table(title="RECENT CANDIDATES REJECTED BY POLICY GATE", border_style="red")
            rej_table.add_column("Symbol")
            rej_table.add_column("Strategy")
            rej_table.add_column("Alpha Score")
            rej_table.add_column("Primary Rejection Reason")

            for r in rejected_candidates[-4:]:
                rej_table.add_row(
                    r.get("symbol", ""),
                    r.get("strategy", ""),
                    f"{r.get('score', 0):.1f}",
                    f"[yellow]{r.get('reason', '')}[/yellow]",
                )
            console.print(rej_table)
        console.print("")
