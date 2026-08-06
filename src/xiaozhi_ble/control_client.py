"""Client for the xiaozhi Unix socket control protocol.

Keeps a persistent connection to the voice client's control socket with
automatic reconnect. Requests (``start``, ``stop``, ``set_url ...``) are
serialized: only one is in flight at a time, matched to its ``OK``/``ERR``
response. Everything else arriving on the socket is a notification and is
forwarded to the ``on_notification`` callback.
"""

from __future__ import annotations

import socket
import threading
from typing import Callable

from loguru import logger

NotificationCallback = Callable[[str], None]
ConnectionCallback = Callable[[bool], None]


class ControlUnavailable(Exception):
    """Raised when the control socket is not connected."""


class ControlRequestError(Exception):
    """The server rejected a request; carries the protocol error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ControlClient:
    """Persistent control-socket client with automatic reconnect.

    ``on_notification`` and ``on_connection_change`` are called from the
    reader thread; ``request`` may be called from any other thread.
    """

    def __init__(
        self,
        socket_path: str,
        on_notification: NotificationCallback,
        on_connection_change: ConnectionCallback | None = None,
        reconnect_delay: float = 2.0,
        request_timeout: float = 5.0,
    ) -> None:
        self._socket_path = socket_path
        self._on_notification = on_notification
        self._on_connection_change = on_connection_change or (lambda _c: None)
        self._reconnect_delay = reconnect_delay
        self._request_timeout = request_timeout

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._socket_lock = threading.Lock()
        self._condition = threading.Condition()
        self._request_in_flight = False
        self._response: str | None = None
        self._connected = False

    def start(self) -> None:
        """Start the background connect/read thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="xiaozhi-control-client",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the client and wait for the reader thread to exit."""
        self._stop_event.set()
        self._close_socket()
        with self._condition:
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout)

    def request(self, line: str, timeout: float | None = None) -> str:
        """Send a request line and return the OK response.

        Raises ``ControlUnavailable`` when not connected and
        ``ControlRequestError`` when the server answers with ERR.
        """
        deadline = self._request_timeout if timeout is None else timeout
        with self._condition:
            while self._request_in_flight:
                if not self._condition.wait(timeout=deadline):
                    raise ControlUnavailable("timed out waiting for prior request")
            if not self._connected or self._socket is None:
                raise ControlUnavailable("control socket is not connected")
            self._request_in_flight = True
            self._response = None
            try:
                try:
                    self._socket.sendall(line.encode("utf-8") + b"\n")
                except OSError as exc:
                    raise ControlUnavailable(
                        f"failed to send request: {exc}"
                    ) from exc
                if not self._condition.wait_for(
                    lambda: self._response is not None or not self._connected,
                    timeout=deadline,
                ):
                    raise ControlUnavailable("request timed out")
                if self._response is None:
                    raise ControlUnavailable("control socket disconnected")
                response = self._response
            finally:
                self._request_in_flight = False
                self._condition.notify_all()

        if response.startswith("ERR "):
            parts = response.split(" ", 2)
            code = parts[1]
            message = parts[2] if len(parts) > 2 else ""
            raise ControlRequestError(code, message)
        return response

    @property
    def connected(self) -> bool:
        return self._connected

    def _close_socket(self) -> None:
        with self._socket_lock:
            sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _mark_disconnected(self) -> None:
        was_connected = self._connected
        self._connected = False
        self._close_socket()
        with self._condition:
            self._condition.notify_all()
        if was_connected:
            logger.warning("Control socket disconnected")
            self._on_connection_change(False)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(1.0)
                sock.connect(self._socket_path)
                sock.settimeout(None)
            except OSError:
                try:
                    sock.close()
                except (OSError, UnboundLocalError):
                    pass
                self._stop_event.wait(self._reconnect_delay)
                continue

            with self._socket_lock:
                self._socket = sock
            self._connected = True
            logger.info(f"Connected to control socket: {self._socket_path}")
            self._on_connection_change(True)

            try:
                self._read_loop(sock)
            except OSError:
                pass
            finally:
                self._mark_disconnected()
            self._stop_event.wait(self._reconnect_delay)

    def _read_loop(self, sock: socket.socket) -> None:
        buffer = b""
        while not self._stop_event.is_set():
            chunk = sock.recv(4096)
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                try:
                    line = raw.decode("utf-8").strip()
                except UnicodeDecodeError:
                    logger.warning("Ignoring non-UTF-8 line from control socket")
                    continue
                if line:
                    self._dispatch_line(line)

    def _dispatch_line(self, line: str) -> None:
        if line.startswith("OK ") or line.startswith("ERR ") or line == "OK":
            with self._condition:
                if self._request_in_flight and self._response is None:
                    self._response = line
                    self._condition.notify_all()
                    return
            logger.warning(f"Unmatched control response: {line!r}")
            return
        self._on_notification(line)
