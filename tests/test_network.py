"""Tests for the WiFi network status provider."""

import time

import xiaozhi_ble.network as network
from xiaozhi_ble.network import (
    NetworkProvider,
    detect_wifi_interface,
    parse_proc_net_wireless,
)

_PROC_NET_WIRELESS = """Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
 wlan0: 0000   70.  -38.  -256        0      0      0      0   1391        0
"""


def test_parse_proc_net_wireless_reads_signal_level():
    assert parse_proc_net_wireless(_PROC_NET_WIRELESS, "wlan0") == -38
    assert parse_proc_net_wireless(_PROC_NET_WIRELESS, "wlan1") is None
    assert parse_proc_net_wireless("", "wlan0") is None


def test_detect_wifi_interface_prefers_wlan(tmp_path):
    (tmp_path / "eth0").mkdir()
    (tmp_path / "wlan0").mkdir()
    assert detect_wifi_interface(tmp_path) == "wlan0"


def test_detect_wifi_interface_falls_back_to_wireless_dir(tmp_path):
    (tmp_path / "wlp3s0").mkdir()
    (tmp_path / "wlp3s0" / "wireless").mkdir()
    assert detect_wifi_interface(tmp_path) == "wlp3s0"


def test_detect_wifi_interface_none(tmp_path):
    (tmp_path / "eth0").mkdir()
    assert detect_wifi_interface(tmp_path) is None
    assert detect_wifi_interface(tmp_path / "missing") is None


def _stub_readers(monkeypatch, essids, signal=-38, ipv4="172.16.0.195"):
    values = iter(essids)
    monkeypatch.setattr(network, "read_essid", lambda _iface: next(values))
    monkeypatch.setattr(network, "read_signal_dbm", lambda _iface: signal)
    monkeypatch.setattr(network, "read_ipv4", lambda _iface: ipv4)


def test_provider_reports_only_changes(monkeypatch):
    updates = []
    provider = NetworkProvider(
        "wlan0", lambda ssid, rssi, ip: updates.append((ssid, rssi, ip))
    )
    assert provider.enabled

    _stub_readers(monkeypatch, ["MyHome", "MyHome", None])

    provider._poll_once()
    provider._poll_once()  # unchanged: no callback
    provider._poll_once()

    assert updates == [("MyHome", -38, "172.16.0.195"), (None, None, None)]


def test_provider_reports_ip_change(monkeypatch):
    updates = []
    provider = NetworkProvider(
        "wlan0", lambda ssid, rssi, ip: updates.append((ssid, rssi, ip))
    )
    monkeypatch.setattr(network, "read_essid", lambda _iface: "MyHome")
    monkeypatch.setattr(network, "read_signal_dbm", lambda _iface: -38)
    ips = iter(["172.16.0.195", "172.16.0.200"])
    monkeypatch.setattr(network, "read_ipv4", lambda _iface: next(ips))

    provider._poll_once()
    provider._poll_once()

    assert updates == [
        ("MyHome", -38, "172.16.0.195"),
        ("MyHome", -38, "172.16.0.200"),
    ]


def test_provider_disabled_without_interface(monkeypatch):
    monkeypatch.setattr(network, "detect_wifi_interface", lambda: None)
    provider = NetworkProvider("", lambda _s, _r, _i: None)
    assert not provider.enabled
    provider.start()
    assert provider._thread is None


def test_provider_poll_loop_starts_and_stops(monkeypatch):
    updates = []
    provider = NetworkProvider(
        "wlan0",
        lambda ssid, rssi, ip: updates.append((ssid, rssi, ip)),
        poll_interval_secs=0.05,
    )
    monkeypatch.setattr(network, "read_essid", lambda _iface: "MyHome")
    monkeypatch.setattr(network, "read_signal_dbm", lambda _iface: -38)
    monkeypatch.setattr(network, "read_ipv4", lambda _iface: "172.16.0.195")

    provider.start()
    try:
        deadline = time.monotonic() + 5.0
        while not updates and time.monotonic() < deadline:
            time.sleep(0.01)
        assert updates == [("MyHome", -38, "172.16.0.195")]
    finally:
        provider.stop()
    assert provider._thread is None
