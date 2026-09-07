"""
Asynchronous High-Throughput Market Data Bus.
Publishes and routes normalized market intelligence events across the system.
"""

import asyncio
import logging
import inspect
from typing import Dict, Any, List, Optional, Callable, Coroutine, Set, Tuple
from src.bybit.intelligence.models import EventType

logger = logging.getLogger(__name__)


class MarketDataBus:
    """Pub/sub message broker distributing normalized multi-exchange market events."""

    def __init__(self):
        self._subscribers: Dict[EventType, List[Tuple[Callable, Optional[str]]]] = {
            et: [] for et in EventType
        }
        self.published_count: int = 0
        self.delivered_count: int = 0
        self.error_count: int = 0
        self._background_tasks: set = set()
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        self._main_loop = loop

    def subscribe(
        self,
        event_type: EventType,
        callback: Callable[[EventType, Any], Coroutine],
        symbol: Optional[str] = None,
    ):
        """Register an async callback for specific event types and optional canonical symbol."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        target_sym = symbol.upper() if symbol else None
        self._subscribers[event_type].append((callback, target_sym))

    def unsubscribe(self, event_type: EventType, callback: Callable):
        """Unregister callback."""
        if event_type in self._subscribers:
            self._subscribers[event_type] = [
                (cb, sym) for cb, sym in self._subscribers[event_type] if cb != callback
            ]

    def publish(self, event_type: EventType, data: Any, symbol: Optional[str] = None):
        """
        Publish normalized event synchronously or non-blocking.
        Schedules async delivery for coroutines or invokes sync callbacks immediately.
        """
        self.published_count += 1
        listeners = self._subscribers.get(event_type, [])
        if not listeners:
            return

        target_sym = symbol.upper() if symbol else None

        for cb, sub_sym in listeners:
            if sub_sym is not None and target_sym is not None and sub_sym != target_sym:
                continue

            if asyncio.iscoroutinefunction(cb):
                try:
                    loop = asyncio.get_running_loop()
                    task = loop.create_task(self._safe_async_dispatch(cb, event_type, data))
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)
                except RuntimeError:
                    if self._main_loop and not self._main_loop.is_closed():
                        asyncio.run_coroutine_threadsafe(
                            self._safe_async_dispatch(cb, event_type, data),
                            self._main_loop
                        )
                    else:
                        self.error_count += 1
                        logger.warning(f"Cannot dispatch async event {event_type.value}: no event loop available")
            else:
                self._safe_sync_dispatch(cb, event_type, data)

    def _safe_sync_dispatch(self, callback: Callable, event_type: EventType, data: Any):
        """Invoke synchronous callback safely supporting 1-arg or 2-arg signatures."""
        try:
            sig = inspect.signature(callback)
            if len(sig.parameters) >= 2:
                callback(event_type, data)
            else:
                callback(data)
            self.delivered_count += 1
        except Exception as err:
            self.error_count += 1
            logger.error(f"Error in subscriber callback for {event_type.value}: {err}")

    async def _safe_async_dispatch(self, callback: Callable, event_type: EventType, data: Any):
        """Deliver event safely to async coroutines."""
        try:
            sig = inspect.signature(callback)
            if len(sig.parameters) >= 2:
                res = callback(event_type, data)
            else:
                res = callback(data)
            if asyncio.iscoroutine(res):
                await res
            self.delivered_count += 1
        except Exception as err:
            self.error_count += 1
            logger.error(f"Error in async subscriber callback for {event_type.value}: {err}")

