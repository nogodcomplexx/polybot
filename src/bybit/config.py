"""
Configuration Layer for Bybit Scalper.
Enforces non-negotiable hard safety constraints and bounded tunable parameters.
"""

import os
from dataclasses import dataclass, field
from typing import List, Dict, Any
from pathlib import Path
import yaml
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class HardSafetyLimits:
    """Non-negotiable safety invariants that can NEVER be dynamically modified."""
    MAX_ACTIVE_POSITIONS: int = 1
    HEDGE_MODE: bool = False
    MAX_RISK_PER_TRADE_PCT: float = 7.5       # Absolute emergency ceiling
    PRODUCTION_RISK_CEILING_PCT: float = 2.0   # Conservative production ceiling
    DEFAULT_RISK_PER_TRADE_PCT: float = 1.0   # Default normal risk per trade
    MIN_CASH_FORTRESS_PCT: float = 85.0       # Unencumbered equity floor
    MIN_TP1_RR: float = 1.5                   # Minimum Reward:Risk for TP1
    MIN_TP2_RR: float = 2.8                   # Minimum Reward:Risk for TP2
    MIN_STOP_DIST_PCT: float = 0.10           # Minimum stop distance (0.10%)
    MAX_STOP_DIST_PCT: float = 2.50           # Maximum stop distance (2.50%)
    MIN_ALPHA_SCORE: float = 70.0             # Alpha score gate (0-100, 70-74: reduced risk, 75-84: normal, 85+: high confidence)
    CONSECUTIVE_LOSS_LIMIT: int = 2           # Consecutive losses triggering cooldown
    COOLDOWN_HOURS: float = 4.0               # Mandatory cooldown duration
    DRAWDOWN_KILL_PCT: float = 5.0            # Daily DD threshold triggering global halt


@dataclass
class ParameterBound:
    """Parameter bound with MIN, DEFAULT, MAX constraints."""
    name: str
    min_val: float
    default_val: float
    max_val: float
    current_val: float

    def clamp(self, value: float) -> float:
        """Clamp proposed value to authorized bounds."""
        return max(self.min_val, min(self.max_val, value))


class TunableParameterRegistry:
    """Registry of self-tunable parameters with strict bounds clamping."""
    def __init__(self):
        self.bounds: Dict[str, ParameterBound] = {
            "min_alpha_score": ParameterBound("min_alpha_score", 60.0, 70.0, 90.0, 70.0),
            "ob_imbalance_bullish": ParameterBound("ob_imbalance_bullish", 1.10, 1.30, 2.00, 1.30),
            "ob_imbalance_bearish": ParameterBound("ob_imbalance_bearish", 0.50, 0.75, 0.90, 0.75),
            "atr_stop_multiplier": ParameterBound("atr_stop_multiplier", 1.00, 1.50, 3.00, 1.50),
            "min_expected_ev": ParameterBound("min_expected_ev", 0.0010, 0.0025, 0.0100, 0.0025),
            "min_execution_score": ParameterBound("min_execution_score", 50.0, 65.0, 85.0, 65.0),
            "spoof_max_prob": ParameterBound("spoof_max_prob", 0.40, 0.65, 0.85, 0.65),
            "entry_ttl_sec": ParameterBound("entry_ttl_sec", 15.0, 45.0, 180.0, 45.0),
            "max_spread_pct": ParameterBound("max_spread_pct", 0.02, 0.08, 0.20, 0.08),
        }

    def get(self, name: str) -> float:
        param = self.bounds.get(name)
        if param is None:
            raise ValueError(f"Unknown tunable parameter: '{name}'")
        return param.current_val

    def update(self, name: str, new_val: float) -> float:
        """Safely update a parameter within bounds."""
        if name in self.bounds:
            clamped = self.bounds[name].clamp(new_val)
            self.bounds[name].current_val = clamped
            return clamped
        raise ValueError(f"Unknown tunable parameter: {name}")


@dataclass
class UniverseConfig:
    """Dynamic Universe Discovery & Rotation Configuration."""
    mode: str = "dynamic"                      # "dynamic" or "static"
    top_n: int = 20                            # Number of symbols to actively track
    min_turnover_usd: float = 50_000_000.0     # $50M 24h turnover floor
    max_spread_pct: float = 0.04               # 0.04% max spread
    min_volatility_pct: float = 2.5            # 2.5% minimum 24h price range
    rotation_interval_hours: float = 2.0       # Re-scan universe every 2 hours
    pinned_symbols: List[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])


@dataclass
class BinanceConfig:
    """Binance Futures Cross-Exchange Intelligence Configuration."""
    enabled: bool = True
    ws_url: str = "wss://fstream.binance.com/stream"
    rest_url: str = "https://fapi.binance.com"
    rate_limit_per_sec: float = 20.0
    follow_universe: bool = True
    symbols: List[str] = field(default_factory=list)



@dataclass
class CoinGlassConfig:
    """CoinGlass Contextual Derivatives Configuration."""
    enabled: bool = True
    api_key: str = ""
    poll_interval_sec: float = 60.0
    base_url: str = "https://open-api.coinglass.com/public/v2"
    rate_limit_per_sec: float = 2.0


@dataclass
class DashboardConfig:
    """Web Telemetry Dashboard & Observatory Configuration."""
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8080
    update_interval_ms: int = 250


@dataclass
class BybitScalperConfig:
    """Master Scalper Configuration."""
    mode: str = "paper"                        # "paper", "testnet", "shadow", "live"
    api_key: str = ""
    api_secret: str = ""
    testnet: bool = True
    symbols: List[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT"])
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    binance: BinanceConfig = field(default_factory=BinanceConfig)
    coinglass: CoinGlassConfig = field(default_factory=CoinGlassConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    margin_mode: str = "ISOLATED"              # ISOLATED or CROSS
    default_leverage: int = 10
    max_position_usd: float = 100.0
    starting_equity: float = 1000.0
    post_only: bool = True
    tp1_ratio: float = 0.50                    # 50% position on TP1
    tp2_ratio: float = 0.50                    # 50% position on TP2
    breakeven_buffer_pct: float = 0.0005       # +0.05% buffer above entry
    max_protection_latency_sec: float = 2.5
    rate_limit_requests_per_sec: int = 10
    db_path: str = "polybot.db"
    safety: HardSafetyLimits = field(default_factory=HardSafetyLimits)
    tunable: TunableParameterRegistry = field(default_factory=TunableParameterRegistry)

    def validate(self):
        """Validate startup invariants."""
        if self.safety.MAX_ACTIVE_POSITIONS != 1:
            raise ValueError("HARD INVARIANT VIOLATION: MAX_ACTIVE_POSITIONS must be 1")
        if self.safety.MIN_CASH_FORTRESS_PCT < 85.0:
            raise ValueError("HARD INVARIANT VIOLATION: MIN_CASH_FORTRESS_PCT must be at least 85%")
        if self.safety.MAX_RISK_PER_TRADE_PCT > 7.5:
            raise ValueError("HARD INVARIANT VIOLATION: MAX_RISK_PER_TRADE_PCT cannot exceed 7.5%")
        if self.safety.DEFAULT_RISK_PER_TRADE_PCT > self.safety.MAX_RISK_PER_TRADE_PCT:
            raise ValueError("DEFAULT_RISK_PER_TRADE_PCT cannot exceed MAX_RISK_PER_TRADE_PCT")
        if self.mode == "live" and (not self.api_key or not self.api_secret):
            raise ValueError("API credentials required for LIVE mode")


def load_bybit_config() -> BybitScalperConfig:
    """Load config from .env and config.yaml with environment overrides."""
    env_path = _PROJECT_ROOT / ".env"
    load_dotenv(env_path)

    cfg = BybitScalperConfig()

    # Load YAML if present
    yaml_path = _PROJECT_ROOT / "config.yaml"
    if yaml_path.exists():
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                b_data = data.get("bybit", {})
                if "symbols" in b_data and b_data["symbols"]:
                    cfg.symbols = [s.upper() for s in b_data["symbols"]]
                if "default_leverage" in b_data:
                    cfg.default_leverage = int(b_data["default_leverage"])
                if "margin_mode" in b_data:
                    cfg.margin_mode = b_data["margin_mode"].upper()
                if "max_position_usd" in b_data:
                    cfg.max_position_usd = float(b_data["max_position_usd"])
                if "post_only" in b_data:
                    cfg.post_only = bool(b_data["post_only"])

                # Universe settings
                u_data = b_data.get("universe", {})
                if "mode" in u_data:
                    cfg.universe.mode = str(u_data["mode"]).lower()
                if "top_n" in u_data:
                    cfg.universe.top_n = int(u_data["top_n"])
                if "min_turnover_usd" in u_data:
                    cfg.universe.min_turnover_usd = float(u_data["min_turnover_usd"])
                if "max_spread_pct" in u_data:
                    cfg.universe.max_spread_pct = float(u_data["max_spread_pct"])
                if "min_volatility_pct" in u_data:
                    cfg.universe.min_volatility_pct = float(u_data["min_volatility_pct"])
                if "rotation_interval_hours" in u_data:
                    cfg.universe.rotation_interval_hours = float(u_data["rotation_interval_hours"])
                if "pinned_symbols" in u_data:
                    cfg.universe.pinned_symbols = [s.upper() for s in u_data["pinned_symbols"]]

                # Binance settings
                bin_data = data.get("binance", {})
                if "enabled" in bin_data:
                    cfg.binance.enabled = bool(bin_data["enabled"])
                if "follow_universe" in bin_data:
                    cfg.binance.follow_universe = bool(bin_data["follow_universe"])
                if "symbols" in bin_data:
                    cfg.binance.symbols = [s.upper() for s in bin_data["symbols"]]


                # CoinGlass settings
                cg_data = data.get("coinglass", {})
                if "enabled" in cg_data:
                    cfg.coinglass.enabled = bool(cg_data["enabled"])
                if "api_key" in cg_data:
                    cfg.coinglass.api_key = str(cg_data["api_key"])
                if "poll_interval_sec" in cg_data:
                    cfg.coinglass.poll_interval_sec = float(cg_data["poll_interval_sec"])
        except Exception:
            pass

    # Env overrides
    cfg.api_key = os.getenv("BYBIT_API_KEY", cfg.api_key)
    cfg.api_secret = os.getenv("BYBIT_API_SECRET", cfg.api_secret)
    cfg.testnet = os.getenv("BYBIT_TESTNET", "true").lower() in ("true", "1", "yes")

    # Universe env overrides
    if os.getenv("BYBIT_UNIVERSE_MODE"):
        cfg.universe.mode = os.getenv("BYBIT_UNIVERSE_MODE", "").lower()
    if os.getenv("BYBIT_UNIVERSE_TOP_N"):
        cfg.universe.top_n = int(os.getenv("BYBIT_UNIVERSE_TOP_N", "20"))

    # Binance & CoinGlass env overrides
    if os.getenv("BINANCE_ENABLED"):
        cfg.binance.enabled = os.getenv("BINANCE_ENABLED", "true").lower() in ("true", "1", "yes")
    cfg.coinglass.api_key = os.getenv("COINGLASS_API_KEY", cfg.coinglass.api_key)
    if os.getenv("COINGLASS_ENABLED"):
        cfg.coinglass.enabled = os.getenv("COINGLASS_ENABLED", "true").lower() in ("true", "1", "yes")


    # Determine mode
    env_mode = os.getenv("BYBIT_MODE", "").lower()
    if env_mode in ("paper", "testnet", "shadow", "live"):
        cfg.mode = env_mode
    else:
        if not cfg.api_key or not cfg.api_secret:
            cfg.mode = "paper"
        elif cfg.testnet:
            cfg.mode = "testnet"
        else:
            cfg.mode = "live"

    cfg.validate()
    return cfg
