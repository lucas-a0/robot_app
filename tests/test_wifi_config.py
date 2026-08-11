"""Tests for the NetworkManager WiFi provisioning module."""

from __future__ import annotations

import threading

import pytest

from xiaozhi_ble import wifi_config
from xiaozhi_ble.wifi_config import (
    CONNECTION_ID,
    NM_PATH,
    NM_SETTINGS_PATH,
    WifiConfigurator,
    WifiRequestError,
    parse_wifi_request,
)


class FakeDevice:
    def __init__(self, device_type: int, state_reason: tuple[int, int]) -> None:
        self.DeviceType = device_type
        self.StateReason = state_reason


class FakeConnection:
    def __init__(self, connection_id: str) -> None:
        self._settings = {"connection": {"id": connection_id}}
        self.deleted = False

    def GetSettings(self) -> dict:
        return self._settings

    def Delete(self) -> None:
        self.deleted = True


class FakeSettings:
    def __init__(self, connections: dict[str, FakeConnection]) -> None:
        self._connections = connections

    def ListConnections(self) -> list[str]:
        return list(self._connections)


class FakeNetworkManager:
    def __init__(self, devices: list[str]) -> None:
        self._devices = devices
        self.activated: list[tuple[dict, str]] = []

    def GetDevices(self) -> list[str]:
        return self._devices

    def AddAndActivateConnection(self, settings: dict, device: str, specific: str):
        self.activated.append((settings, device))
        return ("/org/freedesktop/NetworkManager/Settings/1", "/active/1")


class FakeBus:
    def __init__(self, proxies: dict[str, object]) -> None:
        self._proxies = proxies

    def get_proxy(self, service: str, path: str, interface_name: str | None = None):
        return self._proxies[path]


def _make_configurator(
    results: list,
    device_state: tuple[int, int] = (100, 0),
    connections: dict[str, FakeConnection] | None = None,
    timeout: float = 30.0,
) -> tuple[WifiConfigurator, FakeNetworkManager]:
    """Build a configurator wired to a fake system bus with one WiFi device."""
    device_path = "/org/freedesktop/NetworkManager/Devices/1"
    nm = FakeNetworkManager([device_path])
    proxies: dict[str, object] = {
        NM_PATH: nm,
        device_path: FakeDevice(2, device_state),
        NM_SETTINGS_PATH: FakeSettings(connections or {}),
    }
    proxies.update(connections or {})
    bus = FakeBus(proxies)
    configurator = WifiConfigurator(
        on_result=lambda code, ssid: results.append((code, ssid)),
        connect_timeout_secs=timeout,
        bus_factory=lambda: bus,
    )
    return configurator, nm


def _wait_results(results: list, count: int = 1) -> None:
    deadline = threading.Event()
    for _ in range(200):
        if len(results) >= count:
            return
        deadline.wait(0.01)
    raise AssertionError(f"timed out waiting for results, got {results!r}")


def test_parse_wifi_request_accepts_ssid_with_spaces_and_open_networks():
    assert parse_wifi_request("MyHome\n12345678") == ("MyHome", "12345678")
    assert parse_wifi_request("Office AP 2F\n12345678") == ("Office AP 2F", "12345678")
    # An empty or omitted password line means an open network.
    assert parse_wifi_request("Open AP\n") == ("Open AP", "")
    assert parse_wifi_request("Open AP") == ("Open AP", "")
    assert parse_wifi_request("  MyHome\n12345678  ") == ("MyHome", "12345678")
    # Non-ASCII SSIDs are measured in UTF-8 bytes (4 chars * 3 bytes <= 32).
    assert parse_wifi_request("家庭网络\n12345678") == ("家庭网络", "12345678")


def test_parse_wifi_request_rejects_bad_input():
    with pytest.raises(WifiRequestError):
        parse_wifi_request("")
    with pytest.raises(WifiRequestError):
        parse_wifi_request("\n12345678")
    with pytest.raises(WifiRequestError):
        parse_wifi_request(f"{'x' * 33}\n12345678")
    with pytest.raises(WifiRequestError):
        parse_wifi_request("MyHome\n1234567")
    with pytest.raises(WifiRequestError):
        parse_wifi_request(f"MyHome\n{'x' * 64}")


def test_connect_reports_connected_and_replaces_old_profile():
    old_profile = FakeConnection(CONNECTION_ID)
    other_profile = FakeConnection("someone-else")
    results: list = []
    configurator, nm = _make_configurator(
        results,
        connections={"/settings/1": old_profile, "/settings/2": other_profile},
    )

    assert configurator.connect("MyHome", "12345678") is None
    _wait_results(results)

    assert results == [("connected", "MyHome")]
    assert old_profile.deleted
    assert not other_profile.deleted
    settings, _device = nm.activated[0]
    assert settings["connection"]["id"].unpack() == CONNECTION_ID
    assert settings["802-11-wireless-security"]["psk"].unpack() == "12345678"


def test_connect_open_network_omits_security_settings():
    results: list = []
    configurator, nm = _make_configurator(results)

    assert configurator.connect("Open AP", "") is None
    _wait_results(results)

    assert results == [("connected", "Open AP")]
    settings, _device = nm.activated[0]
    assert "802-11-wireless-security" not in settings


@pytest.mark.parametrize(
    "state_reason, expected",
    [
        ((120, 7), "auth"),  # NO_SECRETS
        ((120, 8), "auth"),  # SUPPLICANT_DISCONNECT
        ((120, 53), "not_found"),  # SSID_NOT_FOUND
        ((120, 1), "failed"),
    ],
)
def test_connect_maps_failure_reasons(state_reason, expected):
    results: list = []
    configurator, _nm = _make_configurator(results, device_state=state_reason)

    assert configurator.connect("MyHome", "12345678") is None
    _wait_results(results)

    assert results == [(expected, "MyHome")]


def test_connect_reports_timeout():
    results: list = []
    configurator, _nm = _make_configurator(
        results, device_state=(60, 0), timeout=0.2
    )

    assert configurator.connect("MyHome", "12345678") is None
    _wait_results(results)

    assert results == [("timeout", "MyHome")]


def test_connect_unavailable_without_wifi_device():
    results: list = []
    bus = FakeBus({NM_PATH: FakeNetworkManager([])})
    configurator = WifiConfigurator(
        on_result=lambda code, ssid: results.append((code, ssid)),
        bus_factory=lambda: bus,
    )

    assert configurator.connect("MyHome", "12345678") == "unavailable"
    assert results == []


def test_connect_unavailable_when_networkmanager_is_missing():
    results: list = []

    def broken_factory():
        raise RuntimeError("no system bus")

    configurator = WifiConfigurator(
        on_result=lambda code, ssid: results.append((code, ssid)),
        bus_factory=broken_factory,
    )

    assert configurator.connect("MyHome", "12345678") == "unavailable"
    assert results == []


def test_connect_rejects_concurrent_attempts(monkeypatch):
    results: list = []
    configurator, _nm = _make_configurator(results)
    blocker = threading.Event()

    def blocking_activate(ssid, password, device_path):
        blocker.wait(5)
        return "connected"

    monkeypatch.setattr(configurator, "_activate", blocking_activate)
    try:
        assert configurator.connect("First", "12345678") is None
        assert configurator.connect("Second", "12345678") == "busy"
    finally:
        blocker.set()
    _wait_results(results)
    assert results == [("connected", "First")]
