"""
Bybit V5 REST API Client.
Provides authenticated REST operations for Linear Perpetuals with signature verification,
rate limit protection, clock synchronization, and trading stop management.
"""

import hmac
import hashlib
import json
import logging
import time
from typing import Dict, Any, List, Optional

import httpx

from src.bybit.utils.api_logger import log_api_call

logger = logging.getLogger(__name__)

BYBIT_MAINNET_URL = "https://api.bybit.com"
BYBIT_TESTNET_URL = "https://api-testnet.bybit.com"
BYBIT_DEMO_URL = "https://api-demo.bybit.com"


class BybitClient:
    """Production async client for Bybit V5 Unified Trading API."""

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = True,
        demo: bool = False,
        recv_window: int = 5000,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.demo = demo
        self.recv_window = recv_window
        if demo:
            self.base_url = BYBIT_DEMO_URL
        elif testnet:
            self.base_url = BYBIT_TESTNET_URL
        else:
            self.base_url = BYBIT_MAINNET_URL
        self._client: Optional[httpx.AsyncClient] = None
        self._server_time_offset_ms: float = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=8.0,
                headers={"Accept": "application/json"},
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _generate_signature(self, timestamp: str, payload_str: str) -> str:
        param_str = f"{timestamp}{self.api_key}{self.recv_window}{payload_str}"
        return hmac.new(
            self.api_secret.encode("utf-8"),
            param_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        auth_required: bool = True,
    ) -> Dict[str, Any]:
        client = await self._get_client()
        start = time.time()
        method = method.upper()
        headers: Dict[str, str] = {}
        payload_str = ""

        if method == "GET":
            if params:
                clean_params = {k: v for k, v in params.items() if v is not None}
                payload_str = "&".join(f"{k}={v}" for k, v in sorted(clean_params.items()))
            else:
                payload_str = ""
        else:
            payload_str = json.dumps(data, separators=(',', ':')) if data else ""
            headers["Content-Type"] = "application/json"

        if auth_required:
            if not self.api_key or not self.api_secret:
                raise ValueError("Bybit API key and secret are required for authenticated requests")

            timestamp = str(int((time.time() * 1000) + self._server_time_offset_ms))
            signature = self._generate_signature(timestamp, payload_str)
            headers.update({
                "X-BAPI-API-KEY": self.api_key,
                "X-BAPI-TIMESTAMP": timestamp,
                "X-BAPI-SIGN": signature,
                "X-BAPI-RECV-WINDOW": str(self.recv_window),
            })

        content_bytes = payload_str.encode("utf-8") if payload_str and method != "GET" else None

        try:
            if method == "GET":
                request_url = f"{path}?{payload_str}" if payload_str else path
                resp = await client.get(request_url, headers=headers)
            elif method == "POST":
                resp = await client.post(path, content=content_bytes, headers=headers)
            elif method == "DELETE":
                resp = await client.delete(path, content=content_bytes, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            duration_ms = (time.time() - start) * 1000
            resp_data = resp.json()
            ret_code = resp_data.get("retCode", 0)

            if ret_code != 0:
                err_msg = resp_data.get("retMsg", "Unknown error")
                log_api_call("Bybit", f"{method} {path}", False, duration_ms, f"code={ret_code}: {err_msg}")
                logger.warning(f"Bybit API error [{ret_code}]: {err_msg} ({path})")
                return resp_data

            log_api_call("Bybit", f"{method} {path}", True, duration_ms)
            return resp_data

        except Exception as e:
            duration_ms = (time.time() - start) * 1000
            log_api_call("Bybit", f"{method} {path}", False, duration_ms, str(e))
            logger.error(f"Bybit request failed ({method} {path}): {e}")
            raise

    # ------------------------------------------------------------------
    # Clock Sync & Server Time
    # ------------------------------------------------------------------

    async def sync_server_time(self) -> float:
        """Fetch Bybit server time and calculate clock offset."""
        res = await self._request("GET", "/v5/market/time", auth_required=False)
        if res.get("retCode") == 0:
            server_ts = int(res.get("time", 0) or 0)
            if server_ts == 0:
                time_second = res.get("result", {}).get("timeSecond", 0)
                if time_second:
                    server_ts = int(float(time_second) * 1000)
            if server_ts > 0:
                local_ts = int(time.time() * 1000)
                self._server_time_offset_ms = server_ts - local_ts
                logger.info(f"Bybit clock offset: {self._server_time_offset_ms:.1f}ms")
        return self._server_time_offset_ms

    # ------------------------------------------------------------------
    # Account & Wallet
    # ------------------------------------------------------------------

    async def get_wallet_balance(self, account_type: str = "UNIFIED", coin: str = "USDT") -> Dict[str, Any]:
        res = await self._request("GET", "/v5/account/wallet-balance", params={"accountType": account_type, "coin": coin})
        if res.get("retCode") == 0:
            result = res.get("result", {})
            list_data = result.get("list", [])
            if list_data:
                account = list_data[0]
                coins = account.get("coin", [])
                for c in coins:
                    if c.get("coin") == coin:
                        return {
                            "equity": float(c.get("equity", 0) or 0),
                            "available": float(c.get("availableToWithdraw", 0) or c.get("walletBalance", 0) or 0),
                            "wallet_balance": float(c.get("walletBalance", 0) or 0),
                            "unrealised_pnl": float(c.get("unrealisedPnl", 0) or 0),
                            "margin_balance": float(c.get("marginBalance", 0) or 0),
                        }
                return {
                    "equity": float(account.get("totalEquity", 0) or 0),
                    "available": float(account.get("totalAvailableBalance", 0) or 0),
                    "wallet_balance": float(account.get("totalWalletBalance", 0) or 0),
                    "unrealised_pnl": 0.0,
                    "margin_balance": float(account.get("totalMarginBalance", 0) or 0),
                }
        return {"equity": 0.0, "available": 0.0, "wallet_balance": 0.0, "unrealised_pnl": 0.0, "margin_balance": 0.0}

    # ------------------------------------------------------------------
    # Instruments & Market Rules
    # ------------------------------------------------------------------

    async def get_instruments_info(self, category: str = "linear", symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"category": category}
        if symbol:
            params["symbol"] = symbol
        res = await self._request("GET", "/v5/market/instruments-info", params=params, auth_required=False)
        return res.get("result", {}).get("list", []) if res.get("retCode") == 0 else []

    async def get_risk_limits(self, category: str = "linear", symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch risk limit tiers for symbols."""
        params: Dict[str, Any] = {"category": category}
        if symbol:
            params["symbol"] = symbol
        res = await self._request("GET", "/v5/market/risk-limit", params=params, auth_required=False)
        return res.get("result", {}).get("list", []) if res.get("retCode") == 0 else []

    async def get_tickers(self, category: str = "linear", symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"category": category}
        if symbol:
            params["symbol"] = symbol
        res = await self._request("GET", "/v5/market/tickers", params=params, auth_required=False)
        return res.get("result", {}).get("list", []) if res.get("retCode") == 0 else []

    async def get_klines(
        self,
        symbol: str,
        interval: str = "1",
        limit: int = 100,
        category: str = "linear",
    ) -> List[Dict[str, Any]]:
        params = {"category": category, "symbol": symbol, "interval": interval, "limit": limit}
        res = await self._request("GET", "/v5/market/kline", params=params, auth_required=False)
        if res.get("retCode") == 0:
            raw_list = res.get("result", {}).get("list", [])
            candles = []
            for item in reversed(raw_list):
                candles.append({
                    "start_time": int(item[0]) / 1000,
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                    "turnover": float(item[6]),
                })
            return candles
        return []

    # ------------------------------------------------------------------
    # Positions & Leverage
    # ------------------------------------------------------------------

    async def get_positions(self, category: str = "linear", symbol: Optional[str] = None, settle_coin: str = "USDT") -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"category": category}
        if symbol:
            params["symbol"] = symbol
        else:
            params["settleCoin"] = settle_coin

        res = await self._request("GET", "/v5/position/list", params=params)
        if res.get("retCode") == 0:
            positions = []
            for p in res.get("result", {}).get("list", []):
                size = float(p.get("size", 0) or 0)
                if size > 0:
                    positions.append({
                        "symbol": p.get("symbol", ""),
                        "side": p.get("side", ""),
                        "size": size,
                        "entry_price": float(p.get("avgPrice", 0) or 0),
                        "mark_price": float(p.get("markPrice", 0) or 0),
                        "liq_price": float(p.get("liqPrice", 0) or 0),
                        "leverage": float(p.get("leverage", 1) or 1),
                        "unrealised_pnl": float(p.get("unrealisedPnl", 0) or 0),
                        "position_value": float(p.get("positionValue", 0) or 0),
                        "take_profit": float(p.get("takeProfit", 0) or 0),
                        "stop_loss": float(p.get("stopLoss", 0) or 0),
                        "trailing_stop": float(p.get("trailingStop", 0) or 0),
                        "cur_realised_pnl": float(p.get("curRealisedPnl", 0) or 0),
                        "cum_realised_pnl": float(p.get("cumRealisedPnl", 0) or 0),
                    })
            return positions
        return []

    async def set_leverage(self, symbol: str, leverage: int, category: str = "linear") -> bool:
        data = {"category": category, "symbol": symbol, "buyLeverage": str(leverage), "sellLeverage": str(leverage)}
        res = await self._request("POST", "/v5/position/set-leverage", data=data)
        ret_code = res.get("retCode", 0)
        return ret_code in (0, 110043)

    async def set_margin_mode(self, symbol: str, is_isolated: bool = True, category: str = "linear", leverage: int = 10) -> bool:
        data = {
            "category": category, "symbol": symbol, "tradeMode": 1 if is_isolated else 0,
            "buyLeverage": str(leverage), "sellLeverage": str(leverage)
        }
        res = await self._request("POST", "/v5/position/switch-isolated", data=data)
        ret_code = res.get("retCode", 0)
        return ret_code in (0, 110026)

    async def set_trading_stop(
        self,
        symbol: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        tp_trigger_by: str = "MarkPrice",
        sl_trigger_by: str = "MarkPrice",
        tpsl_mode: str = "Full",
        category: str = "linear",
    ) -> bool:
        """Establish or update server-side hard stop loss / take profit on position."""
        data: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "positionIdx": 0,
            "tpslMode": tpsl_mode,
        }
        if stop_loss is not None:
            data["stopLoss"] = str(stop_loss)
            data["slTriggerBy"] = sl_trigger_by
        if take_profit is not None:
            data["takeProfit"] = str(take_profit)
            data["tpTriggerBy"] = tp_trigger_by

        res = await self._request("POST", "/v5/position/set-trading-stop", data=data)
        return res.get("retCode") == 0

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    async def place_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: Optional[float] = None,
        order_type: str = "Limit",
        time_in_force: str = "PostOnly",
        reduce_only: bool = False,
        order_link_id: Optional[str] = None,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
        category: str = "linear",
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "side": side.capitalize(),
            "orderType": order_type.capitalize(),
            "qty": str(qty),
            "timeInForce": time_in_force,
            "positionIdx": 0,
            "reduceOnly": reduce_only,
        }
        if price is not None:
            data["price"] = str(price)
        if order_link_id:
            data["orderLinkId"] = order_link_id
        if take_profit:
            data["takeProfit"] = str(take_profit)
        if stop_loss:
            data["stopLoss"] = str(stop_loss)

        return await self._request("POST", "/v5/order/create", data=data)

    async def cancel_order(self, symbol: str, order_id: Optional[str] = None, order_link_id: Optional[str] = None, category: str = "linear") -> bool:
        data: Dict[str, Any] = {"category": category, "symbol": symbol}
        if order_id:
            data["orderId"] = order_id
        if order_link_id:
            data["orderLinkId"] = order_link_id
        res = await self._request("POST", "/v5/order/cancel", data=data)
        return res.get("retCode") == 0

    async def cancel_all_orders(self, symbol: Optional[str] = None, category: str = "linear", settle_coin: str = "USDT") -> bool:
        data: Dict[str, Any] = {"category": category}
        if symbol:
            data["symbol"] = symbol
        else:
            data["settleCoin"] = settle_coin
        res = await self._request("POST", "/v5/order/cancel-all", data=data)
        return res.get("retCode") == 0

    async def get_open_orders(self, symbol: Optional[str] = None, category: str = "linear", settle_coin: str = "USDT") -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"category": category}
        if symbol:
            params["symbol"] = symbol
        else:
            params["settleCoin"] = settle_coin
        res = await self._request("GET", "/v5/order/realtime", params=params)
        return res.get("result", {}).get("list", []) if res.get("retCode") == 0 else []

    async def get_executions(
        self,
        symbol: Optional[str] = None,
        order_id: Optional[str] = None,
        order_link_id: Optional[str] = None,
        limit: int = 50,
        category: str = "linear",
    ) -> List[Dict[str, Any]]:
        """Fetch execution records with exact fees paid directly from Bybit matching engine."""
        params: Dict[str, Any] = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol
        if order_id:
            params["orderId"] = order_id
        if order_link_id:
            params["orderLinkId"] = order_link_id
        res = await self._request("GET", "/v5/execution/list", params=params)
        return res.get("result", {}).get("list", []) if res.get("retCode") == 0 else []

    async def get_closed_pnl(
        self,
        symbol: Optional[str] = None,
        limit: int = 50,
        category: str = "linear",
    ) -> List[Dict[str, Any]]:
        """Fetch authoritative closed PnL and trade statistics from Bybit."""
        params: Dict[str, Any] = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol
        res = await self._request("GET", "/v5/position/closed-pnl", params=params)
        return res.get("result", {}).get("list", []) if res.get("retCode") == 0 else []

