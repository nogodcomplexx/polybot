"""
Data models for the trading bot.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    GTC = "GTC"    # Good-Till-Cancelled
    GTD = "GTD"    # Good-Till-Date
    FOK = "FOK"    # Fill-Or-Kill
    FAK = "FAK"    # Fill-And-Kill


class TradeStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class Conviction(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------

@dataclass
class Market:
    """A single Polymarket market (one question, two outcomes)."""
    condition_id: str
    question: str
    description: str = ""
    category: str = ""
    end_date: str = ""
    active: bool = True
    closed: bool = False
    liquidity: float = 0.0
    volume_24h: float = 0.0
    # Token IDs for YES and NO outcomes
    yes_token_id: str = ""
    no_token_id: str = ""
    # Prices
    yes_price: float = 0.0
    no_price: float = 0.0
    spread: float = 0.0
    # Metadata
    tags: List[str] = field(default_factory=list)
    slug: str = ""
    image: str = ""


@dataclass
class OrderBook:
    """Order book snapshot for a token."""
    token_id: str
    bids: List[Dict[str, float]] = field(default_factory=list)  # [{price, size}]
    asks: List[Dict[str, float]] = field(default_factory=list)
    midpoint: float = 0.0
    spread: float = 0.0
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# News & Sentiment
# ---------------------------------------------------------------------------

@dataclass
class NewsArticle:
    """A single news article from any source."""
    title: str
    description: str = ""
    content: str = ""
    source: str = ""
    url: str = ""
    published_at: str = ""
    fetched_at: float = field(default_factory=time.time)
    # Deduplication
    content_hash: str = ""


@dataclass
class SentimentResult:
    """Sentiment analysis result for a piece of text."""
    text: str
    score: float = 0.0          # -1.0 (very negative) to +1.0 (very positive)
    confidence: float = 0.0     # 0.0 to 1.0
    label: str = "neutral"      # positive, negative, neutral
    key_phrases: List[str] = field(default_factory=list)
    model_used: str = ""


@dataclass
class MarketSignal:
    """A signal linking news sentiment to a specific market."""
    market: Market
    articles: List[NewsArticle] = field(default_factory=list)
    sentiment: Optional[SentimentResult] = None
    relevance_score: float = 0.0    # How relevant the news is to this market


# ---------------------------------------------------------------------------
# Prediction & Edge
# ---------------------------------------------------------------------------

@dataclass
class PredictionResult:
    """Bot's estimated probability vs market price."""
    market: Market
    our_yes_prob: float = 0.0       # Our estimated probability for YES
    market_yes_price: float = 0.0   # Current market YES price
    edge: float = 0.0               # our_yes_prob - market_yes_price
    conviction: Conviction = Conviction.LOW
    confidence: float = 0.0
    reasoning: str = ""
    # Breakdown
    news_signal: float = 0.0
    volume_signal: float = 0.0
    historical_signal: float = 0.0


# ---------------------------------------------------------------------------
# Trading
# ---------------------------------------------------------------------------

@dataclass
class TradeSignal:
    """A recommended trade from a strategy."""
    strategy_name: str
    market: Market
    side: Side
    token_id: str               # Which token to buy/sell
    target_price: float = 0.0   # Limit price
    size_usd: float = 0.0       # Position size in USD
    edge: float = 0.0
    conviction: Conviction = Conviction.LOW
    confidence: float = 0.0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    reasoning: str = ""
    order_type: OrderType = OrderType.GTC
    slug: str = ""
    underlying_symbol: str = ""     # e.g. BTCUSDT
    session: str = ""               # ASIA/EU/NA/LATE
    direction: str = ""             # UP or DOWN
    btc_dominance_pct: float = 0.0
    btc_dominance_change_1h_pct: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class Trade:
    """An executed (or simulated) trade."""
    id: str = ""
    strategy_name: str = ""
    market_question: str = ""
    condition_id: str = ""
    token_id: str = ""
    side: str = "BUY"
    order_type: str = "GTC"
    price: float = 0.0
    size: float = 0.0           # Number of shares
    cost_usd: float = 0.0      # Total cost
    status: str = TradeStatus.PENDING.value
    order_id: str = ""          # Polymarket order ID
    filed_at: float = 0.0
    created_at: float = field(default_factory=time.time)
    pnl: float = 0.0
    is_paper: bool = True
    slug: str = ""
    underlying_symbol: str = ""
    session: str = ""
    direction: str = ""
    btc_dominance_pct: float = 0.0
    btc_dominance_change_1h_pct: float = 0.0
    # Paper execution realism + learning signals
    intended_cost_usd: float = 0.0     # Notional we tried to deploy (for OPEN orders)
    replace_count: int = 0             # How many cancel/replace hops were used (paper)
    model_confidence: float = 0.0      # Strategy/model confidence at entry time (0..1)
    model_edge: float = 0.0            # Strategy/model edge at entry time (prob - price)


@dataclass
class Position:
    """An open position in a market."""
    condition_id: str
    token_id: str
    market_question: str = ""
    side: str = "BUY"
    entry_price: float = 0.0
    current_price: float = 0.0
    size: float = 0.0
    cost_usd: float = 0.0
    unrealized_pnl: float = 0.0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    strategy_name: str = ""
    slug: str = ""
    underlying_symbol: str = ""
    direction: str = ""
    opened_at: float = field(default_factory=time.time)


@dataclass
class PortfolioSnapshot:
    """Portfolio state at a point in time."""
    timestamp: float = field(default_factory=time.time)
    total_value_usd: float = 0.0
    cash_usd: float = 0.0
    positions_value_usd: float = 0.0
    daily_pnl: float = 0.0
    total_pnl: float = 0.0
    num_open_positions: int = 0
    num_trades_today: int = 0
