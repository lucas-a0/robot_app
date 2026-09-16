"""Client for the zone_voice_player Unix-socket control protocol.

The App writes LIST/PLAY/STOP/STATUS to the Zone Voice BLE characteristic;
this module translates those plain-text commands into JSON Lines requests
against zone_voice_player and returns the BLE reply text. A background
poller watches playback status so the App is notified when audio finishes
or when ROS-triggered playback starts.

The module does not know about GATT; the bridge wires :meth:`execute` and
``on_notify`` in main.py.
"""

from __future__ import annotations

import json
import socket
import threading
from typing import Callable

from loguru import logger

# Receives a BLE notify text, e.g. "PLAYING pool_zone.mp3" or "IDLE".
ZoneVoiceNotifyCallback = Callable[[str], None]

_DEFAULT_SOCKET_PATH = "/tmp/zone_voice_player.sock"
_MAX_RESPONSE_BYTES = 8192


class _Unavailable(Exception):
    """The zone_voice_player socket is missing, timed out, or dropped."""


class _ProtocolError(Exception):
    """The peer returned a malformed JSON Lines response."""


def _status_text_from_response(resp: dict) -> str:
    """Map a successful ``status``/``play`` JSON object to BLE status text."""
    state = resp.get("state")
    if state == "idle":
        return "IDLE"
    if state in ("playing", "busy"):
        audio = resp.get("audio")
        if isinstance(audio, str) and audio:
            return f"PLAYING {audio}"
        return "IDLE"
    raise _ProtocolError(f"unexpected state {state!r}")


def _map_error(error: str, audio: str | None = None) -> str:
    """Map a downstream JSON error code to a BLE ``ERR ...`` reply."""
    if error == "audio_not_found":
        return f"ERR audio {audio}" if audio else "ERR audio"
    if error == "play_failed":
        return "ERR failed"
    if error in (
        "unknown_cmd",
        "missing_field",
        "invalid_field",
        "bad_request",
        "too_large",
    ):
        return "ERR command"
    return "ERR internal"


class ZoneVoiceController:
    """Translate BLE Zone Voice writes into zone_voice_player requests.

    An empty ``socket_path`` disables the controller. Requests are
    connect-per-call (JSON Lines, one request / one response) and serialized
    so GATT writes and the status poller never share a socket. ``execute()``
    is safe to call from the GLib thread: a localhost Unix connect is fast.
    """

    def __init__(
        self,
        socket_path: str = _DEFAULT_SOCKET_PATH,
        on_notify: ZoneVoiceNotifyCallback | None = None,
        request_timeout_secs: float = 2.0,
        poll_interval_secs: float = 1.0,
    ) -> None:
        self._socket_path = socket_path
        self._on_notify = on_notify or (lambda _text: None)
        self._request_timeout_secs = request_timeout_secs
        self._poll_interval_secs = poll_interval_secs
        self._lock = threading.Lock()
        self._last_notified: str | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._socket_path.strip())

    def start(self) -> None:
        """Start the status poller (no-op when disabled or already running)."""
        if not self.enabled:
            logger.info("Zone voice disabled: zone_voice.socket_path is empty")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._last_notified = None
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="xiaozhi-zone-voice",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            f"Zone voice started: socket={self._socket_path} "
            f"interval={self._poll_interval_secs}s"
        )

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the status poller."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def execute(self, text: str) -> str:
        """Handle a Zone Voice write; returns the immediate reply text."""
        stripped = text.strip()
        if not stripped:
            return "ERR command"
        parts = stripped.split(None, 1)
        verb = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""
        if verb == "list":
            if rest:
                return "ERR command"
            return self._list()
        if verb == "play":
            if not rest:
                return "ERR command"
            return self._play(rest)
        if verb == "stop":
            if rest:
                return "ERR command"
            return self._stop_playback()
        if verb == "status":
            if rest:
                return "ERR command"
            return self._status()
        return "ERR command"

    def _list(self) -> str:
        if not self.enabled:
            return "ERR unavailable"
        with self._lock:
            try:
                resp = self._request_locked({"cmd": "list"})
            except _Unavailable:
                return "ERR unavailable"
            except _ProtocolError:
                logger.exception("Zone voice list returned a malformed response")
                return "ERR internal"
        if not resp.get("ok"):
            return _map_error(str(resp.get("error") or ""))
        audios = resp.get("audios")
        if not isinstance(audios, list):
            return "ERR internal"
        names = [name for name in audios if isinstance(name, str)]
        return "LIST" if not names else "LIST " + " ".join(names)

    def _play(self, audio: str) -> str:
        if not self.enabled:
            return "ERR unavailable"
        with self._lock:
            try:
                resp = self._request_locked(
                    {"cmd": "play", "audio": audio, "interrupt": True}
                )
            except _Unavailable:
                return "ERR unavailable"
            except _ProtocolError:
                logger.exception("Zone voice play returned a malformed response")
                return "ERR internal"
            if not resp.get("ok"):
                return _map_error(str(resp.get("error") or ""), audio=audio)
            try:
                reply = _status_text_from_response(resp)
            except _ProtocolError:
                logger.exception("Zone voice play returned an unexpected state")
                return "ERR internal"
            self._last_notified = reply
            return reply

    def _stop_playback(self) -> str:
        if not self.enabled:
            return "ERR unavailable"
        with self._lock:
            try:
                resp = self._request_locked({"cmd": "stop"})
            except _Unavailable:
                return "ERR unavailable"
            except _ProtocolError:
                logger.exception("Zone voice stop returned a malformed response")
                return "ERR internal"
            if not resp.get("ok"):
                return _map_error(str(resp.get("error") or ""))
            self._last_notified = "IDLE"
            return "IDLE"

    def _status(self) -> str:
        with self._lock:
            text = self._query_status_locked()
            self._last_notified = text
            return text

    def _query_status_locked(self) -> str:
        """Return PLAYING/IDLE/UNKNOWN. Caller must hold ``_lock``."""
        if not self.enabled:
            return "UNKNOWN"
        try:
            resp = self._request_locked({"cmd": "status"})
        except _Unavailable:
            return "UNKNOWN"
        except _ProtocolError:
            logger.exception("Zone voice status returned a malformed response")
            return "UNKNOWN"
        if not resp.get("ok"):
            return "UNKNOWN"
        try:
            return _status_text_from_response(resp)
        except _ProtocolError:
            return "UNKNOWN"

    def _request_locked(self, payload: dict) -> dict:
        raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self._request_timeout_secs)
        try:
            try:
                sock.connect(self._socket_path)
                sock.sendall(raw)
            except OSError as exc:
                raise _Unavailable(f"failed to send request: {exc}") from exc
            buf = b""
            while b"\n" not in buf:
                try:
                    chunk = sock.recv(4096)
                except OSError as exc:
                    raise _Unavailable(f"failed to read response: {exc}") from exc
                if not chunk:
                    raise _Unavailable("connection closed before response")
                buf += chunk
                if len(buf) > _MAX_RESPONSE_BYTES:
                    raise _ProtocolError("response exceeded size limit")
            line = buf.split(b"\n", 1)[0]
            try:
                resp = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise _ProtocolError(f"response is not JSON: {exc}") from exc
            if not isinstance(resp, dict):
                raise _ProtocolError("response is not a JSON object")
            return resp
        finally:
            sock.close()

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                logger.warning(f"Zone voice status poll error: {exc}")
            self._stop_event.wait(self._poll_interval_secs)

    def _poll_once(self) -> None:
        with self._lock:
            text = self._query_status_locked()
            if text == self._last_notified:
                return
            self._last_notified = text
        self._on_notify(text)
