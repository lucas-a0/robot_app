"""Tests for the bridge wiring in main.py."""

from xiaozhi_ble.config import BridgeConfig
from xiaozhi_ble.main import _Bridge


class _StubServer:
    def __init__(self) -> None:
        self.lan = []
        self.audio = []
        self.network = []
        self.battery = []

    def update_lan_endpoint(self, text: str) -> None:
        self.lan.append(text)

    def update_audio_endpoint(self, text: str) -> None:
        self.audio.append(text)

    def notify_network_status(self, ssid, rssi, ipv4) -> None:
        self.network.append((ssid, rssi, ipv4))

    def notify_battery_status(self, percentage, supply_status) -> None:
        self.battery.append((percentage, supply_status))


class _StubLan:
    def __init__(self) -> None:
        self.lan = []
        self.audio = []
        self.network = []
        self.battery = []

    def set_lan_endpoint(self, text: str) -> None:
        self.lan.append(text)

    def set_audio_endpoint(self, text: str) -> None:
        self.audio.append(text)

    def notify_network_status(self, ssid, rssi, ipv4) -> None:
        self.network.append((ssid, rssi, ipv4))

    def notify_battery_status(self, percentage, supply_status) -> None:
        self.battery.append((percentage, supply_status))


def _make_bridge(tmp_path) -> _Bridge:
    path = tmp_path / "config.yaml"
    path.write_text(
        "battery:\n  topic: ''\n"
        "lan:\n  host: 127.0.0.1\n  port: 4205\n"
    )
    config = BridgeConfig(str(path))
    assert config.load()
    bridge = _Bridge(config)
    bridge._server = _StubServer()
    bridge._lan = _StubLan()
    return bridge


def test_network_update_publishes_lan_endpoint(tmp_path):
    bridge = _make_bridge(tmp_path)

    bridge._on_network("MyHome", -38, "192.168.1.12")

    assert bridge._server.network == [("MyHome", -38, "192.168.1.12")]
    assert bridge._lan.network == [("MyHome", -38, "192.168.1.12")]
    assert bridge._server.lan == ["192.168.1.12 4205"]
    assert bridge._lan.lan == ["192.168.1.12 4205"]
    # Audio stays UNKNOWN until the PCM query reports a port.
    assert bridge._server.audio == ["UNKNOWN"]
    assert bridge._lan.audio == ["UNKNOWN"]


def test_audio_port_combines_with_cached_ipv4(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._on_network("MyHome", -40, "10.0.0.8")
    bridge._on_audio_port(4203)

    assert bridge._server.audio[-1] == "10.0.0.8 4203"
    assert bridge._lan.audio[-1] == "10.0.0.8 4203"

    bridge._on_audio_port(None)
    assert bridge._server.audio[-1] == "UNKNOWN"


def test_battery_fans_out_to_both_transports(tmp_path):
    bridge = _make_bridge(tmp_path)
    bridge._on_battery(0.67, "CHARGING")
    assert bridge._server.battery == [(0.67, "CHARGING")]
    assert bridge._lan.battery == [(0.67, "CHARGING")]
