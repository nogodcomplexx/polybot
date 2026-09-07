"""
Market State Aggregator.
Consolidates real-time feeds, candles, indicators, order flow, OI, funding,
and microstructure into an immutable MarketSnapshot.
"""

from typing import Dict, Any, List, Optional
import time

from src.bybit.models import MarketSnapshot, MarketRegime
from src.bybit.exchange.public_ws import BybitPublicWS
from src.bybit.market.candles import MultiTimeframeCandleManager
from src.bybit.market.volatility import calculate_atr
from src.bybit.market.vwap import calculate_vwap
from src.bybit.market.microstructure import MicrostructureEngine
from src.bybit.market.spoof import SpoofDetector


class MarketStateEngine:
    """Aggregates multi-source feeds into unified MarketSnapshots."""

    def __init__(
        self,
        symbols: List[str],
        ws: BybitPublicWS,
        candle_mgr: MultiTimeframeCandleManager,
        micro_engine: MicrostructureEngine,
        spoof_detector: SpoofDetector,
    ):
        self.symbols = [s.upper() for s in symbols]
        self.ws = ws
        self.candle_mgr = candle_mgr
        self.micro_engine = micro_engine
        self.spoof_detector = spoof_detector

    def update_symbols(self, symbols: List[str]):
        """Dynamically update active symbol list."""
        self.symbols = [s.upper() for s in symbols]

    def get_snapshot(self, symbol: str, regime: MarketRegime = MarketRegime.UNCERTAIN) -> MarketSnapshot:
        """Produce an immutable, synchronized MarketSnapshot for symbol."""
        symbol = symbol.upper()
        now = time.time()

        book_summary = self.ws.get_orderbook_summary(symbol)
        price = self.ws.latest_prices.get(symbol, book_summary["mid_price"])
        mark = self.ws.mark_prices.get(symbol, price)
        index = self.ws.index_prices.get(symbol, price)

        c1m = self.candle_mgr.get_candles(symbol, "1m")
        c5m = self.candle_mgr.get_candles(symbol, "5m")
        c15m = self.candle_mgr.get_candles(symbol, "15m")
        c24h = self.candle_mgr.get_candles(symbol, "24h")

        # Technical Indicators
        atr_1m = calculate_atr(c1m, 14)
        atr_5m = calculate_atr(c5m, 14)
        atr_15m = calculate_atr(c15m, 14)

        vwap_1m = calculate_vwap(c1m[-20:] if len(c1m) >= 20 else c1m)
        vwap_5m = calculate_vwap(c5m[-20:] if len(c5m) >= 20 else c5m)
        vwap_15m = calculate_vwap(c15m[-20:] if len(c15m) >= 20 else c15m)
        vwap_24h = calculate_vwap(c24h) if c24h else vwap_15m

        # Microstructure & Spoofing
        micro_metrics = self.micro_engine.get_metrics(symbol, book_summary)
        raw_book = self.ws.orderbooks.get(symbol, {})
        spoof_prob = self.spoof_detector.evaluate_walls(symbol, raw_book, price)

        # OI & Funding
        oi = self.ws.open_interests.get(symbol, 0.0)
        funding = self.ws.funding_rates.get(symbol, 0.0)
        next_funding = self.ws.next_funding_times.get(symbol, 0.0)

        return MarketSnapshot(
            timestamp=now,
            symbol=symbol,
            last_price=price,
            mark_price=mark,
            index_price=index,
            spread=book_summary["spread"],
            spread_pct=book_summary["spread_pct"],
            best_bid=book_summary["best_bid"],
            best_ask=book_summary["best_ask"],
            bid_depth=book_summary["bid_depth"],
            ask_depth=book_summary["ask_depth"],
            orderbook_imbalance=book_summary["orderbook_imbalance"],
            microprice=book_summary["microprice"],
            cvd=micro_metrics["cvd"],
            trade_velocity=micro_metrics["trade_velocity"],
            volume_velocity=micro_metrics["volume_velocity"],
            open_interest=oi,
            funding=funding,
            next_funding_time=next_funding,
            vwap_1m=vwap_1m,
            vwap_5m=vwap_5m,
            vwap_15m=vwap_15m,
            vwap_24h=vwap_24h,
            atr_1m=atr_1m,
            atr_5m=atr_5m,
            atr_15m=atr_15m,
            regime=regime,
            spoof_probability=spoof_prob,
            orderbook_valid=book_summary.get("is_valid", False),
        )
