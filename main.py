"""
Polybot — Autonomous Bybit V5 Perpetual Quantitative Scalping Operating System.
Main Execution Entry Point.
"""

import asyncio
from src.bybit_main import main

if __name__ == "__main__":
    asyncio.run(main())
