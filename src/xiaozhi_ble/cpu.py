"""CPU usage provider.

Reads cumulative CPU tick counters from ``/proc/stat`` on a timer and
computes the usage percentage between consecutive samples — the same data
``top`` is built on, with no subprocesses and no third-party dependencies.

CPU usage has no instantaneous value, so the provider keeps the previous
counters and reports ``busy_delta / total_delta``. The first poll only
establishes the baseline; the first reading appears after one interval.
To keep BLE notification traffic meaningful, the callback only fires when
the usage changed by at least ``notify_threshold`` percentage points.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from loguru import logger

# Receives the whole-machine CPU usage in percent (0-100).
CpuUsageCallback = Callable[[float], None]

_PROC_STAT = Path("/proc/stat")


def parse_proc_stat_cpu(content: str) -> tuple[int, int] | None:
    """Parse the aggregate ``cpu`` line into (busy_ticks, total_ticks).

    Fields are ``user nice system idle iowait irq softirq steal ...``;
    missing trailing fields count as zero.
    """
    lines = content.splitlines()
    if not lines:
        return None
    fields = lines[0].split()
    if not fields or fields[0] != "cpu":
        return None
    try:
        ticks = [int(field) for field in fields[1:]]
    except ValueError:
        return None
    if len(ticks) < 4:
        return None
    user, nice, system, idle, iowait, irq, softirq, steal = (ticks + [0] * 8)[:8]
    busy = user + nice + system + irq + softirq + steal
    total = busy + idle + iowait
    return busy, total


def read_cpu_counters(proc_stat: Path = _PROC_STAT) -> tuple[int, int] | None:
    """Return (busy_ticks, total_ticks) from /proc/stat, or None."""
    try:
        content = proc_stat.read_text()
    except OSError:
        return None
    return parse_proc_stat_cpu(content)


class CpuProvider:
    """Poll /proc/stat and forward CPU usage changes."""

    def __init__(
        self,
        on_usage: CpuUsageCallback,
        poll_interval_secs: float = 5.0,
        notify_threshold: float = 1.0,
        proc_stat: Path = _PROC_STAT,
    ) -> None:
        self._on_usage = on_usage
        self._poll_interval_secs = poll_interval_secs
        self._notify_threshold = notify_threshold
        self._proc_stat = proc_stat
        self._previous: tuple[int, int] | None = None
        self._last_reported: float | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self._proc_stat.is_file()

    def start(self) -> None:
        """Start polling (no-op when disabled or already running)."""
        if not self.enabled:
            logger.info(f"CPU provider disabled: {self._proc_stat} not readable")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._previous = None
        self._last_reported = None
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="xiaozhi-cpu",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            f"CPU provider started: interval={self._poll_interval_secs}s "
            f"threshold={self._notify_threshold}%"
        )

    def stop(self, timeout: float = 5.0) -> None:
        """Stop polling."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                logger.warning(f"CPU usage poll error: {exc}")
            self._stop_event.wait(self._poll_interval_secs)

    def _poll_once(self) -> None:
        counters = read_cpu_counters(self._proc_stat)
        if counters is None:
            return
        if self._previous is None:
            # First sample only establishes the baseline; usage needs a delta.
            self._previous = counters
            return
        busy_delta = counters[0] - self._previous[0]
        total_delta = counters[1] - self._previous[1]
        self._previous = counters
        if total_delta <= 0:
            return
        usage = busy_delta * 100.0 / total_delta
        if (
            self._last_reported is None
            or abs(usage - self._last_reported) >= self._notify_threshold
        ):
            self._last_reported = usage
            self._on_usage(usage)
