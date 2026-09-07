"""
Cross-Exchange Orderflow Engine (Section 22).
Compares Cumulative Volume Delta (CVD) and trade velocities across Bybit and Binance
to classify order flow confirmation and detect venue-led divergence.
"""

from typing import Dict, Any, Optional
from src.bybit.intelligence.models import OrderflowConfirmation


class CrossExchangeOrderflowEngine:
    """Evaluates cross-exchange order flow agreement between Bybit and Binance."""

    def evaluate_orderflow(
        self,
        bybit_cvd: float,
        binance_cvd: float,
        bybit_velocity: float,
        binance_velocity: float,
    ) -> Dict[str, Any]:
        """
        Compare Bybit and Binance CVD and trade velocity to classify flow confirmation.
        """
        if abs(bybit_cvd) < 1.0 and abs(binance_cvd) < 1.0:
            return {
                "orderflow_confirmation": OrderflowConfirmation.UNCERTAIN,
                "cvd_ratio": 1.0,
                "velocity_ratio": 1.0,
            }

        # Flow directional agreement
        both_bullish = bybit_cvd > 0 and binance_cvd > 0
        both_bearish = bybit_cvd < 0 and binance_cvd < 0
        opposing = (bybit_cvd > 0 and binance_cvd < 0) or (bybit_cvd < 0 and binance_cvd > 0)

        # Velocity and magnitude checks
        velocity_ratio = (bybit_velocity / binance_velocity) if binance_velocity > 0 else 1.0

        if abs(bybit_cvd) > abs(binance_cvd) * 3.0 and bybit_velocity > 5.0:
            confirmation = OrderflowConfirmation.BYBIT_LOCAL
        elif abs(binance_cvd) > abs(bybit_cvd) * 3.0 and binance_velocity > 5.0:
            confirmation = OrderflowConfirmation.BINANCE_LEADING
        elif both_bullish:
            confirmation = OrderflowConfirmation.CONFIRMED_BULLISH
        elif both_bearish:
            confirmation = OrderflowConfirmation.CONFIRMED_BEARISH
        elif opposing:
            confirmation = OrderflowConfirmation.MIXED
        else:
            confirmation = OrderflowConfirmation.UNCERTAIN


        return {
            "orderflow_confirmation": confirmation,
            "both_bullish": both_bullish,
            "both_bearish": both_bearish,
            "velocity_ratio": round(velocity_ratio, 2),
        }
