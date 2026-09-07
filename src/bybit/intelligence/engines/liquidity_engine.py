"""
Cross-Exchange Liquidity Engine & Spoof Confirmation (Sections 23 & 41).
Compares depth and imbalance across venues to identify broad vs local liquidity support
and cross-validate orderbook wall authenticity.
"""

from typing import Dict, Any, Optional
from src.bybit.intelligence.models import LiquidityClassification


class CrossExchangeLiquidityEngine:
    """Evaluates multi-venue orderbook depth, imbalance, and spoof confirmation."""

    def evaluate_liquidity(
        self,
        bybit_obi: float,
        binance_obi: float,
        bybit_bid_depth: float,
        bybit_ask_depth: float,
        binance_bid_depth: float,
        binance_ask_depth: float,
        bybit_has_wall: bool = False,
        binance_has_wall: bool = False,
    ) -> Dict[str, Any]:
        """
        Classify multi-venue orderbook liquidity structure and cross-validate spoof walls.
        """
        # 1. Imbalance alignment
        both_bid_skew = bybit_obi >= 1.25 and binance_obi >= 1.25
        both_ask_skew = bybit_obi <= 0.80 and binance_obi <= 0.80
        opposing = (bybit_obi >= 1.20 and binance_obi <= 0.80) or (bybit_obi <= 0.80 and binance_obi >= 1.20)

        if both_bid_skew:
            classification = LiquidityClassification.BROAD_SUPPORT
        elif both_ask_skew:
            classification = LiquidityClassification.BROAD_RESISTANCE
        elif opposing:
            classification = LiquidityClassification.LIQUIDITY_CONFLICT
        elif bybit_obi >= 1.30 and binance_obi < 1.10:
            classification = LiquidityClassification.LOCAL_BYBIT_SUPPORT
        elif bybit_obi <= 0.75 and binance_obi > 0.90:
            classification = LiquidityClassification.LOCAL_BYBIT_RESISTANCE
        else:
            classification = LiquidityClassification.UNKNOWN

        # 2. Section 41 Spoof Confirmation
        # If wall only exists on Bybit -> lower confirmation (higher spoof risk)
        # If wall exists on both venues -> confirmed genuine institutional interest
        if bybit_has_wall and binance_has_wall:
            spoof_confirmed_score = 0.90   # High cross-venue confirmation
        elif bybit_has_wall and not binance_has_wall:
            spoof_confirmed_score = 0.35   # Low confirmation, likely local spoof
        else:
            spoof_confirmed_score = 0.70   # Neutral/no distinct wall

        return {
            "liquidity_classification": classification,
            "both_bid_skew": both_bid_skew,
            "both_ask_skew": both_ask_skew,
            "spoof_confirmation_score": spoof_confirmed_score,
        }
