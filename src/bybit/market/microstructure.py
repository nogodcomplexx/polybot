"""
Microstructure & Order Flow Engine.
Calculates Order Book Imbalance (OBI), microprice, Cumulative Volume Delta (CVD),
trade velocity, and passive absorption detection.
"""

from typing import Dict, Any, List, Optional
import time


class MicrostructureEngine:
    """Tracks real-time order flow and orderbook microstructure."""

    def __init__(self, symbols: List[str]):
        self.symbols = [s.upper() for s in symbols]
        self.cvd_window: Dict[str, List[float]] = {s: [] for s in self.symbols}
        self.recent_trades: Dict[str, List[Dict[str, Any]]] = {s: [] for s in self.symbols}
        self.absorption_events: Dict[str, List[Dict[str, Any]]] = {s: [] for s in self.symbols}

    def process_trade(self, symbol: str, price: float, size: float, side: str, ts: float):
        """Update CVD and track aggressive trades."""
        symbol = symbol.upper()
        if symbol not in self.cvd_window:
            self.cvd_window[symbol] = []
            self.recent_trades[symbol] = []

        delta = size if side.capitalize() == "Buy" else -size
        window = self.cvd_window[symbol]
        window.append(delta)
        if len(window) > 300:
            self.cvd_window[symbol] = window[-300:]

        trades = self.recent_trades[symbol]
        trades.append({"price": price, "size": size, "side": side.capitalize(), "time": ts})
        if len(trades) > 300:
            self.recent_trades[symbol] = trades[-300:]

    def get_metrics(self, symbol: str, book_summary: Dict[str, Any]) -> Dict[str, Any]:
        """Compute complete microstructure metrics for symbol."""
        symbol = symbol.upper()
        trades = self.recent_trades.get(symbol, [])
        now = time.time()

        # Trades within last 10 seconds
        recent_window = [t for t in trades if now - t["time"] <= 10.0]
        trade_velocity = len(recent_window) / 10.0
        volume_velocity = sum(t["size"] for t in recent_window) / 10.0

        aggressive_buy_vol = sum(t["size"] for t in recent_window if t["side"] == "Buy")
        aggressive_sell_vol = sum(t["size"] for t in recent_window if t["side"] == "Sell")
        total_vol = aggressive_buy_vol + aggressive_sell_vol
        buy_ratio = (aggressive_buy_vol / total_vol) if total_vol > 0 else 0.5

        # Absorption check: heavy market selling/buying that fails to move price
        absorption_bullish = False
        absorption_bearish = False
        if len(recent_window) >= 10:
            prices = [t["price"] for t in recent_window]
            price_range_pct = ((max(prices) - min(prices)) / prices[0]) * 100 if prices[0] > 0 else 0.0

            # Heavy selling (>65% sells) but price barely moved (<0.08%) + strong bid depth
            if (aggressive_sell_vol > aggressive_buy_vol * 1.8 and price_range_pct < 0.08 and
                    book_summary.get("orderbook_imbalance", 1.0) >= 1.10):
                absorption_bullish = True

            # Heavy buying (>65% buys) but price barely moved (<0.08%) + strong ask depth
            if (aggressive_buy_vol > aggressive_sell_vol * 1.8 and price_range_pct < 0.08 and
                    book_summary.get("orderbook_imbalance", 1.0) <= 0.90):
                absorption_bearish = True

        return {
            "cvd": sum(self.cvd_window.get(symbol, [])),
            "trade_velocity": round(trade_velocity, 2),
            "volume_velocity": round(volume_velocity, 2),
            "aggressive_buy_vol": round(aggressive_buy_vol, 2),
            "aggressive_sell_vol": round(aggressive_sell_vol, 2),
            "buy_ratio": round(buy_ratio, 3),
            "absorption_bullish": absorption_bullish,
            "absorption_bearish": absorption_bearish,
        }

    def register_symbol(self, symbol: str):
        symbol = symbol.upper()
        if symbol not in self.symbols:
            self.symbols.append(symbol)
        if symbol not in self.cvd_window:
            self.cvd_window[symbol] = []
            self.recent_trades[symbol] = []
            self.absorption_events[symbol] = []

    def unregister_symbol(self, symbol: str):
        symbol = symbol.upper()
        if symbol in self.symbols:
            self.symbols.remove(symbol)
        self.cvd_window.pop(symbol, None)
        self.recent_trades.pop(symbol, None)
        self.absorption_events.pop(symbol, None)

