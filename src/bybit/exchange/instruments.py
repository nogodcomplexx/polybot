"""
Bybit Instrument Repository & Specifications Cache.
Caches and validates lot size, tick size, min/max quantities, and precisions.
"""

from typing import Dict, Any, Optional
import math


class BybitInstrumentRepository:
    """Dynamic repository of Bybit linear perpetual contract specifications."""

    def __init__(self):
        self._specs: Dict[str, Dict[str, Any]] = {}

    def update_spec(self, symbol: str, spec: Dict[str, Any]):
        """Cache contract specification for symbol."""
        symbol = symbol.upper()
        self._specs[symbol] = {
            "tick_size": float(spec.get("tick_size", 0.01) or 0.01),
            "qty_step": float(spec.get("qty_step", 0.001) or 0.001),
            "min_order_qty": float(spec.get("min_order_qty", 0.001) or 0.001),
            "max_order_qty": float(spec.get("max_order_qty", 1000.0) or 1000.0),
            "min_price": float(spec.get("min_price", 0.01) or 0.01),
            "max_price": float(spec.get("max_price", 1000000.0) or 1000000.0),
            "max_leverage": float(spec.get("max_leverage", 100.0) or 100.0),
        }

    def get_spec(self, symbol: str) -> Dict[str, float]:
        symbol = symbol.upper()
        return self._specs.get(symbol, {
            "tick_size": 0.01,
            "qty_step": 0.001,
            "min_order_qty": 0.001,
            "max_order_qty": 1000.0,
            "min_price": 0.01,
            "max_price": 1000000.0,
            "max_leverage": 100.0,
        })

    def round_qty(self, symbol: str, qty: float) -> float:
        """Round quantity down to step size so risk is never increased."""
        spec = self.get_spec(symbol)
        step = spec["qty_step"]
        min_qty = spec["min_order_qty"]

        if qty < min_qty:
            return 0.0

        step_str = f"{step:.8f}".rstrip("0")
        decimals = len(step_str.split(".")[1]) if "." in step_str else 0

        # Floor division to never round upward beyond authorized risk budget
        units = math.floor(qty / step)
        rounded = round(units * step, decimals)
        return rounded

    def round_price(self, symbol: str, price: float) -> float:
        """Round price to nearest valid tick size."""
        spec = self.get_spec(symbol)
        tick = spec["tick_size"]
        tick_str = f"{tick:.8f}".rstrip("0")
        decimals = len(tick_str.split(".")[1]) if "." in tick_str else 0
        return round(round(price / tick) * tick, decimals)

    def validate_order(self, symbol: str, qty: float, price: Optional[float] = None) -> (bool, str):
        """Validate order against exchange contract rules."""
        spec = self.get_spec(symbol)
        if qty < spec["min_order_qty"]:
            return False, f"Quantity {qty} < min allowed {spec['min_order_qty']}"
        if qty > spec["max_order_qty"]:
            return False, f"Quantity {qty} > max allowed {spec['max_order_qty']}"
        if price is not None:
            if price < spec["min_price"]:
                return False, f"Price {price} < min allowed {spec['min_price']}"
            if price > spec["max_price"]:
                return False, f"Price {price} > max allowed {spec['max_price']}"
        return True, ""
