"""
Multi-Timeframe Candle Manager.
Maintains synchronized rolling windows of 1M, 5M, 15M, 1H, 4H, and 24H candles.
Aggregates ticks causally with strict time ordering.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Any
import time


@dataclass
class Candle:
    timestamp: float      # Start time of candle (seconds)
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: float
    quote_volume: float = 0.0
    trades: int = 1


class MultiTimeframeCandleManager:
    """Manages multi-timeframe candles per symbol."""

    TIMEFRAMES = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "1h": 3600,
        "4h": 14400,
        "24h": 86400,
    }

    def __init__(self, symbols: List[str], max_history: int = 200):
        self.symbols = [s.upper() for s in symbols]
        self.max_history = max_history
        self.candles: Dict[str, Dict[str, List[Candle]]] = {
            s: {tf: [] for tf in self.TIMEFRAMES} for s in self.symbols
        }

    def seed_candles(self, symbol: str, timeframe: str, raw_candles: List[Dict[str, Any]]):
        """Seed historical candles from REST API."""
        symbol = symbol.upper()
        if symbol not in self.candles or timeframe not in self.TIMEFRAMES:
            return

        interval_sec = self.TIMEFRAMES[timeframe]
        converted = [
            Candle(
                timestamp=k["start_time"],
                open=k["open"],
                high=k["high"],
                low=k["low"],
                close=k["close"],
                volume=k["volume"],
                close_time=k["start_time"] + interval_sec,
                quote_volume=k.get("turnover", k["volume"] * k["close"]),
                trades=1,
            )
            for k in raw_candles
        ]
        self.candles[symbol][timeframe] = converted[-self.max_history:]

    def process_trade(self, symbol: str, price: float, size: float, ts: float):
        """Aggregate trade tick into rolling multi-timeframe candles."""
        symbol = symbol.upper()
        if symbol not in self.candles:
            return

        for tf, interval_sec in self.TIMEFRAMES.items():
            current_bar_ts = int(ts // interval_sec) * interval_sec
            bar_list = self.candles[symbol][tf]

            if not bar_list or bar_list[-1].timestamp < current_bar_ts:
                # Open a new candle
                new_bar = Candle(
                    timestamp=current_bar_ts,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=size,
                    close_time=current_bar_ts + interval_sec,
                    quote_volume=size * price,
                    trades=1,
                )
                bar_list.append(new_bar)
                if len(bar_list) > self.max_history:
                    self.candles[symbol][tf] = bar_list[-self.max_history:]
            else:
                # Update current active open candle
                last = bar_list[-1]
                last.high = max(last.high, price)
                last.low = min(last.low, price)
                last.close = price
                last.volume += size
                last.quote_volume += size * price
                last.trades += 1

    def get_candles(self, symbol: str, timeframe: str) -> List[Candle]:
        return self.candles.get(symbol.upper(), {}).get(timeframe, [])

    def register_symbol(self, symbol: str):
        symbol = symbol.upper()
        if symbol not in self.symbols:
            self.symbols.append(symbol)
        if symbol not in self.candles:
            self.candles[symbol] = {tf: [] for tf in self.TIMEFRAMES}

    def unregister_symbol(self, symbol: str):
        symbol = symbol.upper()
        if symbol in self.symbols:
            self.symbols.remove(symbol)
        self.candles.pop(symbol, None)

