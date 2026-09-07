"""
Core Data Models for Bybit V5 Linear Perpetual Scalper.
Defines all domain entities, enumerations, market snapshots, signals, and execution plans.
"""

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Dict, Any, List, Optional


class PositionState(str, Enum):
    """15-State Position Lifecycle State Machine."""
    FLAT = "FLAT"
    SIGNAL_DETECTED = "SIGNAL_DETECTED"
    POLICY_PENDING = "POLICY_PENDING"
    ENTRY_PENDING = "ENTRY_PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    PROTECTION_PENDING = "PROTECTION_PENDING"
    ACTIVE = "ACTIVE"
    TP1_FILLED = "TP1_FILLED"
    PROTECTED_RUNNER = "PROTECTED_RUNNER"
    DAMAGE_CONTROL = "DAMAGE_CONTROL"
    EXITING = "EXITING"
    CLOSED = "CLOSED"
    RECONCILING = "RECONCILING"
    KILLED = "KILLED"
    ERROR = "ERROR"


class MarketRegime(str, Enum):
    """9 Deterministic Market Regimes."""
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    HIGH_VOL = "HIGH_VOL"
    LOW_VOL = "LOW_VOL"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    LIQUIDATION_CASCADE = "LIQUIDATION_CASCADE"
    SHOCK = "SHOCK"
    UNCERTAIN = "UNCERTAIN"


class StrategyName(str, Enum):
    """Alpha Strategy Identifiers."""
    TURTLE_SOUP = "TURTLE_SOUP"
    VWAP_FVG_REVERSION = "VWAP_FVG_REVERSION"
    ABSORPTION = "ABSORPTION"
    TREND_CONTINUATION = "TREND_CONTINUATION"


class OrderSide(str, Enum):
    BUY = "Buy"
    SELL = "Sell"


class OrderType(str, Enum):
    LIMIT = "Limit"
    MARKET = "Market"


class TimeInForce(str, Enum):
    POST_ONLY = "PostOnly"
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"


class FailureType(str, Enum):
    """Post-Trade Attribution Failure Categories."""
    NORMAL_WIN = "NORMAL_WIN"
    NORMAL_LOSS = "NORMAL_LOSS"
    ALPHA_FAILURE = "ALPHA_FAILURE"
    ENTRY_FAILURE = "ENTRY_FAILURE"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    REGIME_FAILURE = "REGIME_FAILURE"
    BTC_FAILURE = "BTC_FAILURE"
    RISK_FAILURE = "RISK_FAILURE"
    EXIT_FAILURE = "EXIT_FAILURE"
    EXCHANGE_FAILURE = "EXCHANGE_FAILURE"


class FailureLevel(str, Enum):
    """3 Levels of Failure Response."""
    LEVEL_1_DEGRADED = "LEVEL_1_DEGRADED"
    LEVEL_2_UNSAFE = "LEVEL_2_UNSAFE"
    LEVEL_3_CRITICAL = "LEVEL_3_CRITICAL"


@dataclass
class MarketSnapshot:
    """Immutable market data snapshot across all timeframes & microstructures."""
    timestamp: float
    symbol: str
    last_price: float = 0.0
    mark_price: float = 0.0
    index_price: float = 0.0
    spread: float = 0.0
    spread_pct: float = 0.0
    best_bid: float = 0.0
    best_ask: float = 0.0
    bid_depth: float = 0.0
    ask_depth: float = 0.0
    orderbook_imbalance: float = 1.0  # OBI = BidVolume / AskVolume
    microprice: float = 0.0
    cvd: float = 0.0
    cvd_delta: float = 0.0
    trade_velocity: float = 0.0       # trades per sec
    volume_velocity: float = 0.0      # volume per sec
    open_interest: float = 0.0
    oi_delta: float = 0.0
    funding: float = 0.0
    next_funding_time: float = 0.0
    liquidation_flow: float = 0.0
    vwap_1m: float = 0.0
    vwap_5m: float = 0.0
    vwap_15m: float = 0.0
    vwap_24h: float = 0.0
    atr_1m: float = 0.0
    atr_5m: float = 0.0
    atr_15m: float = 0.0
    rsi_15m: float = 50.0
    wt1_15m: float = 0.0
    wt2_15m: float = 0.0
    wt1_prev: float = 0.0
    wt_curling: bool = False
    btc_state: str = "NEUTRAL"
    regime: MarketRegime = MarketRegime.UNCERTAIN
    spoof_probability: float = 0.0
    event_time: float = field(default_factory=time.time)
    receive_time: float = field(default_factory=time.time)
    processing_time: float = field(default_factory=time.time)


@dataclass
class Signal:
    """Alpha strategy trade candidate signal."""
    id: str
    timestamp: float
    symbol: str
    side: OrderSide
    strategy: StrategyName
    regime: MarketRegime
    entry: float
    structural_stop: float
    tp1: float
    tp2: float
    alpha_score: float = 0.0
    expected_ev: float = 0.0
    confidence: float = 0.0
    thesis_expiry: float = 0.0
    expected_hold_time_sec: float = 300.0
    features: Dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""


@dataclass
class PolicyDecision:
    """Policy & Risk Gate evaluation output."""
    approved: bool
    risk_pct: float = 0.0
    risk_usd: float = 0.0
    rejection_reason: str = ""
    checks: Dict[str, bool] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    policy_version: str = "v1.0"


@dataclass
class ExecutionPlan:
    """Fully validated pre-trade execution directive."""
    symbol: str
    side: OrderSide
    quantity: float
    entry_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    order_type: OrderType = OrderType.LIMIT
    post_only: bool = True
    ttl_seconds: float = 45.0
    client_order_id: str = ""
    created_time: float = field(default_factory=time.time)
    max_price_deviation_pct: float = 0.15


@dataclass
class TradePlan:
    """Immutable pre-trade audit object (Section 110)."""
    symbol: str
    side: OrderSide
    strategy: StrategyName
    regime: MarketRegime
    signal_timestamp: float
    expiry_timestamp: float
    entry_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    alpha_score: float
    execution_score: float
    expected_ev: float
    win_probability: float
    risk_pct: float
    risk_usd: float
    quantity: float
    leverage: int
    btc_context: str
    orderbook_context: Dict[str, Any]
    invalidation: str
    policy_version: str = "v1.0"
    strategy_version: str = "v1.0"
    execution_version: str = "v1.0"


@dataclass
class ActivePosition:
    """Current live or paper position record."""
    symbol: str
    side: OrderSide
    size: float
    entry_price: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    leverage: int
    strategy: StrategyName
    regime: MarketRegime
    client_order_id: str
    entry_time: float
    state: PositionState = PositionState.ACTIVE
    filled_quantity: float = 0.0
    tp1_filled: bool = False
    tp2_filled: bool = False
    stop_order_id: Optional[str] = None
    tp1_order_id: Optional[str] = None
    tp2_order_id: Optional[str] = None
    mae: float = 0.0  # Maximum Adverse Excursion
    mfe: float = 0.0  # Maximum Favorable Excursion
    time_to_mae: float = 0.0
    time_to_mfe: float = 0.0
    expected_target_time: float = 300.0
    max_thesis_lifetime: float = 600.0
    alpha_score: float = 0.0
    execution_score: float = 0.0
    expected_ev: float = 0.0
    notional: float = 0.0
    trade_plan: Optional[TradePlan] = None
