"""WiFi bandwidth provider.

Reads cumulative rx/tx byte counters for the wireless interface from
``/proc/net/dev`` on a timer and computes the throughput between consecutive
samples — the same data ``ifstat`` is built on, with no subprocesses and no
third-party dependencies.

Throughput has no instantaneous value, so the provider keeps the previous
counters and the sampling timestamp (``time.monotonic``) and reports
``bytes_delta / elapsed_secs`` in KB/s. The first poll only establishes the
baseline; the first reading appears after one interval. To keep BLE
notification traffic meaningful, the callback only fires when either direction
changed by at least ``notify_threshold`` KB/s.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from loguru import logger

from .network import detect_wifi_interface

# Receives (rx_kbps, tx_kbps) of the wireless interface.
BandwidthCallback = Callable[[float, float], None]

_PROC_NET_DEV = Path("/proc/net/dev")


def parse_proc_net_dev(content: str, interface: str) -> tuple[int, int] | None:
    """Parse (rx_bytes, tx_bytes) for an interface from /proc/net/dev.

    Fields per interface line are
    ``iface: rx_bytes rx_packets ... tx_bytes tx_packets ...``;
    tx_bytes is the 9th counter after the colon.
    """
    for line in content.splitlines():
        fields = line.split()
        if len(fields) < 10 or fields[0] != f"{interface}:":
            continue
        try:
            return int(fields[1]), int(fields[9])
        except ValueError:
            return None
    return None


def read_traffic_counters(
    interface: str,
    proc_net_dev: Path = _PROC_NET_DEV,
) -> tuple[int, int] | None:
    """Return (rx_bytes, tx_bytes) from /proc/net/dev, or None."""
    try:
        content = proc_net_dev.read_text()
    except OSError:
        return None
    return parse_proc_net_dev(content, interface)


class BandwidthProvider:
    """Poll /proc/net/dev and forward WiFi throughput changes."""

    def __init__(
        self,
        on_bandwidth: BandwidthCallback,
        interface: str = "",
        poll_interval_secs: float = 5.0,
        notify_threshold: float = 10.0,
        proc_net_dev: Path = _PROC_NET_DEV,
    ) -> None:
        self._interface = interface.strip() or (detect_wifi_interface() or "")
        self._on_bandwidth = on_bandwidth
        self._poll_interval_secs = poll_interval_secs
        self._notify_threshold = notify_threshold
        self._proc_net_dev = proc_net_dev
        self._previous: tuple[int, int, float] | None = None
        self._last_reported: tuple[float, float] | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._interface) and self._proc_net_dev.is_file()

    def start(self) -> None:
        """Start polling (no-op when disabled or already running)."""
        if not self.enabled:
            logger.info(
                f"Bandwidth provider disabled: interface={self._interface!r} "
                f"proc_net_dev={self._proc_net_dev}"
            )
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._previous = None
        self._last_reported = None
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="xiaozhi-bandwidth",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            f"Bandwidth provider started: interface={self._interface} "
            f"interval={self._poll_interval_secs}s "
            f"threshold={self._notify_threshold}KB/s"
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
                logger.warning(f"Bandwidth poll error: {exc}")
            self._stop_event.wait(self._poll_interval_secs)

    def _poll_once(self) -> None:
        counters = read_traffic_counters(self._interface, self._proc_net_dev)
        if counters is None:
            return
        now = time.monotonic()
        if self._previous is None:
            # First sample only establishes the baseline; rate needs a delta.
            self._previous = (counters[0], counters[1], now)
            return
        rx_delta = counters[0] - self._previous[0]
        tx_delta = counters[1] - self._previous[1]
        elapsed = now - self._previous[2]
        self._previous = (counters[0], counters[1], now)
        if elapsed <= 0:
            return
        rate = (rx_delta / 1024.0 / elapsed, tx_delta / 1024.0 / elapsed)
        if self._last_reported is None or any(
            abs(current - reported) >= self._notify_threshold
            for current, reported in zip(rate, self._last_reported)
        ):
            self._last_reported = rate
            self._on_bandwidth(*rate)
