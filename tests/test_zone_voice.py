"""Tests for the zone_voice_player JSON Lines client.

A threaded fake Unix-socket server speaks the player protocol so the
controller can be exercised without a real zone_voice_player or ffplay.
"""

from __future__ import annotations

import json
import os
import socket
import threading

import pytest

from xiaozhi_ble.zone_voice import (
    ZoneVoiceController,
    _map_error,
    _status_text_from_response,
)

class _FakePlayer:
    """JSON Lines zone_voice_player stand-in for tests."""

    def __init__(self, socket_path: str) -> None:
        self._socket_path = socket_path
        self.requests: list[dict] = []
        self.audios = [
            "charging_zone.mp3",
            "equipment_zone.mp3",
            "mowing_zone.mp3",
            "pool_zone.mp3",
        ]
        self.state = "idle"
        self.audio: str | None = None
        self.play_error: str | None = None
        self.hang = False
        self._lock = threading.Lock()
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(socket_path)
        self._listener.listen()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._listener.close()
        self._thread.join(timeout=2)
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
            threading.Thread(
                target=self._serve_client, args=(conn,), daemon=True
            ).start()

    def _serve_client(self, conn: socket.socket) -> None:
        with conn:
            if self.hang:
                self._stop.wait()
                return
            buf = b""
            while True:
                while b"\n" not in buf:
                    try:
                        chunk = conn.recv(4096)
                    except OSError:
                        return
                    if not chunk:
                        return
                    buf += chunk
                line, buf = buf.split(b"\n", 1)
                try:
                    req = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    resp = {"ok": False, "error": "bad_request", "message": "bad json"}
                else:
                    with self._lock:
                        self.requests.append(req)
                        resp = self._dispatch(req)
                try:
                    conn.sendall(
                        (json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8")
                    )
                except OSError:
                    return

    def _dispatch(self, req: dict) -> dict:
        cmd = req.get("cmd")
        if cmd == "list":
            return {"ok": True, "audios": list(self.audios)}
        if cmd == "play":
            if self.play_error:
                return {
                    "ok": False,
                    "error": self.play_error,
                    "message": "nope",
                }
            audio = req.get("audio")
            if audio not in self.audios:
                return {
                    "ok": False,
                    "error": "audio_not_found",
                    "message": f"missing {audio}",
                }
            self.state = "playing"
            self.audio = audio
            return {"ok": True, "state": "playing", "audio": audio}
        if cmd == "stop":
            stopped = self.audio
            self.state = "idle"
            self.audio = None
            return {"ok": True, "state": "idle", "stopped": stopped}
        if cmd == "status":
            if self.state == "playing":
                return {"ok": True, "state": "playing", "audio": self.audio}
            return {"ok": True, "state": "idle", "audio": None}
        return {"ok": False, "error": "unknown_cmd", "message": str(cmd)}


@pytest.fixture
def fake_player(tmp_path):
    path = str(tmp_path / "zone_voice.sock")
    server = _FakePlayer(path)
    yield server
    server.close()


def _controller(fake_player, on_notify=None, **kwargs) -> ZoneVoiceController:
    return ZoneVoiceController(
        socket_path=fake_player._socket_path,
        on_notify=on_notify,
        request_timeout_secs=kwargs.get("request_timeout_secs", 0.5),
        poll_interval_secs=kwargs.get("poll_interval_secs", 10.0),
    )


def test_list_play_status_stop(fake_player):
    controller = _controller(fake_player)

    listed = controller.execute("LIST")
    assert listed.startswith("LIST ")
    assert "pool_zone.mp3" in listed.split()
    assert listed.split()[1:] == fake_player.audios

    assert controller.execute("PLAY pool_zone.mp3") == "PLAYING pool_zone.mp3"
    assert controller.execute("status") == "PLAYING pool_zone.mp3"
    assert controller.execute("STOP") == "IDLE"
    assert controller.execute("STATUS") == "IDLE"

    play_req = next(req for req in fake_player.requests if req.get("cmd") == "play")
    assert play_req == {
        "cmd": "play",
        "audio": "pool_zone.mp3",
        "interrupt": True,
    }


def test_play_preserves_audio_filename_case(fake_player):
    fake_player.audios.append("Pool_Zone.mp3")
    controller = _controller(fake_player)

    assert controller.execute("PLAY Pool_Zone.mp3") == "PLAYING Pool_Zone.mp3"
    play_req = next(req for req in fake_player.requests if req.get("cmd") == "play")
    assert play_req["audio"] == "Pool_Zone.mp3"
    assert play_req["interrupt"] is True


def test_play_unknown_audio(fake_player):
    controller = _controller(fake_player)
    assert controller.execute("PLAY missing.wav") == "ERR audio missing.wav"


def test_play_failed(fake_player):
    fake_player.play_error = "play_failed"
    controller = _controller(fake_player)
    assert controller.execute("PLAY pool_zone.mp3") == "ERR failed"


def test_unknown_and_malformed_commands(fake_player):
    controller = _controller(fake_player)
    assert controller.execute("") == "ERR command"
    assert controller.execute("pause") == "ERR command"
    assert controller.execute("LIST extra") == "ERR command"
    assert controller.execute("STOP extra") == "ERR command"
    assert controller.execute("STATUS extra") == "ERR command"
    assert controller.execute("PLAY") == "ERR command"


def test_disabled_without_socket_path():
    controller = ZoneVoiceController(socket_path="")
    assert not controller.enabled
    controller.start()
    assert controller.execute("LIST") == "ERR unavailable"
    assert controller.execute("PLAY pool_zone.mp3") == "ERR unavailable"
    assert controller.execute("STOP") == "ERR unavailable"
    assert controller.execute("STATUS") == "UNKNOWN"
    controller.stop()


def test_missing_socket_is_unavailable(tmp_path):
    controller = ZoneVoiceController(
        socket_path=str(tmp_path / "nope.sock"),
        request_timeout_secs=0.2,
    )
    assert controller.execute("LIST") == "ERR unavailable"
    assert controller.execute("PLAY pool_zone.mp3") == "ERR unavailable"
    assert controller.execute("STOP") == "ERR unavailable"
    assert controller.execute("STATUS") == "UNKNOWN"


def test_request_timeout(fake_player):
    fake_player.hang = True
    controller = _controller(fake_player, request_timeout_secs=0.2)
    assert controller.execute("LIST") == "ERR unavailable"


def test_poller_notifies_only_on_change(fake_player):
    updates = []
    controller = _controller(fake_player, on_notify=updates.append)
    fake_player.state = "playing"
    fake_player.audio = "mowing_zone.mp3"

    controller._poll_once()
    controller._poll_once()
    assert updates == ["PLAYING mowing_zone.mp3"]

    fake_player.state = "idle"
    fake_player.audio = None
    controller._poll_once()
    assert updates == ["PLAYING mowing_zone.mp3", "IDLE"]

    controller._poll_once()
    assert updates == ["PLAYING mowing_zone.mp3", "IDLE"]


def test_execute_status_suppresses_duplicate_poll(fake_player):
    updates = []
    controller = _controller(fake_player, on_notify=updates.append)
    fake_player.state = "playing"
    fake_player.audio = "pool_zone.mp3"

    assert controller.execute("STATUS") == "PLAYING pool_zone.mp3"
    controller._poll_once()
    assert updates == []


def test_start_stop_are_idempotent(fake_player):
    controller = _controller(fake_player, poll_interval_secs=0.05)
    controller.start()
    controller.start()
    thread = controller._thread
    assert thread is not None and thread.is_alive()
    controller.stop(timeout=1.0)
    controller.stop(timeout=1.0)
    assert controller._thread is None


def test_map_error_codes():
    assert _map_error("audio_not_found", "x.mp3") == "ERR audio x.mp3"
    assert _map_error("audio_not_found") == "ERR audio"
    assert _map_error("play_failed") == "ERR failed"
    assert _map_error("bad_request") == "ERR command"
    assert _map_error("unknown_cmd") == "ERR command"
    assert _map_error("internal_error") == "ERR internal"


def test_status_text_from_response():
    assert _status_text_from_response({"state": "idle", "audio": None}) == "IDLE"
    assert (
        _status_text_from_response({"state": "playing", "audio": "pool_zone.mp3"})
        == "PLAYING pool_zone.mp3"
    )
    assert (
        _status_text_from_response({"state": "busy", "audio": "pool_zone.mp3"})
        == "PLAYING pool_zone.mp3"
    )
