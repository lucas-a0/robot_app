"""Tests for the PCM query-port audio endpoint provider."""

import socket
import threading

from xiaozhi_ble.audio_endpoint import (
    AudioEndpointProvider,
    format_endpoint,
    parse_audio_query_line,
    query_audio_port,
)


def test_format_endpoint_requires_both_fields():
    assert format_endpoint("192.168.1.12", 4203) == "192.168.1.12 4203"
    assert format_endpoint(None, 4203) == "UNKNOWN"
    assert format_endpoint("192.168.1.12", None) == "UNKNOWN"
    assert format_endpoint("", 4203) == "UNKNOWN"


def test_parse_audio_query_line_accepts_valid_line():
    assert parse_audio_query_line("AUDIO 4203 24000 1") == 4203
    assert parse_audio_query_line("audio 4203 16000 2\n") == 4203


def test_parse_audio_query_line_rejects_bad_input():
    assert parse_audio_query_line("") is None
    assert parse_audio_query_line("AUDIO") is None
    assert parse_audio_query_line("AUDIO 4203 24000") is None
    assert parse_audio_query_line("PCM 4203 24000 1") is None
    assert parse_audio_query_line("AUDIO 0 24000 1") is None
    assert parse_audio_query_line("AUDIO 70000 24000 1") is None
    assert parse_audio_query_line("AUDIO x 24000 1") is None
    assert parse_audio_query_line("AUDIO 4203 0 1") is None
    assert parse_audio_query_line("AUDIO 4203 24000 0") is None


def _serve_one_line(port_holder: list, line: bytes, ready: threading.Event) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port_holder.append(srv.getsockname()[1])
    srv.listen(1)
    ready.set()
    conn, _addr = srv.accept()
    try:
        conn.sendall(line)
    finally:
        conn.close()
        srv.close()


def test_query_audio_port_reads_advertised_port():
    port_holder: list[int] = []
    ready = threading.Event()
    thread = threading.Thread(
        target=_serve_one_line,
        args=(port_holder, b"AUDIO 4203 24000 1\n", ready),
        daemon=True,
    )
    thread.start()
    assert ready.wait(2.0)
    assert query_audio_port("127.0.0.1", port_holder[0], timeout_secs=2.0) == 4203
    thread.join(2.0)


def test_query_audio_port_returns_none_when_refused():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()
    assert query_audio_port("127.0.0.1", port, timeout_secs=0.3) is None


def test_provider_reports_only_port_changes(monkeypatch):
    updates: list[int | None] = []
    ports = iter([4203, 4203, 4205, None, None])
    monkeypatch.setattr(
        "xiaozhi_ble.audio_endpoint.query_audio_port",
        lambda *_a, **_k: next(ports),
    )
    provider = AudioEndpointProvider(updates.append, poll_interval_secs=0.05)
    for _ in range(5):
        provider._poll_once()
    assert updates == [4203, 4205, None]


def test_provider_start_stop_is_idempotent(monkeypatch):
    monkeypatch.setattr(
        "xiaozhi_ble.audio_endpoint.query_audio_port",
        lambda *_a, **_k: 4203,
    )
    provider = AudioEndpointProvider(lambda _port: None, poll_interval_secs=0.05)
    provider.start()
    provider.start()
    provider.stop(timeout=2.0)
    provider.stop(timeout=0.1)


def test_provider_disabled_when_query_port_invalid():
    updates: list[int | None] = []
    provider = AudioEndpointProvider(updates.append, query_port=0)
    assert provider.enabled is False
    provider.start()
    assert provider._thread is None
    assert updates == []
