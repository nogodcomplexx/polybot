"""
Asynchronous Non-Blocking Telemetry Bus.
Provides high-throughput, decoupled event distribution to dashboard and observatory clients.
CRITICAL INVARIANT: The publisher never blocks or awaits slow subscribers.
"""

import asyncio
import logging
from typing import Dict, Any, Set, Optional, Callable
import time

logger = logging.getLogger(__name__)


class TelemetryBus:
    """Decoupled, non-blocking telemetry event distribution bus."""

    def __init__(self, max_queue_size: int = 500):
        self.max_queue_size = max_queue_size
        self._subscribers: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()
        self.total_published: int = 0
        self.total_dropped: int = 0
        self._latest_state: Dict[str, Any] = {}

    def subscribe(self, queue_size: Optional[int] = None) -> asyncio.Queue:
        """Register a new consumer queue."""
        q = asyncio.Queue(maxsize=queue_size or self.max_queue_size)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, queue: asyncio.Queue):
        """Unregister a consumer queue."""
        self._subscribers.discard(queue)

    def publish(self, event: Any):
        """
        Publish an event to all subscribers without blocking.
        Uses put_nowait(). If a queue is full, drops the oldest item.
        """
        self.total_published += 1

        # Cache latest event by event type for state hydration
        if hasattr(event, "to_dict"):
            data = event.to_dict()
        elif isinstance(event, dict):
            data = event
        else:
            data = {"type": "GENERIC", "payload": event}

        event_type = data.get("type", "UNKNOWN")
        self._latest_state[event_type] = data

        dead_queues = []
        for q in list(self._subscribers):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                # Discard oldest to prevent stall and maintain real-time freshness
                try:
                    _ = q.get_nowait()
                    q.put_nowait(data)
                    self.total_dropped += 1
                except Exception:
                    dead_queues.append(q)
            except Exception:
                dead_queues.append(q)

        for dead in dead_queues:
            self._subscribers.discard(dead)

    def get_latest_state(self) -> Dict[str, Any]:
        """Return cached latest state across all event channels."""
        return dict(self._latest_state)
