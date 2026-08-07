"""Tests for the dialogue-server latency provider."""

import socket
import time

import xiaozhi_ble.latency as latency
from xiaozhi_ble.latency import (
    LatencyProvider,
    measure_tcp_latency_ms,
    parse_ws_url,
)


def test_parse_ws_url_reads_host_and_port():
    assert parse_ws_url("wss://robot.example.com/ws/robot") == (
        "robot.example.com",
        443,
    )
    assert parse_ws_url("ws://robot.local:8080/ws") == ("robot.local", 8080)
    assert parse_ws_url("  ws://a.example  ") == ("a.example", 80)


def test_parse_ws_url_rejects_invalid_urls():
    assert parse_ws_url("") is None
    assert parse_ws_url("http://robot.example.com") is None
    assert parse_ws_url("wss://") is None
    assert parse_ws_url("not a url") is None


def _listening_socket():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    return server


def test_measure_tcp_latency_ms_to_local_listener():
    with _listening_socket() as server:
        port = server.getsockname()[1]
        latency_ms = measure_tcp_latency_ms("127.0.0.1", port, timeout_secs=2.0)
    assert latency_ms is not None
    assert latency_ms >= 0


def test_measure_tcp_latency_ms_returns_none_when_refused():
    server = _listening_socket()
    port = server.getsockname()[1]
    server.close()
    assert measure_tcp_latency_ms("127.0.0.1", port, timeout_secs=0.5) is None


def _provider_with_latencies(monkeypatch, updates, values, threshold_ms=10.0):
    scripted = iter(values)
    monkeypatch.setattr(
        latency, "measure_tcp_latency_ms", lambda _h, _p, _t: next(scripted)
    )
    provider = LatencyProvider(updates.append, notify_threshold_ms=threshold_ms)
    provider.set_url("ws://robot.local/ws")
    return provider


def test_provider_reports_only_changes_beyond_threshold(monkeypatch):
    updates = []
    provider = _provider_with_latencies(
        monkeypatch,
        updates,
        [
            12.0,  # first reading, reported
            15.0,  # change 3 ms: skipped
            30.0,  # change vs reported 18 ms: reported
            None,  # reachability flipped: reported
            None,  # still unreachable: skipped
        ],
    )

    for _ in range(5):
        provider._poll_once()

    assert updates == [12.0, 30.0, None]


def test_provider_idles_without_url(monkeypatch):
    updates = []
    monkeypatch.setattr(
        latency, "measure_tcp_latency_ms", lambda *_a: updates.append(1.0) or 1.0
    )
    provider = LatencyProvider(updates.append)
    provider._poll_once()
    assert updates == []


def test_provider_retargets_on_url_change(monkeypatch):
    updates = []
    provider = _provider_with_latencies(monkeypatch, updates, [12.0, 12.5])

    provider._poll_once()
    assert updates == [12.0]

    provider.set_url("ws://robot.local:9090/ws")
    # Small change must still be reported: the target is new.
    provider._poll_once()
    assert updates == [12.0, 12.5]


def test_provider_poll_loop_starts_and_stops(monkeypatch):
    updates = []
    monkeypatch.setattr(
        latency, "measure_tcp_latency_ms", lambda *_a: 5.0
    )
    provider = LatencyProvider(updates.append, poll_interval_secs=0.05)
    provider.set_url("ws://robot.local/ws")

    provider.start()
    try:
        deadline = time.monotonic() + 5.0
        while not updates and time.monotonic() < deadline:
            time.sleep(0.01)
        assert updates == [5.0]
    finally:
        provider.stop()
    assert provider._thread is None
