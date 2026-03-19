"""
Polymarket authentication — derives CLOB L2 API credentials from private key.
"""

import logging
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

from src.config import PolymarketConfig

logger = logging.getLogger(__name__)


def derive_api_credentials(pm_cfg: PolymarketConfig) -> ApiCreds:
    """
    Use L1 private-key auth to derive L2 API credentials.
    Returns ApiCreds(api_key, secret, passphrase).
    """
    if pm_cfg.api_key and pm_cfg.api_secret and pm_cfg.api_passphrase:
        logger.info("Using existing L2 API credentials from .env")
        return ApiCreds(
            api_key=pm_cfg.api_key,
            api_secret=pm_cfg.api_secret,
            api_passphrase=pm_cfg.api_passphrase,
        )

    if not pm_cfg.private_key:
        raise ValueError(
            "POLYMARKET_PRIVATE_KEY is not set. "
            "Export your wallet private key and add it to .env"
        )

    logger.info("Deriving L2 API credentials from private key (L1 auth)...")
    client = ClobClient(
        host=pm_cfg.clob_host,
        chain_id=pm_cfg.chain_id,
        key=pm_cfg.private_key,
        signature_type=0,  # EOA wallet (MetaMask)
    )
    creds = client.derive_api_key()
    logger.info("L2 API credentials derived successfully")
    return ApiCreds(
        api_key=creds.api_key,
        api_secret=creds.api_secret,
        api_passphrase=creds.api_passphrase,
    )


def build_clob_client(pm_cfg: PolymarketConfig) -> ClobClient:
    """Build an authenticated ClobClient ready for trading."""
    creds = derive_api_credentials(pm_cfg)

    client = ClobClient(
        host=pm_cfg.clob_host,
        chain_id=pm_cfg.chain_id,
        key=pm_cfg.private_key,
        signature_type=0,
        creds=creds,
        funder=pm_cfg.relayer_api_key_address or None,
    )
    logger.info("ClobClient initialized and authenticated")
    return client
