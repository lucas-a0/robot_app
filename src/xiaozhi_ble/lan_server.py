"""TCP line-protocol control server, the LAN fallback for BLE GATT.

One client at a time: a new connection replaces the previous one. UTF-8
text, one message per line, channel name prefix matching the BLE
characteristics. Providers stay unaware of this transport; the bridge
fans notifications out to both GATT and this server.
"""

from __future__ import annotations

import socket
import threading
from typing import Callable

from loguru import logger

from .wifi_config import WifiRequestError, parse_wifi_request

RobotControlCallback = Callable[[str], str | None]
WifiConfigCallback = Callable[[str, str], str | None]
NavTaskCallback = Callable[[str], str]
ZoneNavCallback = Callable[[str], str]
CmdVelCallback = Callable[[str], str]
ZoneVoiceCallback = Callable[[str], str]
InitialPoseCallback = Callable[[str], str]

_LINE_LIMIT = 180

READ_CHANNELS = frozenset(
    {
        "LAN",
        "AUDIO",
        "BATTERY",
        "NETWORK",
        "CPU",
        "BANDWIDTH",
        "MEMORY",
        "ROBOT",
        "WIFI",
        "NAV",
        "ZONE_NAV",
        "CMD_VEL",
        "ZONE_VOICE",
        "INITIAL_POSE",
    }
)
WRITE_CHANNELS = frozenset(
    {
        "ROBOT",
        "WIFI",
        "NAV",
        "ZONE_NAV",
        "CMD_VEL",
        "ZONE_VOICE",
        "INITIAL_POSE",
    }
)
# LAN / AUDIO are read-only and are not pushed after the connect snapshot.
PUSH_CHANNELS = READ_CHANNELS - {"LAN", "AUDIO"}
SNAPSHOT_ORDER = (
    "LAN",
    "AUDIO",
    "BATTERY",
    "NETWORK",
    "CPU",
    "BANDWIDTH",
    "MEMORY",
    "ROBOT",
    "WIFI",
    "NAV",
    "ZONE_NAV",
    "CMD_VEL",
    "ZONE_VOICE",
    "INITIAL_POSE",
)


def _bounded_text(text: str, limit: int = _LINE_LIMIT) -> str:
    data = text.encode("utf-8")[:limit]
    return data.decode("utf-8", errors="ignore")


def format_battery(percentage: float | None, supply_status: str | None) -> str:
    if percentage is None and supply_status is None:
        return "UNKNOWN"
    percentage_text = "UNKNOWN" if percentage is None else f"{percentage:.3f}"
    return f"{percentage_text} {supply_status or 'UNKNOWN'}"


def format_network(
    ssid: str | None,
    rssi_dbm: int | None,
    ipv4: str | None,
) -> str:
    if ssid is None:
        return "DISCONNECTED"
    signal_text = "-" if rssi_dbm is None else str(rssi_dbm)
    ip_text = ipv4 or "-"
    return f"WIFI {signal_text} {ip_text} {ssid}"


def format_cpu(usage: float | None) -> str:
    return "UNKNOWN" if usage is None else f"CPU {usage:.1f}"


def format_memory(
    used_mb: int | None,
    total_mb: int | None,
    percent: float | None,
) -> str:
    if used_mb is None or total_mb is None or percent is None:
        return "UNKNOWN"
    return f"MEM {used_mb} {total_mb} {percent:.1f}"


def format_bandwidth(rx_kbps: float | None, tx_kbps: float | None) -> str:
    if rx_kbps is None or tx_kbps is None:
        return "UNKNOWN"
    return f"BANDWIDTH {rx_kbps:.1f} {tx_kbps:.1f}"


def parse_request_line(line: str) -> tuple[str, str, str]:
    """Split a client line into (kind, channel, payload).

    ``kind`` is ``GET`` or ``WRITE``. Channel is uppercased. Raises
    ``ValueError`` with a protocol reason for empty / unknown input.
    """
    stripped = line.strip()
    if not stripped:
        raise ValueError("empty")
    parts = stripped.split(None, 1)
    verb = parts[0].upper()
    rest = parts[1] if len(parts) > 1 else ""
    if verb == "GET":
        channel = rest.strip().upper()
        if not channel:
            raise ValueError("command")
        if channel not in READ_CHANNELS:
            raise ValueError("unknown")
        return "GET", channel, ""
    channel = verb
    if channel not in READ_CHANNELS and channel not in WRITE_CHANNELS:
        raise ValueError("unknown")
    return "WRITE", channel, rest


class LanControlServer:
    """Threaded TCP control server with a single active client."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 4205,
        robot_control_callback: RobotControlCallback | None = None,
        wifi_config_callback: WifiConfigCallback | None = None,
        nav_task_callback: NavTaskCallback | None = None,
        zone_nav_callback: ZoneNavCallback | None = None,
        cmd_vel_callback: CmdVelCallback | None = None,
        zone_voice_callback: ZoneVoiceCallback | None = None,
        initial_pose_callback: InitialPoseCallback | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._robot_control_callback = robot_control_callback or (
            lambda _command: "ERR unavailable"
        )
        self._wifi_config_callback = wifi_config_callback or (
            lambda _ssid, _password: "ERR unavailable"
        )
        self._nav_task_callback = nav_task_callback or (
            lambda _text: "ERR unavailable"
        )
        self._zone_nav_callback = zone_nav_callback or (
            lambda _zone: "ERR unavailable"
        )
        self._cmd_vel_callback = cmd_vel_callback or (
            lambda _text: "ERR unavailable"
        )

        def _unavailable_zone_voice(text: str) -> str:
            verb = text.strip().split(None, 1)[0].lower() if text.strip() else ""
            if verb == "status":
                return "UNKNOWN"
            return "ERR unavailable"

        self._zone_voice_callback = zone_voice_callback or _unavailable_zone_voice
        self._initial_pose_callback = initial_pose_callback or (
            lambda _text: "ERR unavailable"
        )
        self._values: dict[str, str] = {
            "LAN": "UNKNOWN",
            "AUDIO": "UNKNOWN",
            "BATTERY": "UNKNOWN",
            "NETWORK": "UNKNOWN",
            "CPU": "UNKNOWN",
            "BANDWIDTH": "UNKNOWN",
            "MEMORY": "UNKNOWN",
        }
        self._lock = threading.Lock()
        self._client: socket.socket | None = None
        self._generation = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._listen: socket.socket | None = None

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> bool:
        """Bind and start accepting (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return True
        listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listen.bind((self._host, self._port))
            listen.listen(1)
            listen.settimeout(0.5)
        except OSError as exc:
            listen.close()
            logger.error(f"Failed to bind LAN control server: {exc}")
            return False
        self._listen = listen
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._accept_loop,
            name="xiaozhi-lan",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"LAN control server listening on {self._host}:{self._port}")
        return True

    def stop(self, timeout: float = 5.0) -> None:
        """Close the listener and the active client."""
        self._stop_event.set()
        listen = self._listen
        self._listen = None
        if listen is not None:
            try:
                listen.close()
            except OSError:
                pass
        with self._lock:
            client = self._client
            self._client = None
            self._generation += 1
        if client is not None:
            try:
                client.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def set_lan_endpoint(self, text: str) -> None:
        """Update the cached LAN endpoint (no push after the snapshot)."""
        self._store("LAN", text, push=False)

    def set_audio_endpoint(self, text: str) -> None:
        """Update the cached audio endpoint (no push after the snapshot)."""
        self._store("AUDIO", text, push=False)

    def notify_battery_status(
        self,
        percentage: float | None,
        supply_status: str | None,
    ) -> None:
        self._store("BATTERY", format_battery(percentage, supply_status))

    def notify_network_status(
        self,
        ssid: str | None,
        rssi_dbm: int | None,
        ipv4: str | None,
    ) -> None:
        self._store("NETWORK", format_network(ssid, rssi_dbm, ipv4))

    def notify_cpu_usage(self, usage: float | None) -> None:
        self._store("CPU", format_cpu(usage))

    def notify_memory_usage(
        self,
        used_mb: int | None,
        total_mb: int | None,
        percent: float | None,
    ) -> None:
        self._store("MEMORY", format_memory(used_mb, total_mb, percent))

    def notify_bandwidth(self, rx_kbps: float | None, tx_kbps: float | None) -> None:
        self._store("BANDWIDTH", format_bandwidth(rx_kbps, tx_kbps))

    def notify_robot_control_error(self, text: str) -> None:
        self._store("ROBOT", text)

    def notify_wifi_config_result(self, text: str) -> None:
        self._store("WIFI", text)

    def notify_nav_task(self, text: str) -> None:
        self._store("NAV", text)

    def notify_zone_nav(self, text: str) -> None:
        self._store("ZONE_NAV", text)

    def notify_cmd_vel(self, text: str) -> None:
        self._store("CMD_VEL", text)

    def notify_zone_voice(self, text: str) -> None:
        self._store("ZONE_VOICE", text)

    def notify_initial_pose(self, text: str) -> None:
        self._store("INITIAL_POSE", text)

    def _store(self, channel: str, text: str, push: bool = True) -> None:
        bounded = _bounded_text(text)
        with self._lock:
            self._values[channel] = bounded
            client = self._client if push and channel in PUSH_CHANNELS else None
        if client is not None:
            self._send(client, channel, bounded)

    def _accept_loop(self) -> None:
        assert self._listen is not None
        listen = self._listen
        while not self._stop_event.is_set():
            try:
                conn, addr = listen.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop_event.is_set():
                    return
                logger.warning("LAN control accept failed")
                continue
            logger.info(f"LAN control client connected: {addr[0]}:{addr[1]}")
            self._replace_client(conn)

    def _replace_client(self, conn: socket.socket) -> None:
        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        with self._lock:
            old = self._client
            self._generation += 1
            generation = self._generation
            self._client = conn
            snapshot = [
                (channel, self._values[channel])
                for channel in SNAPSHOT_ORDER
                if channel in self._values and self._values[channel]
            ]
        if old is not None:
            try:
                old.close()
            except OSError:
                pass
        for channel, text in snapshot:
            self._send(conn, channel, text)
        reader = threading.Thread(
            target=self._read_loop,
            args=(conn, generation),
            name="xiaozhi-lan-client",
            daemon=True,
        )
        reader.start()

    def _read_loop(self, conn: socket.socket, generation: int) -> None:
        buf = b""
        wifi_lines: list[str] | None = None
        try:
            while not self._stop_event.is_set():
                try:
                    data = conn.recv(4096)
                except OSError:
                    break
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    try:
                        line = raw.decode("utf-8").rstrip("\r")
                    except UnicodeDecodeError:
                        self._send_err(conn, "encoding")
                        wifi_lines = None
                        continue
                    wifi_lines = self._handle_line(conn, line, wifi_lines)
        finally:
            with self._lock:
                if self._generation == generation and self._client is conn:
                    self._client = None
            try:
                conn.close()
            except OSError:
                pass

    def _handle_line(
        self,
        conn: socket.socket,
        line: str,
        wifi_lines: list[str] | None,
    ) -> list[str] | None:
        if wifi_lines is not None:
            wifi_lines.append(line)
            if len(wifi_lines) < 2:
                return wifi_lines
            ssid_line, password_line = wifi_lines
            self._dispatch_wifi(conn, f"{ssid_line}\n{password_line}")
            return None

        try:
            kind, channel, payload = parse_request_line(line)
        except ValueError as exc:
            reason = str(exc)
            if reason == "unknown":
                token = line.strip().split(None, 1)[0] if line.strip() else ""
                self._send_err(conn, "unknown", token.upper() or "-")
            else:
                self._send_err(conn, reason)
            return None

        if kind == "GET":
            with self._lock:
                text = self._values.get(channel, "")
            self._send(conn, channel, text or "")
            return None

        if channel not in WRITE_CHANNELS:
            self._send_err(conn, "readonly", channel)
            return None

        if channel == "WIFI":
            if payload:
                self._send_err(conn, "command", "WIFI")
                return None
            return []
        self._dispatch_write(conn, channel, payload)
        return None

    def _dispatch_wifi(self, conn: socket.socket, payload: str) -> None:
        try:
            ssid, password = parse_wifi_request(payload)
        except WifiRequestError:
            self._store("WIFI", "ERR invalid")
            return
        logger.info(f"Received LAN wifi-config request: ssid={ssid!r}")
        try:
            error = self._wifi_config_callback(ssid, password)
        except Exception:
            logger.exception("Failed to dispatch LAN wifi-config command")
            self._store("WIFI", "ERR internal")
            return
        if error is not None:
            self._store("WIFI", error)
        else:
            # GATT reports CONNECTING asynchronously via notify; keep a
            # synchronous accept so the TCP client knows the write landed.
            self._store("WIFI", f"CONNECTING {ssid}")

    def _dispatch_write(self, conn: socket.socket, channel: str, payload: str) -> None:
        try:
            reply = self._invoke_write(channel, payload)
        except Exception:
            logger.exception(f"Failed to dispatch LAN {channel} command")
            self._store(channel, "ERR internal")
            return
        if reply is None:
            return
        self._store(channel, reply)

    def _invoke_write(self, channel: str, payload: str) -> str | None:
        if channel == "ROBOT":
            command = payload.strip().lower()
            logger.info(f"Received LAN robot-control command: {command!r}")
            error = self._robot_control_callback(command)
            if error is not None:
                return error
            return f"OK {command}"
        if channel == "NAV":
            logger.info(f"Received LAN nav-task command: {payload!r}")
            return self._nav_task_callback(payload)
        if channel == "ZONE_NAV":
            zone = payload.strip().lower()
            logger.info(f"Received LAN zone-nav command: {zone!r}")
            return self._zone_nav_callback(zone)
        if channel == "CMD_VEL":
            logger.info(f"Received LAN cmd-vel command: {payload!r}")
            return self._cmd_vel_callback(payload)
        if channel == "ZONE_VOICE":
            logger.info(f"Received LAN zone-voice command: {payload!r}")
            return self._zone_voice_callback(payload)
        if channel == "INITIAL_POSE":
            logger.info(f"Received LAN initial-pose trigger: {payload!r}")
            return self._initial_pose_callback(payload)
        return "ERR command"

    def _send(self, conn: socket.socket, channel: str, payload: str) -> None:
        line = f"{channel} {_bounded_text(payload)}" if payload else channel
        try:
            conn.sendall((line + "\n").encode("utf-8"))
        except OSError:
            pass

    def _send_err(
        self,
        conn: socket.socket,
        reason: str,
        channel: str | None = None,
    ) -> None:
        if channel:
            self._send(conn, "ERR", f"{channel} {reason}")
        else:
            self._send(conn, "ERR", reason)
