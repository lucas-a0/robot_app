"""Memory usage provider.

Reads ``MemTotal`` / ``MemAvailable`` from ``/proc/meminfo`` on a timer and
reports used / total / occupancy — the same data ``free`` is built on, with
no subprocesses and no third-party dependencies.

Unlike CPU usage, memory occupancy is instantaneous, so the first poll
already produces a reading. To keep BLE notification traffic meaningful,
the callback only fires when occupancy changed by at least
``notify_threshold`` percentage points.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from loguru import logger

# Receives (used_mb, total_mb, occupancy percent 0-100).
MemoryUsageCallback = Callable[[int, int, float], None]

_PROC_MEMINFO = Path("/proc/meminfo")
_KB_PER_MB = 1024


def parse_proc_meminfo(content: str) -> tuple[int, int] | None:
    """Parse /proc/meminfo into (used_kb, total_kb).

    ``used`` is ``MemTotal - MemAvailable`` (the same definition ``free``
    uses). When ``MemAvailable`` is missing, fall back to
    ``MemFree + Buffers + Cached``.
    """
    fields: dict[str, int] = {}
    for line in content.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        key = parts[0].rstrip(":")
        try:
            fields[key] = int(parts[1])
        except ValueError:
            continue
    total = fields.get("MemTotal")
    if total is None or total <= 0:
        return None
    available = fields.get("MemAvailable")
    if available is None:
        available = (
            fields.get("MemFree", 0)
            + fields.get("Buffers", 0)
            + fields.get("Cached", 0)
        )
    available = max(0, min(available, total))
    return total - available, total


def read_memory_usage(proc_meminfo: Path = _PROC_MEMINFO) -> tuple[int, int] | None:
    """Return (used_kb, total_kb) from /proc/meminfo, or None."""
    try:
        content = proc_meminfo.read_text()
    except OSError:
        return None
    return parse_proc_meminfo(content)


class MemoryProvider:
    """Poll /proc/meminfo and forward memory occupancy changes."""

    def __init__(
        self,
        on_usage: MemoryUsageCallback,
        poll_interval_secs: float = 5.0,
        notify_threshold: float = 1.0,
        proc_meminfo: Path = _PROC_MEMINFO,
    ) -> None:
        self._on_usage = on_usage
        self._poll_interval_secs = poll_interval_secs
        self._notify_threshold = notify_threshold
        self._proc_meminfo = proc_meminfo
        self._last_reported: float | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self._proc_meminfo.is_file()

    def start(self) -> None:
        """Start polling (no-op when disabled or already running)."""
        if not self.enabled:
            logger.info(f"Memory provider disabled: {self._proc_meminfo} not readable")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._last_reported = None
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="xiaozhi-memory",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            f"Memory provider started: interval={self._poll_interval_secs}s "
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
                logger.warning(f"Memory usage poll error: {exc}")
            self._stop_event.wait(self._poll_interval_secs)

    def _poll_once(self) -> None:
        usage = read_memory_usage(self._proc_meminfo)
        if usage is None:
            return
        used_kb, total_kb = usage
        percent = used_kb * 100.0 / total_kb
        if (
            self._last_reported is None
            or abs(percent - self._last_reported) >= self._notify_threshold
        ):
            self._last_reported = percent
            self._on_usage(used_kb // _KB_PER_MB, total_kb // _KB_PER_MB, percent)
