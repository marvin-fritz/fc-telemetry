"""Prozessmetriken ohne psutil: RSS aus /proc, CPU aus os.times, Threads aus threading."""

from __future__ import annotations

import os
import threading
import time
from typing import Callable


def _page_size() -> int:
    try:
        return int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, ValueError, OSError):
        return 4096


class ProcStats:
    def __init__(self, statm_path: str = "/proc/self/statm", clock: Callable[[], float] = time.monotonic) -> None:
        self._statm_path = statm_path
        self._clock = clock
        self._page_size = _page_size()
        self._last_cpu: float | None = None
        self._last_wall: float | None = None

    def _rss_mb(self) -> float | None:
        try:
            with open(self._statm_path, encoding="ascii") as fh:
                fields = fh.read().split()
            return round(int(fields[1]) * self._page_size / (1024 * 1024), 1)
        except (OSError, IndexError, ValueError):
            return None

    def _cpu_pct(self) -> float:
        t = os.times()
        cpu = float(t.user + t.system)
        wall = self._clock()
        pct = 0.0
        if self._last_cpu is not None and self._last_wall is not None and wall > self._last_wall:
            pct = round((cpu - self._last_cpu) / (wall - self._last_wall) * 100.0, 1)
        self._last_cpu, self._last_wall = cpu, wall
        return max(0.0, pct)

    def snapshot(self) -> dict:
        snap: dict = {"cpuPct": self._cpu_pct(), "threads": threading.active_count()}
        rss = self._rss_mb()
        if rss is not None:
            snap["rssMb"] = rss
        return snap
