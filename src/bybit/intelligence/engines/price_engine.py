"""
Cross-Exchange Price & Basis Engine (Section 21).
Calculates Bybit vs Binance basis, percentage difference, basis velocity,
and classifies cross-venue lead/lag synchronization.
"""

import time
from typing import Dict, Any, List, Optional, Tuple
from src.bybit.intelligence.models import PriceSyncState

MAX_HISTORY = 100


class CrossExchangePriceEngine:
    """Tracks and classifies cross-exchange price dynamics between Bybit and Binance."""

    def __init__(self):
        self.basis_history: Dict[str, List[Tuple[float, float]]] = {}  # symbol -> [(ts, basis)]
        self.price_history: Dict[str, Dict[str, List[Tuple[float, float]]]] = {}  # symbol -> venue -> [(ts, price)]

    def record_prices(self, symbol: str, bybit_price: float, binance_price: float) -> Dict[str, Any]:
        """
        Record instantaneous prices, calculate basis and velocity, and classify synchronization.
        """
        symbol = symbol.upper()
        now = time.time()

        if bybit_price <= 0 or binance_price <= 0:
            return {
                "basis": 0.0,
                "basis_pct": 0.0,
                "basis_velocity": 0.0,
                "price_sync": PriceSyncState.SYNCED,
            }

        basis = bybit_price - binance_price
        basis_pct = (basis / binance_price) * 100.0

        # Maintain rolling basis_pct history
        history = self.basis_history.setdefault(symbol, [])
        history.append((now, basis_pct))
        if len(history) > MAX_HISTORY:
            self.basis_history[symbol] = history[-MAX_HISTORY:]

        # Calculate Basis Velocity (% per sec) over last 10 seconds
        recent_10s = [b for ts, b in history if now - ts <= 10.0]
        if len(recent_10s) >= 2:
            dt = max(1.0, history[-1][0] - history[0][0])
            basis_velocity = (recent_10s[-1] - recent_10s[0]) / dt
        else:
            basis_velocity = 0.0

        # Classify Sync State
        price_sync = self._classify_sync(basis_pct, basis_velocity)

        return {
            "basis": round(basis, 4),
            "basis_pct": round(basis_pct, 4),
            "basis_velocity": round(basis_velocity, 4),
            "price_sync": price_sync,
        }

    def _classify_sync(self, basis_pct: float, basis_velocity: float) -> PriceSyncState:
        """Classify cross-exchange price relationship."""
        abs_pct = abs(basis_pct)
        abs_vel = abs(basis_velocity)

        if abs_pct > 0.30:
            return PriceSyncState.DIVERGING
        if abs_vel > 0.15:
            return PriceSyncState.UNSTABLE
        if abs_pct <= 0.04 and abs_vel <= 0.02:
            return PriceSyncState.SYNCED
        if basis_pct > 0.04 and basis_velocity >= -0.05:
            return PriceSyncState.BYBIT_LEADING
        if basis_pct < -0.04 and basis_velocity <= 0.05:
            return PriceSyncState.BINANCE_LEADING

        return PriceSyncState.SYNCED

