"""Tests for the control-socket client."""

import os
import socket
import threading
import time

import pytest

from xiaozhi_ble.control_client import (
    ControlClient,
    ControlRequestError,
    ControlUnavailable,
)


class _FakeServer:
    """A minimal threaded control-protocol server for tests."""

    def __init__(self, socket_path: str) -> None:
        self._socket_path = socket_path
        self._requests: list[str] = []
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(socket_path)
        self._listener.listen()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    @property
    def requests(self) -> list[str]:
        with self._lock:
            return list(self._requests)

    def broadcast(self, line: str) -> None:
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.sendall(line.encode("utf-8") + b"\n")
            except OSError:
                pass

    def close(self) -> None:
        self._stop.set()
        self._listener.close()
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            # shutdown wakes a recv blocked in the peer (and in our own
            # serving thread); a bare close would not.
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                client.close()
            except OSError:
                pass
        self._thread.join(timeout=2)
        # Remove the socket file so the path can be bound again.
        try:
            os.unlink(self._socket_path)
        except OSError:
            pass

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            with self._lock:
                self._clients.append(conn)
            if self._stop.is_set():
                # Accepted while close() was in flight; the client list was
                # already snapshotted, so shut this connection down ourselves.
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                conn.close()
                return
            threading.Thread(
                target=self._serve_client, args=(conn,), daemon=True
            ).start()

    def _serve_client(self, conn: socket.socket) -> None:
        conn.sendall(b"STATE idle\nNONE\n")
        buffer = b""
        try:
            while not self._stop.is_set():
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buffer += chunk
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    line = raw.decode("utf-8").strip()
                    if not line:
                        continue
                    with self._lock:
                        self._requests.append(line)
                    if line == "start":
                        conn.sendall(b"OK start\n")
                    elif line == "stop":
                        conn.sendall(b"OK stop\n")
                    elif line.startswith("set_url "):
                        conn.sendall(f"OK url {line[8:]}\n".encode("utf-8"))
                    else:
                        conn.sendall(b"ERR COMMAND unsupported command\n")
        except OSError:
            return


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_request_response_and_notifications(tmp_path):
    notifications = []
    server = _FakeServer(str(tmp_path / "control.sock"))
    client = ControlClient(
        socket_path=str(tmp_path / "control.sock"),
        on_notification=notifications.append,
        reconnect_delay=0.05,
        request_timeout=2.0,
    )
    client.start()
    try:
        assert _wait_for(lambda: client.connected)
        assert _wait_for(lambda: notifications == ["STATE idle", "NONE"])

        assert client.request("start") == "OK start"
        assert client.request("stop") == "OK stop"
        assert client.request("set_url wss://new.example/ws") == (
            "OK url wss://new.example/ws"
        )

        server.broadcast("STATE listening")
        assert _wait_for(lambda: notifications[-1] == "STATE listening")
    finally:
        client.stop()
        server.close()

    assert server.requests == ["start", "stop", "set_url wss://new.example/ws"]


def test_err_response_raises_request_error(tmp_path):
    server = _FakeServer(str(tmp_path / "control.sock"))
    client = ControlClient(
        socket_path=str(tmp_path / "control.sock"),
        on_notification=lambda _line: None,
        reconnect_delay=0.05,
        request_timeout=2.0,
    )
    client.start()
    try:
        assert _wait_for(lambda: client.connected)
        with pytest.raises(ControlRequestError) as exc_info:
            client.request("dance")
        assert exc_info.value.code == "COMMAND"
    finally:
        client.stop()
        server.close()


def test_request_fails_when_disconnected_and_reconnects(tmp_path):
    socket_path = str(tmp_path / "control.sock")
    notifications = []
    connection_changes = []
    client = ControlClient(
        socket_path=socket_path,
        on_notification=notifications.append,
        on_connection_change=connection_changes.append,
        reconnect_delay=0.05,
        request_timeout=1.0,
    )
    client.start()
    try:
        with pytest.raises(ControlUnavailable):
            client.request("start", timeout=0.2)

        server = _FakeServer(socket_path)
        try:
            assert _wait_for(lambda: client.connected)
            assert client.request("start") == "OK start"

            # Server disappears: subsequent requests fail fast.
            server.close()
            assert _wait_for(lambda: not client.connected)
            with pytest.raises(ControlUnavailable):
                client.request("stop", timeout=0.2)
        finally:
            server.close()

        # Server comes back: the client reconnects and works again.
        server = _FakeServer(socket_path)
        try:
            assert _wait_for(lambda: client.connected)
            assert client.request("stop") == "OK stop"
        finally:
            server.close()
    finally:
        client.stop()

    assert connection_changes.count(True) >= 2
    assert connection_changes.count(False) >= 1
