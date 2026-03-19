"""
Configuration loader — merges .env secrets with config.yaml settings.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional

import yaml
from dotenv import load_dotenv


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    """Load .env from project root."""
    env_path = _PROJECT_ROOT / ".env"
    load_dotenv(env_path)


def _load_yaml() -> dict:
    """Load config.yaml from project root."""
    cfg_path = _PROJECT_ROOT / "config.yaml"
    if not cfg_path.exists():
        return {}
    with open(cfg_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PolymarketConfig:
    clob_host: str = "https://clob.polymarket.com"
    gamma_host: str = "https://gamma-api.polymarket.com"
    chain_id: int = 137
    private_key: str = ""
    api_key: str = ""
    api_secret: str = ""
    api_passphrase: str = ""
    relayer_api_key: str = ""
    relayer_api_key_address: str = ""


@dataclass
class DiscoveryConfig:
    min_liquidity_usd: float = 500
    min_volume_24h: float = 100
    max_days_to_resolution: int = 90
    min_days_to_resolution: float = 0.5
    categories: List[str] = field(default_factory=list)


@dataclass
class NewsConfig:
    newsapi_key: str = ""
    newsapi_poll_interval_minutes: int = 15
    rss_poll_interval_minutes: int = 5
    max_articles_per_cycle: int = 50
    rss_feeds: List[str] = field(default_factory=list)


@dataclass
class SentimentConfig:
    model: str = "finbert"
    min_confidence: float = 0.6
    llm_provider: str = ""
    llm_api_key: str = ""


@dataclass
class PredictionConfig:
    weights: dict = field(default_factory=lambda: {
        "market_price": 0.40,
        "news_sentiment": 0.30,
        "volume_momentum": 0.15,
        "historical_patterns": 0.15,
    })
    min_edge_pct: float = 5.0
    high_conviction_edge_pct: float = 15.0
    medium_conviction_edge_pct: float = 8.0


@dataclass
class RiskConfig:
    max_position_pct: float = 15.0
    max_category_pct: float = 40.0
    daily_loss_limit_pct: float = 20.0
    cash_buffer_pct: float = 10.0          # Reduced from 20% → more capital deployed
    kelly_fraction: float = 0.25
    stop_loss_pct: float = 25.0
    trailing_stop_pct: float = 15.0
    reserve_lock_pct: float = 20.0         # Lock 20% of profits as untouchable reserves


@dataclass
class ExecutionConfig:
    default_order_type: str = "GTC"
    max_slippage_pct: float = 2.0
    max_retries: int = 3
    retry_delay_seconds: int = 5


@dataclass
class AlertsConfig:
    discord_enabled: bool = True
    discord_webhook_url: str = ""
    on_trade: bool = True
    on_news_event: bool = True
    on_risk_warning: bool = True
    daily_summary: bool = True
    daily_summary_hour: int = 20


@dataclass
class StrategyArbitrageConfig:
    enabled: bool = True
    min_profit_pct: float = 0.3


@dataclass
class StrategyNewsDrivenConfig:
    enabled: bool = True
    min_sentiment_confidence: float = 0.7
    min_sources: int = 2
    min_edge_pct: float = 5.0


@dataclass
class StrategyHighProbConfig:
    enabled: bool = True
    min_price_cents: int = 92
    max_price_cents: int = 98


@dataclass
class StrategyContrarianConfig:
    enabled: bool = True
    spike_threshold_pct: float = 10.0
    mean_reversion_target_pct: float = 5.0


@dataclass
class StrategiesConfig:
    arbitrage: StrategyArbitrageConfig = field(default_factory=StrategyArbitrageConfig)
    news_driven: StrategyNewsDrivenConfig = field(default_factory=StrategyNewsDrivenConfig)
    high_probability: StrategyHighProbConfig = field(default_factory=StrategyHighProbConfig)
    contrarian: StrategyContrarianConfig = field(default_factory=StrategyContrarianConfig)


@dataclass
class BotConfig:
    cycle_interval_seconds: int = 45       # 45s cycles for high-frequency (avoids 429 rate limits)
    paper_trading: bool = True
    starting_budget: float = 10.0
    polymarket: PolymarketConfig = field(default_factory=PolymarketConfig)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    news: NewsConfig = field(default_factory=NewsConfig)
    sentiment: SentimentConfig = field(default_factory=SentimentConfig)
    prediction: PredictionConfig = field(default_factory=PredictionConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    strategies: StrategiesConfig = field(default_factory=StrategiesConfig)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _apply_dict(dc, data: dict):
    """Recursively apply a dict onto a dataclass instance."""
    for key, val in data.items():
        if not hasattr(dc, key):
            continue
        current = getattr(dc, key)
        if isinstance(val, dict) and hasattr(current, "__dataclass_fields__"):
            _apply_dict(current, val)
        else:
            setattr(dc, key, val)


def load_config() -> BotConfig:
    """Load full bot configuration from .env + config.yaml."""
    _load_env()
    yaml_data = _load_yaml()

    cfg = BotConfig()
    _apply_dict(cfg, yaml_data)

    # Override from env
    cfg.polymarket.private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "")
    cfg.polymarket.api_key = os.getenv("POLYMARKET_API_KEY", "")
    cfg.polymarket.api_secret = os.getenv("POLYMARKET_API_SECRET", "")
    cfg.polymarket.api_passphrase = os.getenv("POLYMARKET_API_PASSPHRASE", "")
    cfg.polymarket.relayer_api_key = os.getenv("RELAYER_API_KEY", "")
    cfg.polymarket.relayer_api_key_address = os.getenv("RELAYER_API_KEY_ADDRESS", "")
    cfg.news.newsapi_key = os.getenv("NEWSAPI_KEY", "")
    cfg.alerts.discord_webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "")
    cfg.paper_trading = os.getenv("PAPER_TRADING", "true").lower() == "true"
    cfg.starting_budget = float(os.getenv("STARTING_BUDGET", "10.0"))
    cfg.sentiment.model = os.getenv("SENTIMENT_MODEL", cfg.sentiment.model)

    return cfg
