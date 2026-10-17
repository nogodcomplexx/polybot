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
from pathlib import Path

# Ensure repo root is on sys.path (needed for 'dashboard' package when invoked as 'python src/bybit_main.py')
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

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


from logging.handlers import RotatingFileHandler
from rich.console import Console
from rich.logging import RichHandler

from src.bybit.config import load_bybit_config
from src.bybit.app.orchestrator import BybitScalperOrchestrator

console = Console(legacy_windows=False)


def setup_logging():
    log_dir = Path(_REPO_ROOT) / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "polybot.log"

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=20 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )
    file_handler.setLevel(logging.INFO)

    console_handler = RichHandler(rich_tracebacks=True, console=console, show_path=False)
    console_handler.setLevel(logging.INFO)

    logging.basicConfig(
        level=logging.INFO,
        handlers=[console_handler, file_handler],
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
