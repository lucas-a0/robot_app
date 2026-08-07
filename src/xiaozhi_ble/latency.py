"""Dialogue-server latency provider.

Measures the round-trip latency to the voice dialogue server with a plain TCP
connect against the host:port parsed from the configured WebSocket URL —
the closest user-space approximation of ``ping`` without raw ICMP sockets
(which would need root), no subprocesses and no third-party dependencies.

The bridge learns the effective URL from the control socket and pushes it via
``set_url``; until a URL is set the provider idles and the GATT characteristic
stays ``UNKNOWN``. A failed connect reports ``None`` (unreachable), which the
characteristic renders as ``LATENCY -``. To keep BLE notification traffic
meaningful, the callback only fires when the latency changed by at least
``notify_threshold_ms`` (or reachability flipped).
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Callable
from urllib.parse import urlparse

from loguru import logger

# Receives the connect latency in milliseconds, or None when the server is
# unreachable (DNS failure, refused connection, timeout).
LatencyCallback = Callable[[float | None], None]

_DEFAULT_PORTS = {"ws": 80, "wss": 443}


def parse_ws_url(url: str) -> tuple[str, int] | None:
    """Parse a ws(s):// URL into (host, port), or None when invalid."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if parsed.scheme not in _DEFAULT_PORTS or not parsed.hostname:
        return None
    return parsed.hostname, parsed.port or _DEFAULT_PORTS[parsed.scheme]


def measure_tcp_latency_ms(
    host: str,
    port: int,
    timeout_secs: float = 2.0,
) -> float | None:
    """Return the TCP connect time to host:port in ms, or None on failure."""
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout_secs):
            pass
    except OSError:
        return None
    return (time.monotonic() - started) * 1000.0


class LatencyProvider:
    """Probe the dialogue server on a timer and forward latency changes."""

    def __init__(
        self,
        on_latency: LatencyCallback,
        poll_interval_secs: float = 5.0,
        notify_threshold_ms: float = 10.0,
        connect_timeout_secs: float = 2.0,
    ) -> None:
        self._on_latency = on_latency
        self._poll_interval_secs = poll_interval_secs
        self._notify_threshold_ms = notify_threshold_ms
        self._connect_timeout_secs = connect_timeout_secs
        self._target: tuple[str, int] | None = None
        self._last_reported: float | None = None
        # Distinct from _last_reported being None (= reported unreachable):
        # a first failed probe must still reach the App, not sit at UNKNOWN.
        self._has_reported = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        """The provider can always run; it idles until a URL is pushed."""
        return True

    def set_url(self, url: str) -> None:
        """Update the probe target from the effective WebSocket URL."""
        target = parse_ws_url(url)
        if target != self._target:
            self._target = target
            # Force a fresh report for the new target.
            self._has_reported = False
            self._last_reported = None
            if target is None:
                logger.warning(f"Latency probe disabled: invalid ws URL {url!r}")
            else:
                logger.info(f"Latency probe target set to {target[0]}:{target[1]}")

    def start(self) -> None:
        """Start probing (no-op when already running)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._has_reported = False
        self._last_reported = None
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="xiaozhi-latency",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            f"Latency provider started: interval={self._poll_interval_secs}s "
            f"threshold={self._notify_threshold_ms}ms"
        )

    def stop(self, timeout: float = 5.0) -> None:
        """Stop probing."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                logger.warning(f"Latency poll error: {exc}")
            self._stop_event.wait(self._poll_interval_secs)

    def _poll_once(self) -> None:
        if self._target is None:
            return
        latency = measure_tcp_latency_ms(
            self._target[0], self._target[1], self._connect_timeout_secs
        )
        if self._has_reported:
            same_reachability = (latency is None) == (self._last_reported is None)
            if not same_reachability:
                pass
            elif latency is None:
                return
            elif abs(latency - self._last_reported) < self._notify_threshold_ms:
                return
        self._has_reported = True
        self._last_reported = latency
        self._on_latency(latency)
