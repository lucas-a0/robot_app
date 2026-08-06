"""WiFi network status provider.

Reads the WiFi link status directly from the kernel on a timer: the SSID via
a wireless-extensions ioctl, the signal level from ``/proc/net/wireless`` and
the IPv4 address via netifaces. No subprocesses and no ROS2 or control-socket
dependency.

The provider only invokes the callback when the status changes; the GATT
characteristic caches the latest snapshot for reads and for the immediate
notification sent when an App subscribes.
"""

from __future__ import annotations

import array
import fcntl
import socket
import struct
import threading
from pathlib import Path
from typing import Callable

from loguru import logger

# Receives (ssid, rssi_dbm, ipv4); all are None while disconnected or until
# the first successful poll.
NetworkStatusCallback = Callable[[str | None, int | None, str | None], None]

_SYS_CLASS_NET = Path("/sys/class/net")
_PROC_NET_WIRELESS = Path("/proc/net/wireless")

# Wireless extensions: get the current ESSID (32-byte buffer).
_SIOCGIWESSID = 0x8B1B
_ESSID_MAX_SIZE = 32


def detect_wifi_interface(sys_class_net: Path = _SYS_CLASS_NET) -> str | None:
    """Return the first wireless interface name, or None when there is none."""
    try:
        names = sorted(entry.name for entry in sys_class_net.iterdir())
    except OSError:
        return None
    for name in names:
        if name.startswith("wlan") or (sys_class_net / name / "wireless").exists():
            return name
    return None


def read_essid(interface: str) -> str | None:
    """Return the connected SSID, or None when not associated."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        essid = array.array("B", b"\0" * _ESSID_MAX_SIZE)
        pointer, length = essid.buffer_info()
        request = struct.pack(
            "16sPHBB", interface.encode("utf-8"), pointer, length, 0, 0
        )
        fcntl.ioctl(sock.fileno(), _SIOCGIWESSID, request)
    except OSError:
        return None
    finally:
        sock.close()
    ssid = essid.tobytes().rstrip(b"\0").decode("utf-8", errors="replace").strip()
    return ssid or None


def parse_proc_net_wireless(content: str, interface: str) -> int | None:
    """Parse the signal level (dBm) for an interface from /proc/net/wireless."""
    for line in content.splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[0] != f"{interface}:":
            continue
        try:
            return round(float(fields[3].rstrip(".")))
        except ValueError:
            return None
    return None


def read_signal_dbm(
    interface: str,
    proc_net_wireless: Path = _PROC_NET_WIRELESS,
) -> int | None:
    """Return the current signal level in dBm, or None when unavailable."""
    try:
        content = proc_net_wireless.read_text()
    except OSError:
        return None
    return parse_proc_net_wireless(content, interface)


def read_ipv4(interface: str) -> str | None:
    """Return the first IPv4 address of the interface, or None."""
    try:
        import netifaces
    except ImportError:
        logger.error("netifaces is not importable; IPv4 reporting disabled")
        return None
    try:
        addresses = netifaces.ifaddresses(interface)
    except (OSError, ValueError):
        return None
    entries = addresses.get(netifaces.AF_INET) or []
    return entries[0].get("addr") if entries else None


class NetworkProvider:
    """Poll the WiFi link status and forward changes."""

    def __init__(
        self,
        interface: str,
        on_status: NetworkStatusCallback,
        poll_interval_secs: float = 5.0,
    ) -> None:
        self._interface = interface.strip() or (detect_wifi_interface() or "")
        self._on_status = on_status
        self._poll_interval_secs = poll_interval_secs
        self._last: tuple[str | None, int | None, str | None] | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._interface)

    def start(self) -> None:
        """Start polling (no-op when disabled or already running)."""
        if not self.enabled:
            logger.info("Network provider disabled: no wireless interface found")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="xiaozhi-network",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            f"Network provider started: interface={self._interface} "
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
                logger.warning(f"Network status poll error: {exc}")
            self._stop_event.wait(self._poll_interval_secs)

    def _poll_once(self) -> None:
        ssid = read_essid(self._interface)
        if ssid is None:
            status = (None, None, None)
        else:
            status = (
                ssid,
                read_signal_dbm(self._interface),
                read_ipv4(self._interface),
            )
        if status != self._last:
            self._last = status
            self._on_status(*status)
