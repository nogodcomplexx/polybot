"""
Bybit Account & Margin Repository.
Tracks real-time account balances, margin utilization, and capital fortress ratios.
"""

from typing import Dict, Any, List, Optional
import time


class BybitAccountRepository:
    """Manages wallet balance, unencumbered equity, and position cache."""

    def __init__(self, starting_equity: float = 1000.0):
        self.starting_equity = starting_equity
        self.total_equity: float = starting_equity
        self.available_balance: float = starting_equity
        self.wallet_balance: float = starting_equity
        self.margin_balance: float = starting_equity
        self.unrealised_pnl: float = 0.0
        self.used_margin: float = 0.0
        self.last_update_time: float = time.time()

        # Cached live positions and open orders from Bybit
        self.cached_positions: Dict[str, Dict[str, Any]] = {}
        self.cached_orders: Dict[str, Dict[str, Any]] = {}

    def update_from_wallet_response(self, wallet_data: Dict[str, Any]):
        """Update balances from Bybit V5 wallet-balance API."""
        self.total_equity = float(wallet_data.get("equity", self.total_equity) or self.total_equity)
        self.available_balance = float(wallet_data.get("available", self.available_balance) or self.available_balance)
        self.wallet_balance = float(wallet_data.get("wallet_balance", self.wallet_balance) or self.wallet_balance)
        self.margin_balance = float(wallet_data.get("margin_balance", self.margin_balance) or self.margin_balance)
        self.unrealised_pnl = float(wallet_data.get("unrealised_pnl", 0.0) or 0.0)
        self.used_margin = max(0.0, self.total_equity - self.available_balance)
        self.last_update_time = time.time()

    @property
    def fortress_ratio(self) -> float:
        """Calculate unencumbered cash fortress ratio (0.0 to 1.0)."""
        if self.total_equity <= 0:
            return 0.0
        return self.available_balance / self.total_equity

    @property
    def fortress_percent(self) -> float:
        """Cash fortress as percentage (e.g. 92.5%)."""
        return self.fortress_ratio * 100.0

    def is_fortress_secure(self, min_ratio: float = 0.85) -> bool:
        """Verify unencumbered equity is >= 85%."""
        return self.fortress_ratio >= min_ratio

    def update_positions(self, positions_list: List[Dict[str, Any]]):
        """Update cached active positions."""
        self.cached_positions = {
            p["symbol"].upper(): p for p in positions_list if float(p.get("size", 0) or 0) > 0
        }

    def update_orders(self, orders_list: List[Dict[str, Any]]):
        """Update cached open orders."""
        self.cached_orders = {
            o.get("orderId", ""): o for o in orders_list
        }
