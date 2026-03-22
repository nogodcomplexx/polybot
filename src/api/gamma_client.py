"""
Gamma API client — market discovery and metadata.
"""

import logging
from typing import List, Optional, Dict, Any

import httpx

from src.data.models import Market

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"


class GammaClient:
    """Async client for the Polymarket Gamma API (public, no auth required)."""

    def __init__(self, host: str = GAMMA_BASE):
        self.host = host.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.host,
            timeout=30.0,
            headers={"Accept": "application/json"},
        )

    async def close(self):
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Events & Markets
    # ------------------------------------------------------------------

    async def get_events(
        self,
        limit: int = 50,
        offset: int = 0,
        active: bool = True,
        closed: bool = False,
        order: str = "volume24hr",
        ascending: bool = False,
        tag: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch events from Gamma API."""
        params: Dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "order": order,
            "ascending": str(ascending).lower(),
        }
        if tag:
            params["tag"] = tag

        resp = await self._client.get("/events", params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        active: bool = True,
        closed: bool = False,
        order: str = "volume24hr",
        ascending: bool = False,
        tag: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch markets from Gamma API."""
        params: Dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "order": order,
            "ascending": str(ascending).lower(),
        }
        if tag:
            params["tag"] = tag

        resp = await self._client.get("/markets", params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_market(self, condition_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single market by condition ID."""
        resp = await self._client.get("/markets", params={"condition_id": condition_id, "limit": 1})
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and len(data) > 0:
            return data[0]
        return None

    async def get_event(self, event_id: str) -> Dict[str, Any]:
        """Fetch a single event."""
        resp = await self._client.get(f"/events/{event_id}")
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def parse_market(self, raw: Dict[str, Any]) -> Optional[Market]:
        """Parse a raw Gamma API market dict into a Market model."""
        try:
            tokens_raw = raw.get("clobTokenIds") or raw.get("clob_token_ids") or []
            if isinstance(tokens_raw, str) and tokens_raw.startswith("["):
                try:
                    import json
                    tokens_raw = json.loads(tokens_raw)
                except Exception:
                    parts = [p.strip() for p in tokens_raw.replace("[", "").replace("]", "").split(",")]
                    tokens_raw = [p.strip('"').strip() for p in parts if p.strip()]

            tokens = tokens_raw if isinstance(tokens_raw, list) else []
            outcomes = raw.get("outcomes") or []
            outcome_prices = raw.get("outcomePrices") or raw.get("outcome_prices") or []

            yes_token = tokens[0] if len(tokens) > 0 else ""
            no_token = tokens[1] if len(tokens) > 1 else ""

            # Parse prices
            yes_price = 0.0
            no_price = 0.0
            if outcome_prices:
                try:
                    if isinstance(outcome_prices, str):
                        import json
                        outcome_prices = json.loads(outcome_prices)
                    yes_price = float(outcome_prices[0]) if len(outcome_prices) > 0 else 0.0
                    no_price = float(outcome_prices[1]) if len(outcome_prices) > 1 else 0.0
                except (ValueError, IndexError):
                    pass

            return Market(
                condition_id=raw.get("conditionId") or raw.get("condition_id") or "",
                question=raw.get("question") or "",
                description=raw.get("description") or "",
                category=raw.get("groupItemTitle") or raw.get("category") or "",
                end_date=raw.get("endDate") or raw.get("end_date_iso") or "",
                active=raw.get("active", True),
                closed=raw.get("closed", False),
                liquidity=float(raw.get("liquidity") or 0),
                volume_24h=float(raw.get("volume24hr") or raw.get("volume_num") or 0),
                yes_token_id=yes_token,
                no_token_id=no_token,
                yes_price=yes_price,
                no_price=no_price,
                spread=abs(yes_price - (1.0 - no_price)) if yes_price and no_price else 0.0,
                tags=raw.get("tags") or [],
                slug=raw.get("slug") or "",
                image=raw.get("image") or "",
            )
        except Exception as e:
            logger.warning(f"Failed to parse market: {e}")
            return None

    async def fetch_active_markets(self, limit: int = 200) -> List[Market]:
        """Fetch and parse all active markets."""
        raw_markets = []
        try:
            # Paginate through all crypto markets
            for offset in range(0, 1000, 100):
                crypto_batch = await self.get_markets(limit=100, offset=offset, active=True, closed=False, tag="crypto")
                raw_markets.extend(crypto_batch)
                if len(crypto_batch) < 100:
                    break
        except Exception as e:
            logger.debug(f"Failed to fetch crypto tagged markets: {e}")
            
        try:
            # Paginate through all bitcoin markets
            for offset in range(0, 500, 100):
                btc_batch = await self.get_markets(limit=100, offset=offset, active=True, closed=False, tag="bitcoin")
                raw_markets.extend(btc_batch)
                if len(btc_batch) < 100:
                    break
        except Exception as e:
            logger.debug(f"Failed to fetch bitcoin tagged markets: {e}")
            
        try:
            # Paginate through all ethereum markets
            for offset in range(0, 500, 100):
                eth_batch = await self.get_markets(limit=100, offset=offset, active=True, closed=False, tag="ethereum")
                raw_markets.extend(eth_batch)
                if len(eth_batch) < 100:
                    break
        except Exception as e:
            logger.debug(f"Failed to fetch ethereum tagged markets: {e}")

        markets = []
        seen = set()
        for raw in raw_markets:
            m = self.parse_market(raw)
            if m and m.condition_id and m.condition_id not in seen:
                seen.add(m.condition_id)
                markets.append(m)
        logger.info(f"Fetched {len(markets)} active markets from Gamma API")
        return markets
