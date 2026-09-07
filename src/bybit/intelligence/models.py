"""
Unified Multi-Exchange Market Intelligence Data Models & Canonical Symbol Mapper.
Ingests, normalizes, validates, and standardizes Bybit, Binance, and CoinGlass feeds.
"""

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Dict, Any, List, Optional, Tuple, Set


# ----------------------------------------------------------------------
# 1. Canonical Symbol Architecture (Section 2)
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class CanonicalSymbol:
    """Canonical representation of an instrument with venue-specific mappings."""
    canonical: str         # e.g., "BTC-USDT"
    base: str              # e.g., "BTC"
    quote: str             # e.g., "USDT"
    bybit: str             # e.g., "BTCUSDT"
    binance: str           # e.g., "BTCUSDT"
    coinglass: str         # e.g., "BTC"

    def __str__(self) -> str:
        return self.canonical


class SymbolMapper:
    """Explicit bidirectional symbol mapping repository."""

    def __init__(self):
        self._by_canonical: Dict[str, CanonicalSymbol] = {}
        self._by_bybit: Dict[str, CanonicalSymbol] = {}
        self._by_binance: Dict[str, CanonicalSymbol] = {}
        self._by_coinglass: Dict[str, CanonicalSymbol] = {}

    def register(self, symbol: CanonicalSymbol):
        """Register explicit canonical mapping."""
        self._by_canonical[symbol.canonical] = symbol
        self._by_bybit[symbol.bybit] = symbol
        self._by_binance[symbol.binance] = symbol
        self._by_coinglass[symbol.coinglass] = symbol

    def from_canonical(self, canonical: str) -> Optional[CanonicalSymbol]:
        canonical = canonical.upper()
        if canonical in self._by_canonical:
            return self._by_canonical[canonical]
        # Auto-derive if standard format BASE-QUOTE
        if "-" in canonical:
            parts = canonical.split("-")
            if len(parts) == 2:
                derived = CanonicalSymbol(
                    canonical=canonical,
                    base=parts[0],
                    quote=parts[1],
                    bybit=f"{parts[0]}{parts[1]}",
                    binance=f"{parts[0]}{parts[1]}",
                    coinglass=parts[0],
                )
                self.register(derived)
                return derived
        return None

    def get(self, canonical: str) -> Optional[CanonicalSymbol]:
        """Convenience alias for from_canonical."""
        return self.from_canonical(canonical)

    def get_or_create(self, canonical: str) -> CanonicalSymbol:
        """Get existing or auto-derive and register canonical symbol."""
        sym = self.from_canonical(canonical)
        if sym is None:
            parts = canonical.split("-") if "-" in canonical else [canonical, "USDT"]
            sym = CanonicalSymbol(
                canonical=f"{parts[0]}-{parts[1]}",
                base=parts[0],
                quote=parts[1],
                bybit=f"{parts[0]}{parts[1]}",
                binance=f"{parts[0]}{parts[1]}",
                coinglass=parts[0],
            )
            self.register(sym)
        return sym

    def from_coinglass(self, cg_symbol: str) -> CanonicalSymbol:
        """Resolve CoinGlass symbol to canonical."""
        cg_symbol = cg_symbol.upper()
        if cg_symbol in self._by_coinglass:
            return self._by_coinglass[cg_symbol]
        return self.get_or_create(f"{cg_symbol}-USDT")


    def from_bybit(self, bybit_symbol: str) -> CanonicalSymbol:
        """Resolve Bybit symbol to canonical, auto-deriving if not registered."""
        bybit_symbol = bybit_symbol.upper()
        if bybit_symbol in self._by_bybit:
            return self._by_bybit[bybit_symbol]

        for quote in ("USDT", "USDC", "USD"):
            if bybit_symbol.endswith(quote):
                base = bybit_symbol[:-len(quote)]
                derived = CanonicalSymbol(
                    canonical=f"{base}-{quote}",
                    base=base,
                    quote=quote,
                    bybit=bybit_symbol,
                    binance=bybit_symbol,
                    coinglass=base,
                )
                self.register(derived)
                return derived

        # Fallback
        derived = CanonicalSymbol(
            canonical=f"{bybit_symbol}-USDT",
            base=bybit_symbol,
            quote="USDT",
            bybit=bybit_symbol,
            binance=bybit_symbol,
            coinglass=bybit_symbol,
        )
        self.register(derived)
        return derived

    def from_binance(self, binance_symbol: str) -> CanonicalSymbol:
        """Resolve Binance symbol to canonical."""
        binance_symbol = binance_symbol.upper()
        if binance_symbol in self._by_binance:
            return self._by_binance[binance_symbol]
        return self.from_bybit(binance_symbol)


# Global default mapper
default_symbol_mapper = SymbolMapper()


# ----------------------------------------------------------------------
# 2. Normalized Event & Data Point Types (Sections 19, 32)
# ----------------------------------------------------------------------

class EventType(str, Enum):
    # Primary venue events
    ORDERBOOK_UPDATE = "ORDERBOOK_UPDATE"
    TRADE_UPDATE = "TRADE_UPDATE"
    PRICE_UPDATE = "PRICE_UPDATE"
    CANDLE_UPDATE = "CANDLE_UPDATE"
    OI_UPDATE = "OI_UPDATE"
    FUNDING_UPDATE = "FUNDING_UPDATE"
    LIQUIDATION_UPDATE = "LIQUIDATION_UPDATE"
    INSTRUMENT_UPDATE = "INSTRUMENT_UPDATE"
    ACCOUNT_UPDATE = "ACCOUNT_UPDATE"
    ORDER_UPDATE = "ORDER_UPDATE"
    FILL_UPDATE = "FILL_UPDATE"
    POSITION_UPDATE = "POSITION_UPDATE"

    # Cross-exchange & derived intelligence
    CVD_UPDATE = "CVD_UPDATE"
    REGIME_UPDATE = "REGIME_UPDATE"
    CROSS_EXCHANGE_UPDATE = "CROSS_EXCHANGE_UPDATE"
    DERIVATIVES_CONTEXT_UPDATE = "DERIVATIVES_CONTEXT_UPDATE"
    DATA_QUALITY_UPDATE = "DATA_QUALITY_UPDATE"


class DataQuality(str, Enum):
    FRESH = "FRESH"          # < 1.0s
    WARNING = "WARNING"      # 1.0s - 3.0s
    STALE = "STALE"          # 3.0s - 10.0s
    INVALID = "INVALID"      # > 10.0s or corrupted


@dataclass
class DataPoint:
    """Strongly typed datum with freshness and source attribution."""
    value: Any
    event_timestamp: float
    receive_timestamp: float
    age_ms: float
    source: str
    quality: DataQuality


@dataclass
class TradeEvent:
    """Normalized tick trade event across Bybit and Binance."""
    source: str                 # "bybit" | "binance"
    canonical_symbol: str       # e.g. "BTC-USDT"
    price: float
    quantity: float
    side: str                   # "Buy" | "Sell"
    exchange_timestamp: float
    receive_timestamp: float


@dataclass
class OrderBookSnapshot:
    """Normalized L50 orderbook snapshot."""
    source: str
    canonical_symbol: str
    bids: List[Tuple[float, float]]     # sorted desc by price
    asks: List[Tuple[float, float]]     # sorted asc by price
    best_bid: float
    best_ask: float
    mid: float
    spread: float
    spread_pct: float
    timestamp: float
    sequence: int
    bid_depth: float
    ask_depth: float
    obi: float
    microprice: float


@dataclass
class LiquidationEvent:
    """Normalized liquidation event."""
    source: str
    canonical_symbol: str
    side: str                   # "Buy" (short liquidated) | "Sell" (long liquidated)
    price: float
    quantity: float
    notional: float
    timestamp: float


@dataclass
class OpenInterestEvent:
    """Normalized Open Interest observation."""
    source: str
    canonical_symbol: str
    open_interest: float
    notional_usd: float
    exchange_timestamp: float
    receive_timestamp: float


@dataclass
class FundingEvent:
    """Normalized funding rate update."""
    source: str
    canonical_symbol: str
    funding_rate: float
    next_funding_time: float
    timestamp: float


# ----------------------------------------------------------------------
# 3. Cross-Exchange & Derivatives Enums (Sections 21–26, 40)
# ----------------------------------------------------------------------

class PriceSyncState(str, Enum):
    SYNCED = "SYNCED"
    BYBIT_LEADING = "BYBIT_LEADING"
    BINANCE_LEADING = "BINANCE_LEADING"
    DIVERGING = "DIVERGING"
    UNSTABLE = "UNSTABLE"


class OrderflowConfirmation(str, Enum):
    CONFIRMED_BULLISH = "CONFIRMED_BULLISH"
    CONFIRMED_BEARISH = "CONFIRMED_BEARISH"
    MIXED = "MIXED"
    BYBIT_LOCAL = "BYBIT_LOCAL"
    BINANCE_LEADING = "BINANCE_LEADING"
    UNCERTAIN = "UNCERTAIN"


class LiquidityClassification(str, Enum):
    BROAD_SUPPORT = "BROAD_SUPPORT"
    LOCAL_BYBIT_SUPPORT = "LOCAL_BYBIT_SUPPORT"
    BROAD_RESISTANCE = "BROAD_RESISTANCE"
    LOCAL_BYBIT_RESISTANCE = "LOCAL_BYBIT_RESISTANCE"
    LIQUIDITY_CONFLICT = "LIQUIDITY_CONFLICT"
    UNKNOWN = "UNKNOWN"


class OIRegime(str, Enum):
    BROAD_POSITION_BUILD = "BROAD_POSITION_BUILD"
    LOCAL_POSITION_BUILD = "LOCAL_POSITION_BUILD"
    BROAD_DELEVERAGING = "BROAD_DELEVERAGING"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class FundingRegime(str, Enum):
    LONG_CROWDED = "LONG_CROWDED"
    SHORT_CROWDED = "SHORT_CROWDED"
    NEUTRAL = "NEUTRAL"
    DISLOCATED = "DISLOCATED"
    UNKNOWN = "UNKNOWN"


class LiquidationRegime(str, Enum):
    LONG_LIQUIDATION = "LONG_LIQUIDATION"
    SHORT_LIQUIDATION = "SHORT_LIQUIDATION"
    LONG_SQUEEZE = "LONG_SQUEEZE"
    SHORT_SQUEEZE = "SHORT_SQUEEZE"
    CASCADE = "CASCADE"
    EXHAUSTION = "EXHAUSTION"
    NORMAL = "NORMAL"


class MarketMoveClassification(str, Enum):
    GLOBAL_MOVE = "GLOBAL_MOVE"
    LOCAL_BYBIT_MOVE = "LOCAL_BYBIT_MOVE"
    BINANCE_LED_MOVE = "BINANCE_LED_MOVE"
    CROSS_EXCHANGE_DISLOCATION = "CROSS_EXCHANGE_DISLOCATION"
    POSSIBLE_LOCAL_LIQUIDITY_EVENT = "POSSIBLE_LOCAL_LIQUIDITY_EVENT"
    UNKNOWN = "UNKNOWN"


class ProviderStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNSAFE = "UNSAFE"
    OFFLINE = "OFFLINE"


@dataclass
class ProviderHealth:
    """Real-time connectivity and operational health status."""
    provider_name: str
    connected: bool = False
    authenticated: bool = False
    last_message_ts: float = 0.0
    last_successful_request_ts: float = 0.0
    reconnect_count: int = 0
    latency_ms: float = 0.0
    error_count: int = 0
    rate_limit_state: Dict[str, Any] = field(default_factory=dict)
    status: ProviderStatus = ProviderStatus.OFFLINE


# ----------------------------------------------------------------------
# 4. Cross-Exchange State & Derivatives Context (Sections 20, 51)
# ----------------------------------------------------------------------

@dataclass
class CrossExchangeState:
    """Real-time Bybit vs Binance cross-exchange comparisons."""
    symbol: str
    bybit_price: float = 0.0
    binance_price: float = 0.0
    basis: float = 0.0                      # Bybit - Binance
    basis_pct: float = 0.0                  # (Bybit - Binance) / Binance * 100
    basis_velocity: float = 0.0             # 10s rate of basis change
    price_sync: PriceSyncState = PriceSyncState.SYNCED

    bybit_cvd: float = 0.0
    binance_cvd: float = 0.0
    orderflow_confirmation: OrderflowConfirmation = OrderflowConfirmation.UNCERTAIN

    bybit_obi: float = 1.0
    binance_obi: float = 1.0
    liquidity_classification: LiquidityClassification = LiquidityClassification.UNKNOWN

    confidence_score: float = 50.0          # 0 to 100
    timestamp: float = 0.0


@dataclass
class DerivativesContext:
    """High-level multi-venue derivatives positioning and sentiment."""
    symbol: str
    bybit_oi: float = 0.0
    binance_oi: float = 0.0
    aggregate_oi: float = 0.0
    oi_delta_1h: float = 0.0
    oi_velocity: float = 0.0
    oi_regime: OIRegime = OIRegime.UNKNOWN

    bybit_funding: float = 0.0
    binance_funding: float = 0.0
    funding_spread: float = 0.0
    funding_regime: FundingRegime = FundingRegime.NEUTRAL

    liquidation_intensity: float = 0.0
    liquidation_regime: LiquidationRegime = LiquidationRegime.NORMAL
    move_classification: MarketMoveClassification = MarketMoveClassification.UNKNOWN

    derivatives_context_score: float = 0.0  # -100 to +100
    timestamp: float = 0.0


@dataclass
class NormalizedMarketState:
    """
    Section 51 Unified Market State Specification.
    Full immutable snapshot consumed by Alpha, Risk, and Telemetry.
    """
    symbol: str
    timestamp: float

    # Authoritative Bybit Venue State
    bybit_price: float
    bybit_mark: float
    bybit_index: float
    bybit_spread: float
    bybit_obi: float
    bybit_cvd: float
    bybit_oi: float
    bybit_funding: float

    # Binance Cross-Exchange Confirmation
    binance_price: Optional[float] = None
    binance_spread: Optional[float] = None
    binance_obi: Optional[float] = None
    binance_cvd: Optional[float] = None
    binance_oi: Optional[float] = None
    binance_funding: Optional[float] = None

    # CoinGlass & Aggregated Derivatives
    aggregate_oi: Optional[float] = None
    aggregate_funding: Optional[float] = None
    aggregate_liquidations: Optional[float] = None

    # Cross-Exchange Intelligence & Fusion
    cross_exchange_basis: Optional[float] = None
    cross_exchange_confidence: float = 50.0
    derivatives_context_score: float = 0.0

    # Scores & Regimes
    volatility_score: float = 50.0
    liquidity_score: float = 50.0
    data_quality_score: float = 100.0
    regime: str = "UNCERTAIN"
