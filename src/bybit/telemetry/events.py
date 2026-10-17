"""
Institutional Telemetry Event Contracts.
Defines strongly typed event models for Market, Brain, Execution, Decision Trace, API Observatory, and Alerts.
"""

from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional
import time


@dataclass
class MarketTelemetryEvent:
    """Real-time market microstructure and orderbook state."""
    symbol: str
    timestamp: float
    last_price: float
    mark_price: float
    spread_pct: float
    best_bid: float
    best_ask: float
    bid_depth: float
    ask_depth: float
    orderbook_imbalance: float
    weighted_obi: float = 1.0
    cvd: float = 0.0
    trade_velocity: float = 0.0
    funding_rate: float = 0.0
    aggregate_oi: float = 0.0
    basis: float = 0.0
    basis_pct: float = 0.0
    regime: str = "RANGE"
    vwap_24h: float = 0.0
    nearest_fvg: float = 0.0
    external_liq: float = 0.0
    bid_walls_usd: float = 0.0
    ask_walls_usd: float = 0.0
    absorption_status: str = "LOW"
    orderbook_valid: bool = True

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = "MARKET_TELEMETRY"
        return d


@dataclass
class BrainTelemetryEvent:
    """Alpha scanner, SMC auditor, and risk decision state."""
    symbol: str
    timestamp: float
    strategy: str
    alpha_score: float
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    smc_auditor_checks: Dict[str, str] = field(default_factory=dict)  # PASS, VETO, PENDING
    expected_ev: float = 0.0
    rr_tp1: float = 0.0
    rr_tp2: float = 0.0
    risk_pct: float = 0.0
    kelly_pct: float = 0.0
    drawdown_scalar: float = 1.0
    final_decision: str = "NEUTRAL"  # TRADE, VETO, NEUTRAL
    decision_reason: str = ""
    target_qty: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = "BRAIN_TELEMETRY"
        return d


@dataclass
class ExecutionTelemetryEvent:
    """Authoritative desk position and execution state."""
    timestamp: float
    position_state: str  # FLAT, PENDING_ENTRY, ENTRY_PLACED, ACTIVE_PROTECTED, etc.
    active_position: Optional[Dict[str, Any]] = None
    recent_fills: List[Dict[str, Any]] = field(default_factory=list)
    reconciliation_status: str = "HEALTHY"
    last_reconciliation_ts: float = 0.0
    maker_fee_bps: float = 2.0
    taker_fee_bps: float = 5.5

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = "EXECUTION_TELEMETRY"
        return d


@dataclass
class DecisionTraceStep:
    """Atomic decision trace step during evaluation."""
    timestamp: float
    symbol: str
    stage: str  # SCANNER, SWEEP, SFP, OBI, ALPHA, SMC_AUDITOR, RISK, PRECHECK, ORDER, FILL, VETO
    status: str  # PASS, VETO, INFO, WARN
    message: str
    requirement: Optional[str] = None
    actual: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = "DECISION_TRACE"
        return d


@dataclass
class ApiPingEvent:
    """Single API or WebSocket ping/message observation."""
    timestamp: float
    venue: str  # Bybit, Binance, CoinGlass
    endpoint: str
    latency_ms: float
    status: str  # 200, 429, WS, TIMEOUT
    message_type: str = "REST"  # REST or WS

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = "API_PING"
        return d


@dataclass
class SystemStatusEvent:
    """Overall system and venue operational health."""
    timestamp: float
    bot_status: str  # ONLINE, FROZEN, UNKNOWN, SHUTDOWN
    freeze_reason: str = ""
    uptime_sec: float = 0.0
    cpu_pct: float = 0.0
    ram_mb: float = 0.0
    event_loop_lag_ms: float = 0.0
    clock_drift_ms: float = 0.0
    bybit_public_ws: str = "HEALTHY"
    bybit_private_ws: str = "HEALTHY"
    binance_ws: str = "HEALTHY"
    coinglass_rest: str = "HEALTHY"
    watchdog_status: str = "HEALTHY"
    kill_switch_active: bool = False
    kill_switch_matrix: Dict[str, bool] = field(default_factory=dict)
    cooldown_active: bool = False
    cooldown_remaining_sec: float = 0.0
    account_equity: float = 0.0
    available_balance: float = 0.0
    cash_fortress_pct: float = 100.0
    used_margin: float = 0.0
    daily_drawdown_pct: float = 0.0
    consecutive_losses: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = "SYSTEM_STATUS"
        return d


@dataclass
class AlertEvent:
    """Real-time operational alert."""
    timestamp: float
    severity: str  # CRITICAL, WARNING, INFO
    title: str
    message: str
    source: str

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = "ALERT"
        return d


@dataclass
class PerformanceTelemetryEvent:
    """Real-time portfolio growth, P&L, and ROI metrics."""
    timestamp: float
    starting_equity: float
    current_equity: float
    total_pnl_usd: float
    total_roi_pct: float
    daily_pnl_usd: float
    daily_roi_pct: float
    realized_pnl_usd: float
    unrealized_pnl_usd: float
    win_count: int = 0
    loss_count: int = 0
    total_trades: int = 0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    total_fees: float = 0.0
    expectancy: float = 0.0
    max_drawdown_pct: float = 0.0
    strategy_stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = "PERFORMANCE_TELEMETRY"
        return d

