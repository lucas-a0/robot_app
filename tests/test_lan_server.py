"""Tests for the LAN TCP line-protocol control server."""

import socket
import time

from xiaozhi_ble.lan_server import (
    LanControlServer,
    format_battery,
    format_cpu,
    format_network,
    parse_request_line,
)


def test_parse_request_line_get_and_write():
    assert parse_request_line("GET AUDIO") == ("GET", "AUDIO", "")
    assert parse_request_line("get battery") == ("GET", "BATTERY", "")
    assert parse_request_line("CMD_VEL -0.3 0.1") == (
        "WRITE",
        "CMD_VEL",
        "-0.3 0.1",
    )
    assert parse_request_line("WIFI") == ("WRITE", "WIFI", "")


def test_parse_request_line_rejects_bad_input():
    try:
        parse_request_line("   ")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert str(exc) == "empty"
    try:
        parse_request_line("GET")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert str(exc) == "command"
    try:
        parse_request_line("FOO bar")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert str(exc) == "unknown"


def test_status_formatters_match_ble_text():
    assert format_battery(None, None) == "UNKNOWN"
    assert format_battery(0.67, "CHARGING") == "0.670 CHARGING"
    assert format_network(None, None, None) == "DISCONNECTED"
    assert format_network("MyHome", -38, "172.16.0.195") == (
        "WIFI -38 172.16.0.195 MyHome"
    )
    assert format_cpu(23.54) == "CPU 23.5"


def _read_lines(sock: socket.socket, count: int, timeout: float = 2.0) -> list[str]:
    sock.settimeout(timeout)
    buf = b""
    lines: list[str] = []
    deadline = time.monotonic() + timeout
    while len(lines) < count and time.monotonic() < deadline:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf and len(lines) < count:
            raw, buf = buf.split(b"\n", 1)
            lines.append(raw.decode("utf-8").rstrip("\r"))
    return lines


def _connect(port: int) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", port), timeout=2.0)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return sock


def test_snapshot_get_write_and_single_client():
    robot_calls: list[str] = []

    def robot(command: str) -> str | None:
        robot_calls.append(command)
        return None

    server = LanControlServer(
        host="127.0.0.1",
        port=0,
        robot_control_callback=robot,
    )
    assert server.start()
    port = server._listen.getsockname()[1]
    try:
        server.set_lan_endpoint("192.168.1.12 4205")
        server.set_audio_endpoint("192.168.1.12 4203")
        server.notify_battery_status(0.67, "CHARGING")

        first = _connect(port)
        snap = _read_lines(first, 7)
        assert "LAN 192.168.1.12 4205" in snap
        assert "AUDIO 192.168.1.12 4203" in snap
        assert "BATTERY 0.670 CHARGING" in snap

        first.sendall(b"GET AUDIO\n")
        assert _read_lines(first, 1) == ["AUDIO 192.168.1.12 4203"]

        first.sendall(b"ROBOT stand_up\n")
        assert _read_lines(first, 1) == ["ROBOT OK stand_up"]
        assert robot_calls == ["stand_up"]

        first.sendall(b"GET NOPE\n")
        err = _read_lines(first, 1)[0]
        assert err.startswith("ERR ")
        assert "unknown" in err.lower() or "NOPE" in err

        first.sendall(b"LAN 1.2.3.4 9\n")
        assert "readonly" in _read_lines(first, 1)[0]

        second = _connect(port)
        _read_lines(second, 7)
        # The first client should be kicked.
        first.sendall(b"GET AUDIO\n")
        first.settimeout(0.5)
        try:
            data = first.recv(64)
        except OSError:
            data = b""
        assert data == b""
        second.close()
        first.close()
    finally:
        server.stop()


def test_wifi_multiline_write_and_notify_fanout():
    wifi_calls: list[tuple[str, str]] = []

    def wifi(ssid: str, password: str) -> str | None:
        wifi_calls.append((ssid, password))
        return None

    server = LanControlServer(
        host="127.0.0.1",
        port=0,
        wifi_config_callback=wifi,
    )
    assert server.start()
    port = server._listen.getsockname()[1]
    try:
        sock = _connect(port)
        _read_lines(sock, 7)
        sock.sendall(b"WIFI\nMyHome\n12345678\n")
        lines = _read_lines(sock, 1)
        assert lines == ["WIFI CONNECTING MyHome"]
        assert wifi_calls == [("MyHome", "12345678")]

        server.notify_wifi_config_result("CONNECTED MyHome")
        assert _read_lines(sock, 1) == ["WIFI CONNECTED MyHome"]
        sock.close()
    finally:
        server.stop()


def test_start_stop_is_idempotent():
    server = LanControlServer(host="127.0.0.1", port=0)
    assert server.start()
    assert server.start()
    server.stop()
    server.stop()
