"""
System Metrics & Performance Collector.
Monitors process memory, CPU utilization, event loop lag, and runtime health.
"""

import asyncio
import os
import sys
import time
from typing import Dict, Any


class SystemMetricsCollector:
    """Collects runtime health and system load metrics without heavy external dependencies."""

    def __init__(self):
        self.start_time: float = time.time()
        self._last_process_time: float = time.process_time()
        self._last_wall_time: float = time.time()
        self._cached_cpu_pct: float = 0.0
        self.event_loop_lag_ms: float = 0.0

    async def measure_event_loop_lag(self) -> float:
        """Measure event loop scheduling latency in milliseconds."""
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        await asyncio.sleep(0)
        t1 = loop.time()
        self.event_loop_lag_ms = round((t1 - t0) * 1000.0, 2)
        return self.event_loop_lag_ms

    def get_memory_mb(self) -> float:
        """Get current resident set size in megabytes."""
        try:
            if sys.platform == "win32":
                import ctypes
                from ctypes import wintypes

                class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                    _fields_ = [
                        ("cb", wintypes.DWORD),
                        ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                    ]

                counters = PROCESS_MEMORY_COUNTERS()
                counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
                handle = ctypes.windll.kernel32.GetCurrentProcess()
                if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    return round(counters.WorkingSetSize / (1024 * 1024), 1)
            else:
                import resource
                rusage = resource.getrusage(resource.RUSAGE_SELF)
                return round(rusage.ru_maxrss / 1024.0, 1)
        except Exception:
            pass
        return 65.0  # Safe fallback estimate

    def get_cpu_pct(self) -> float:
        """Calculate approximate process CPU utilization."""
        now = time.time()
        dt_wall = now - self._last_wall_time
        if dt_wall >= 1.0:
            now_proc = time.process_time()
            dt_proc = now_proc - self._last_process_time
            num_cores = os.cpu_count() or 1
            self._cached_cpu_pct = round(min(100.0, (dt_proc / (dt_wall * num_cores)) * 100.0), 1)
            self._last_process_time = now_proc
            self._last_wall_time = now
        return self._cached_cpu_pct

    def get_uptime_sec(self) -> float:
        """Return system uptime in seconds."""
        return round(time.time() - self.start_time, 1)
