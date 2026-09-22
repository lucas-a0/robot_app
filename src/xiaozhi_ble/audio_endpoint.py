"""Discover the local PCM playback TCP port.

``pcm_tcp_server.py`` exposes a short-lived query socket (default 4204)
that replies with one UTF-8 line ``AUDIO <port> <rate> <channels>``. This
provider polls that socket over a plain TCP connect — no subprocesses —
and reports the PCM listen port when it changes.

The LAN IPv4 address is supplied by the caller (from the network
provider); this module only discovers the port. Missing IP or a failed
query is formatted as ``UNKNOWN`` by ``format_endpoint``.
"""

from __future__ import annotations

import socket
import threading
from typing import Callable

from loguru import logger

# Receives the PCM listen port, or None when the query failed.
AudioPortCallback = Callable[[int | None], None]

_DEFAULT_QUERY_HOST = "127.0.0.1"
_DEFAULT_QUERY_PORT = 4204
_DEFAULT_TIMEOUT_SECS = 1.0


def format_endpoint(ipv4: str | None, port: int | None) -> str:
    """Return ``<ipv4> <port>``, or ``UNKNOWN`` when either field is missing."""
    if not ipv4 or port is None:
        return "UNKNOWN"
    return f"{ipv4} {port}"


def parse_audio_query_line(line: str) -> int | None:
    """Parse ``AUDIO <port> <rate> <channels>`` and return the PCM port.

    Rate and channel fields are accepted but ignored by the bridge; they
    exist so a client talking to the query port directly can see the
    playback format. Returns None on any malformed input.
    """
    parts = line.strip().split()
    if len(parts) != 4 or parts[0].upper() != "AUDIO":
        return None
    try:
        port = int(parts[1])
        rate = int(parts[2])
        channels = int(parts[3])
    except ValueError:
        return None
    if not (1 <= port <= 65535) or rate <= 0 or channels <= 0:
        return None
    return port


def query_audio_port(
    host: str,
    port: int,
    timeout_secs: float = _DEFAULT_TIMEOUT_SECS,
) -> int | None:
    """Connect to the query socket and return the advertised PCM port."""
    try:
        with socket.create_connection((host, port), timeout=timeout_secs) as sock:
            sock.settimeout(timeout_secs)
            data = b""
            while b"\n" not in data and len(data) < 256:
                chunk = sock.recv(256)
                if not chunk:
                    break
                data += chunk
    except OSError:
        return None
    line = data.split(b"\n", 1)[0].decode("utf-8", errors="replace")
    return parse_audio_query_line(line)


class AudioEndpointProvider:
    """Poll the PCM query port and forward changes."""

    def __init__(
        self,
        on_port: AudioPortCallback,
        query_host: str = _DEFAULT_QUERY_HOST,
        query_port: int = _DEFAULT_QUERY_PORT,
        poll_interval_secs: float = 5.0,
        timeout_secs: float = _DEFAULT_TIMEOUT_SECS,
    ) -> None:
        self._on_port = on_port
        self._query_host = query_host
        self._query_port = query_port
        self._poll_interval_secs = poll_interval_secs
        self._timeout_secs = timeout_secs
        self._last_reported: int | None | object = object()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._query_host) and 1 <= self._query_port <= 65535

    def start(self) -> None:
        """Start polling (no-op when disabled or already running)."""
        if not self.enabled:
            logger.info("Audio endpoint provider disabled: query port not configured")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._last_reported = object()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="xiaozhi-audio-endpoint",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            f"Audio endpoint provider started: "
            f"query={self._query_host}:{self._query_port} "
            f"interval={self._poll_interval_secs}s"
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
                logger.warning(f"Audio endpoint poll error: {exc}")
            self._stop_event.wait(self._poll_interval_secs)

    def _poll_once(self) -> None:
        port = query_audio_port(
            self._query_host,
            self._query_port,
            timeout_secs=self._timeout_secs,
        )
        if port == self._last_reported:
            return
        self._last_reported = port
        self._on_port(port)
