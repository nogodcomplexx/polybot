"""
Bybit Scalper Main Runner — Entry point for Bybit V5 Perpetual Futures Quantitative Scalping Operating System.
Orchestrates the Three-Brain Architecture: Alpha Engine, Policy Engine, and Execution Engine.
"""

import asyncio
import logging
import os
import signal
import sys
import time

# Force UTF-8 stdout encoding on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
else:
    # Linux / Ubuntu 24.04 performance optimization: fast C-based event loop
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass


from rich.console import Console
from rich.logging import RichHandler

from src.bybit.config import load_bybit_config
from src.bybit.app.orchestrator import BybitScalperOrchestrator

console = Console(legacy_windows=False)


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, console=console, show_path=False)],
    )


async def main():
    setup_logging()
    config = load_bybit_config()
    orchestrator = BybitScalperOrchestrator(config)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(orchestrator.stop()))
        except NotImplementedError:
            pass

    try:
        await orchestrator.start()
    except (KeyboardInterrupt, asyncio.CancelledError):
        await orchestrator.stop()


if __name__ == "__main__":
    asyncio.run(main())
