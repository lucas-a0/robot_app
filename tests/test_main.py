"""Tests for the bridge wiring in main.py."""

import time

from xiaozhi_ble.config import BridgeConfig
from xiaozhi_ble.control_client import ControlRequestError, ControlUnavailable
from xiaozhi_ble.main import _Bridge


class _StubClient:
    def __init__(self, response: str | None = None, exc: Exception | None = None):
        self._response = response
        self._exc = exc
        self.requests = []

    def request(self, line: str) -> str:
        self.requests.append(line)
        if self._exc is not None:
            raise self._exc
        return self._response


class _StubServer:
    def __init__(self) -> None:
        self.urls = []
        self.errors = []

    def notify_websocket_url(self, url: str) -> None:
        self.urls.append(url)

    def notify_error(self, code: str, message: str) -> None:
        self.errors.append((code, message))


def _make_bridge(tmp_path) -> _Bridge:
    path = tmp_path / "config.yaml"
    # Empty battery topic keeps the provider disabled; nothing is started.
    path.write_text("battery:\n  topic: ''\n")
    config = BridgeConfig(str(path))
    assert config.load()
    bridge = _Bridge(config)
    bridge._server = _StubServer()
    return bridge


def test_sync_websocket_url_publishes_current_url(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._client = _StubClient("OK url wss://robot.example.com/ws/robot")

    bridge._sync_websocket_url()

    assert bridge._client.requests == ["get_url"]
    assert bridge._server.urls == ["wss://robot.example.com/ws/robot"]
    # The latency probe follows the effective dialogue-server URL.
    assert bridge._latency_provider._target == ("robot.example.com", 443)


def test_sync_websocket_url_tolerates_socket_failures(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._client = _StubClient(
        exc=ControlUnavailable("control socket is not connected")
    )

    bridge._sync_websocket_url()

    assert bridge._server.urls == []

    bridge._client = _StubClient(exc=ControlRequestError("ERR", "not supported"))
    bridge._sync_websocket_url()

    assert bridge._server.urls == []


def test_sync_websocket_url_ignores_unexpected_response(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._client = _StubClient("OK url")

    bridge._sync_websocket_url()

    assert bridge._server.urls == []


def test_connection_change_syncs_url_from_worker_thread(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._client = _StubClient("OK url ws://robot.local/ws")

    # Must not block the caller (the client's reader thread in production).
    bridge._handle_connection_change(True)

    deadline = time.monotonic() + 5.0
    while not bridge._server.urls and time.monotonic() < deadline:
        time.sleep(0.01)

    assert bridge._server.errors == [("NONE", "")]
    assert bridge._server.urls == ["ws://robot.local/ws"]


def test_connection_change_reports_socket_loss(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._client = _StubClient()

    bridge._handle_connection_change(False)

    assert bridge._server.errors == [
        ("VOICE_UNAVAILABLE", "control socket is not connected")
    ]
    assert bridge._client.requests == []
