"""Tests for the BLE GATT implementation."""

from xiaozhi_ble.gatt_server import ADVERTISEMENT_PATH
from xiaozhi_ble.gatt_server import AUDIO_ENDPOINT_CHAR_UUID
from xiaozhi_ble.gatt_server import AUDIO_ENDPOINT_CHARACTERISTIC_PATH
from xiaozhi_ble.gatt_server import AudioEndpointCharacteristic
from xiaozhi_ble.gatt_server import BANDWIDTH_CHAR_UUID
from xiaozhi_ble.gatt_server import BANDWIDTH_CHARACTERISTIC_CCCD_PATH
from xiaozhi_ble.gatt_server import BATTERY_STATUS_CHAR_UUID
from xiaozhi_ble.gatt_server import BATTERY_STATUS_CHARACTERISTIC_CCCD_PATH
from xiaozhi_ble.gatt_server import CCCD_UUID
from xiaozhi_ble.gatt_server import CMD_VEL_CHAR_UUID
from xiaozhi_ble.gatt_server import CMD_VEL_CHARACTERISTIC_CCCD_PATH
from xiaozhi_ble.gatt_server import CmdVelCharacteristic
from xiaozhi_ble.gatt_server import CPU_CHAR_UUID
from xiaozhi_ble.gatt_server import CPU_CHARACTERISTIC_CCCD_PATH
from xiaozhi_ble.gatt_server import NETWORK_CHAR_UUID
from xiaozhi_ble.gatt_server import NETWORK_CHARACTERISTIC_CCCD_PATH
from xiaozhi_ble.gatt_server import BatteryStatusCharacteristic
from xiaozhi_ble.gatt_server import BandwidthStatusCharacteristic
from xiaozhi_ble.gatt_server import BleControlServer
from xiaozhi_ble.gatt_server import MEMORY_CHAR_UUID
from xiaozhi_ble.gatt_server import MEMORY_CHARACTERISTIC_CCCD_PATH
from xiaozhi_ble.gatt_server import MemoryStatusCharacteristic
from xiaozhi_ble.gatt_server import WIFI_CONFIG_CHARACTERISTIC_CCCD_PATH
from xiaozhi_ble.gatt_server import WIFI_CONFIG_CHAR_UUID
from xiaozhi_ble.gatt_server import WifiConfigCharacteristic
from xiaozhi_ble.gatt_server import ControlAdvertisement
from xiaozhi_ble.gatt_server import CpuStatusCharacteristic
from xiaozhi_ble.gatt_server import GattApplication
from xiaozhi_ble.gatt_server import INITIAL_POSE_CHAR_UUID
from xiaozhi_ble.gatt_server import INITIAL_POSE_CHARACTERISTIC_CCCD_PATH
from xiaozhi_ble.gatt_server import InitialPoseCharacteristic
from xiaozhi_ble.gatt_server import LAN_ENDPOINT_CHAR_UUID
from xiaozhi_ble.gatt_server import LAN_ENDPOINT_CHARACTERISTIC_PATH
from xiaozhi_ble.gatt_server import LanEndpointCharacteristic
from xiaozhi_ble.gatt_server import NetworkStatusCharacteristic
from xiaozhi_ble.gatt_server import ROBOT_CONTROL_CHAR_UUID
from xiaozhi_ble.gatt_server import ROBOT_CONTROL_CHARACTERISTIC_CCCD_PATH
from xiaozhi_ble.gatt_server import RobotControlCharacteristic
from xiaozhi_ble.gatt_server import ZONE_NAV_CHAR_UUID
from xiaozhi_ble.gatt_server import ZONE_NAV_CHARACTERISTIC_CCCD_PATH
from xiaozhi_ble.gatt_server import ZoneNavCharacteristic
from xiaozhi_ble.gatt_server import ZONE_VOICE_CHAR_UUID
from xiaozhi_ble.gatt_server import ZONE_VOICE_CHARACTERISTIC_CCCD_PATH
from xiaozhi_ble.gatt_server import ZoneVoiceCharacteristic


def _unpack_properties(properties: dict) -> dict:
    return {key: value.unpack() for key, value in properties.items()}


def test_lan_endpoint_characteristic_is_read_only():
    characteristic = LanEndpointCharacteristic()
    assert characteristic.Flags == ["read"]
    assert characteristic.Descriptors == []
    assert bytes(characteristic.Value).decode("utf-8") == "UNKNOWN"

    characteristic.update_endpoint("192.168.1.12 4205")
    assert bytes(characteristic.Value).decode("utf-8") == "192.168.1.12 4205"


def test_audio_endpoint_characteristic_is_read_only():
    characteristic = AudioEndpointCharacteristic()
    assert characteristic.Flags == ["read"]
    assert characteristic.Descriptors == []
    assert bytes(characteristic.Value).decode("utf-8") == "UNKNOWN"

    characteristic.update_endpoint("192.168.1.12 4203")
    assert bytes(characteristic.Value).decode("utf-8") == "192.168.1.12 4203"


def test_battery_status_characteristic_formats_percentage_and_supply_status():
    characteristic = BatteryStatusCharacteristic()
    assert characteristic.Flags == ["read", "notify"]
    assert characteristic.Descriptors == [BATTERY_STATUS_CHARACTERISTIC_CCCD_PATH]
    assert bytes(characteristic.Value).decode("utf-8") == "UNKNOWN"

    characteristic.update_status(0.6700000166893005, "CHARGING")

    assert bytes(characteristic.Value).decode("utf-8") == "0.670 CHARGING"

    characteristic.update_status(0.98, None)

    assert bytes(characteristic.Value).decode("utf-8") == "0.980 UNKNOWN"

    characteristic.update_status(None, None)

    assert bytes(characteristic.Value).decode("utf-8") == "UNKNOWN"


def test_network_status_characteristic_formats_wifi_states():
    characteristic = NetworkStatusCharacteristic()
    assert characteristic.Flags == ["read", "notify"]
    assert characteristic.Descriptors == [NETWORK_CHARACTERISTIC_CCCD_PATH]
    assert bytes(characteristic.Value).decode("utf-8") == "UNKNOWN"

    characteristic.update_status(None, None, None)

    assert bytes(characteristic.Value).decode("utf-8") == "DISCONNECTED"

    characteristic.update_status("MyHome", -38, "172.16.0.195")

    assert bytes(characteristic.Value).decode("utf-8") == "WIFI -38 172.16.0.195 MyHome"

    characteristic.update_status("Office AP 2F", None, None)

    assert bytes(characteristic.Value).decode("utf-8") == "WIFI - - Office AP 2F"


def test_cpu_status_characteristic_formats_usage():
    characteristic = CpuStatusCharacteristic()
    assert characteristic.Flags == ["read", "notify"]
    assert characteristic.Descriptors == [CPU_CHARACTERISTIC_CCCD_PATH]
    assert bytes(characteristic.Value).decode("utf-8") == "UNKNOWN"

    characteristic.update_usage(23.456)

    assert bytes(characteristic.Value).decode("utf-8") == "CPU 23.5"

    characteristic.update_usage(None)

    assert bytes(characteristic.Value).decode("utf-8") == "UNKNOWN"


def test_memory_status_characteristic_formats_usage():
    characteristic = MemoryStatusCharacteristic()
    assert characteristic.Flags == ["read", "notify"]
    assert characteristic.Descriptors == [MEMORY_CHARACTERISTIC_CCCD_PATH]
    assert bytes(characteristic.Value).decode("utf-8") == "UNKNOWN"

    characteristic.update_usage(2145, 7872, 27.234)

    assert bytes(characteristic.Value).decode("utf-8") == "MEM 2145 7872 27.2"

    characteristic.update_usage(None, None, None)

    assert bytes(characteristic.Value).decode("utf-8") == "UNKNOWN"


def test_bandwidth_status_characteristic_formats_rates():
    characteristic = BandwidthStatusCharacteristic()
    assert characteristic.Flags == ["read", "notify"]
    assert characteristic.Descriptors == [BANDWIDTH_CHARACTERISTIC_CCCD_PATH]
    assert bytes(characteristic.Value).decode("utf-8") == "UNKNOWN"

    characteristic.update_bandwidth(1234.56, 56.78)

    assert bytes(characteristic.Value).decode("utf-8") == "BANDWIDTH 1234.6 56.8"

    characteristic.update_bandwidth(None, None)

    assert bytes(characteristic.Value).decode("utf-8") == "UNKNOWN"


def test_robot_control_characteristic_dispatches_commands():
    calls = []

    def callback(command: str) -> str | None:
        calls.append(command)
        return None

    characteristic = RobotControlCharacteristic(callback)
    assert characteristic.Flags == ["read", "write", "notify"]
    assert characteristic.Descriptors == [ROBOT_CONTROL_CHARACTERISTIC_CCCD_PATH]

    characteristic.StartNotify()
    characteristic.WriteValue(list(b"  Stand_Up "), {})

    # Accepted commands stay silent.
    assert calls == ["stand_up"]
    assert bytes(characteristic.Value) == b""

    # An asynchronous failure is notified later.
    characteristic.report_error("ERR failed stand_up low battery")
    assert bytes(characteristic.Value).decode("utf-8") == (
        "ERR failed stand_up low battery"
    )


def test_robot_control_characteristic_relays_immediate_errors():
    def callback(command: str) -> str | None:
        return "ERR command" if command == "dance" else "ERR unavailable"

    characteristic = RobotControlCharacteristic(callback)
    characteristic.StartNotify()

    characteristic.WriteValue([0xff], {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR encoding"

    characteristic.WriteValue(list(b"dance"), {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR command"

    characteristic.WriteValue(list(b"stand_up"), {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR unavailable"

    def broken(command: str) -> str | None:
        raise RuntimeError("dispatch blew up")

    characteristic = RobotControlCharacteristic(broken)
    characteristic.StartNotify()
    characteristic.WriteValue(list(b"stand_up"), {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR internal"


def test_zone_nav_characteristic_dispatches_zones():
    calls = []

    def callback(zone: str) -> str:
        calls.append(zone)
        if zone == "garage_zone":
            return "ERR command"
        return f"OK {zone}"

    characteristic = ZoneNavCharacteristic(callback)
    assert characteristic.Flags == ["read", "write", "notify"]
    assert characteristic.Descriptors == [ZONE_NAV_CHARACTERISTIC_CCCD_PATH]

    characteristic.StartNotify()
    characteristic.WriteValue(list(b"  Charging_Zone \n"), {})
    assert calls == ["charging_zone"]
    assert bytes(characteristic.Value).decode("utf-8") == "OK charging_zone"

    characteristic.WriteValue(list(b"garage_zone"), {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR command"

    characteristic.WriteValue([0xff], {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR encoding"

    characteristic.WriteValue(list(b"  "), {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR command"

    def broken(zone: str) -> str:
        raise RuntimeError("dispatch blew up")

    characteristic = ZoneNavCharacteristic(broken)
    characteristic.StartNotify()
    characteristic.WriteValue(list(b"mowing_zone"), {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR internal"


def test_zone_voice_characteristic_dispatches_commands():
    calls = []

    def callback(text: str) -> str:
        calls.append(text)
        if text.lower() == "status":
            return "IDLE"
        if text.lower() == "list":
            return "LIST pool_zone.mp3"
        if text.lower().startswith("play "):
            return f"PLAYING {text.split(None, 1)[1]}"
        if text.lower() == "stop":
            return "IDLE"
        return "ERR command"

    characteristic = ZoneVoiceCharacteristic(callback)
    assert characteristic.Flags == ["read", "write", "notify"]
    assert characteristic.Descriptors == [ZONE_VOICE_CHARACTERISTIC_CCCD_PATH]
    # Construction queries the current status for the cached value.
    assert calls == ["status"]
    assert bytes(characteristic.Value).decode("utf-8") == "IDLE"

    characteristic.StartNotify()
    assert calls[-1] == "status"
    assert bytes(characteristic.Value).decode("utf-8") == "IDLE"

    characteristic.WriteValue(list(b"  LIST \n"), {})
    assert calls[-1] == "LIST"
    assert bytes(characteristic.Value).decode("utf-8") == "LIST pool_zone.mp3"

    characteristic.WriteValue(list(b"PLAY Pool_Zone.mp3"), {})
    assert calls[-1] == "PLAY Pool_Zone.mp3"
    assert bytes(characteristic.Value).decode("utf-8") == "PLAYING Pool_Zone.mp3"

    characteristic.WriteValue(list(b"STOP"), {})
    assert bytes(characteristic.Value).decode("utf-8") == "IDLE"

    characteristic.WriteValue(list(b"pause"), {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR command"

    characteristic.WriteValue([0xFF], {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR encoding"

    characteristic.WriteValue(list(b"  "), {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR command"

    def broken(text: str) -> str:
        raise RuntimeError("dispatch blew up")

    characteristic = ZoneVoiceCharacteristic(broken)
    # Construction already swallowed the status query exception.
    characteristic.StartNotify()
    characteristic.WriteValue(list(b"LIST"), {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR internal"


def test_cmd_vel_characteristic_dispatches_velocities():
    calls = []

    def callback(text: str) -> str:
        calls.append(text)
        if text == "fast 0.0":
            return "ERR command"
        return f"OK {text}"

    characteristic = CmdVelCharacteristic(callback)
    assert characteristic.Flags == ["read", "write", "notify"]
    assert characteristic.Descriptors == [CMD_VEL_CHARACTERISTIC_CCCD_PATH]

    characteristic.StartNotify()
    characteristic.WriteValue(list(b"  -0.30 0.0 \n"), {})
    assert calls == ["-0.30 0.0"]
    assert bytes(characteristic.Value).decode("utf-8") == "OK -0.30 0.0"

    # Identical repeated writes are dispatched again (no dedup).
    characteristic.WriteValue(list(b"-0.30 0.0"), {})
    assert calls == ["-0.30 0.0", "-0.30 0.0"]

    characteristic.WriteValue(list(b"fast 0.0"), {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR command"

    characteristic.WriteValue([0xff], {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR encoding"

    characteristic.WriteValue(list(b"  "), {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR command"

    def broken(text: str) -> str:
        raise RuntimeError("dispatch blew up")

    characteristic = CmdVelCharacteristic(broken)
    characteristic.StartNotify()
    characteristic.WriteValue(list(b"0.1 0.0"), {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR internal"


def test_initial_pose_characteristic_dispatches_triggers():
    calls = []

    def callback(text: str) -> str:
        calls.append(text)
        return "OK"

    characteristic = InitialPoseCharacteristic(callback)
    assert characteristic.Flags == ["read", "write", "notify"]
    assert characteristic.Descriptors == [INITIAL_POSE_CHARACTERISTIC_CCCD_PATH]

    characteristic.StartNotify()
    characteristic.WriteValue(list(b" 1 \n"), {})
    assert calls == ["1"]
    assert bytes(characteristic.Value).decode("utf-8") == "OK"

    # Any non-empty payload triggers; the text itself is not interpreted.
    characteristic.WriteValue(list(b"reset"), {})
    assert calls == ["1", "reset"]

    characteristic.WriteValue([0xff], {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR encoding"

    characteristic.WriteValue(list(b"  "), {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR command"

    def broken(text: str) -> str:
        raise RuntimeError("dispatch blew up")

    characteristic = InitialPoseCharacteristic(broken)
    characteristic.StartNotify()
    characteristic.WriteValue(list(b"1"), {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR internal"


def test_wifi_config_characteristic_dispatches_requests():
    calls = []

    def callback(ssid: str, password: str) -> str | None:
        calls.append((ssid, password))
        return None

    characteristic = WifiConfigCharacteristic(callback)
    assert characteristic.Flags == ["read", "write", "notify"]
    assert characteristic.Descriptors == [WIFI_CONFIG_CHARACTERISTIC_CCCD_PATH]

    characteristic.StartNotify()
    characteristic.WriteValue(list("MyHome\n12345678".encode("utf-8")), {})

    # Accepted requests get an immediate CONNECTING acknowledgement.
    assert calls == [("MyHome", "12345678")]
    assert bytes(characteristic.Value).decode("utf-8") == "CONNECTING MyHome"

    # An empty/omitted password line means an open network.
    characteristic.WriteValue(list(b"Open AP\n"), {})
    assert calls[-1] == ("Open AP", "")
    assert bytes(characteristic.Value).decode("utf-8") == "CONNECTING Open AP"

    # The asynchronous result replaces the cached value.
    characteristic.report_result("CONNECTED MyHome")
    assert bytes(characteristic.Value).decode("utf-8") == "CONNECTED MyHome"


def test_wifi_config_characteristic_relays_immediate_errors():
    def callback(ssid: str, password: str) -> str | None:
        return "ERR busy" if ssid == "MyHome" else "ERR unavailable"

    characteristic = WifiConfigCharacteristic(callback)
    characteristic.StartNotify()

    characteristic.WriteValue([0xff], {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR encoding"

    # Empty SSID, oversized SSID and short passwords are rejected locally.
    characteristic.WriteValue(list(b"\n12345678"), {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR invalid"

    characteristic.WriteValue(list(f"{'x' * 33}\n12345678".encode()), {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR invalid"

    characteristic.WriteValue(list(b"MyHome\n123"), {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR invalid"

    characteristic.WriteValue(list(b"MyHome\n12345678"), {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR busy"

    characteristic.WriteValue(list(b"OtherAP\n12345678"), {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR unavailable"

    def broken(ssid: str, password: str) -> str | None:
        raise RuntimeError("dispatch blew up")

    characteristic = WifiConfigCharacteristic(broken)
    characteristic.StartNotify()
    characteristic.WriteValue(list(b"MyHome\n12345678"), {})
    assert bytes(characteristic.Value).decode("utf-8") == "ERR internal"


def test_dbus_signatures_match_bluez_gatt_contract():
    characteristic_xml = RobotControlCharacteristic.__dbus_xml__
    application_xml = GattApplication.__dbus_xml__

    assert 'method name="WriteValue"' in characteristic_xml
    assert 'name="value" type="ay" direction="in"' in characteristic_xml
    assert 'method name="GetManagedObjects"' in application_xml
    assert 'type="a{oa{sa{sv}}}"' in application_xml
    managed_objects = GattApplication().GetManagedObjects()
    assert BATTERY_STATUS_CHAR_UUID in str(managed_objects)
    assert NETWORK_CHAR_UUID in str(managed_objects)
    assert CPU_CHAR_UUID in str(managed_objects)
    assert BANDWIDTH_CHAR_UUID in str(managed_objects)
    assert ROBOT_CONTROL_CHAR_UUID in str(managed_objects)
    assert WIFI_CONFIG_CHAR_UUID in str(managed_objects)
    assert ZONE_NAV_CHAR_UUID in str(managed_objects)
    assert CMD_VEL_CHAR_UUID in str(managed_objects)
    assert MEMORY_CHAR_UUID in str(managed_objects)
    assert ZONE_VOICE_CHAR_UUID in str(managed_objects)
    assert INITIAL_POSE_CHAR_UUID in str(managed_objects)
    assert LAN_ENDPOINT_CHAR_UUID in str(managed_objects)
    assert AUDIO_ENDPOINT_CHAR_UUID in str(managed_objects)
    assert "abcdef1" not in str(managed_objects)
    assert "abcdef2" not in str(managed_objects)
    assert "abcdef3" not in str(managed_objects)
    assert "abcdef8" not in str(managed_objects)
    battery_cccd = _unpack_properties(
        managed_objects[BATTERY_STATUS_CHARACTERISTIC_CCCD_PATH][
            "org.bluez.GattDescriptor1"
        ]
    )
    assert battery_cccd == {
        "UUID": CCCD_UUID,
        "Characteristic": "/org/xiaozhi/ble_app/service0/char3",
        "Flags": ["read", "write"],
    }
    assert BATTERY_STATUS_CHARACTERISTIC_CCCD_PATH in managed_objects
    assert NETWORK_CHARACTERISTIC_CCCD_PATH in managed_objects
    assert CPU_CHARACTERISTIC_CCCD_PATH in managed_objects
    assert BANDWIDTH_CHARACTERISTIC_CCCD_PATH in managed_objects
    assert ROBOT_CONTROL_CHARACTERISTIC_CCCD_PATH in managed_objects
    assert WIFI_CONFIG_CHARACTERISTIC_CCCD_PATH in managed_objects
    assert ZONE_NAV_CHARACTERISTIC_CCCD_PATH in managed_objects
    assert MEMORY_CHARACTERISTIC_CCCD_PATH in managed_objects
    assert ZONE_VOICE_CHARACTERISTIC_CCCD_PATH in managed_objects
    assert INITIAL_POSE_CHARACTERISTIC_CCCD_PATH in managed_objects
    lan_char = _unpack_properties(
        managed_objects[LAN_ENDPOINT_CHARACTERISTIC_PATH][
            "org.bluez.GattCharacteristic1"
        ]
    )
    assert lan_char["Flags"] == ["read"]
    assert lan_char["Descriptors"] == []
    audio_char = _unpack_properties(
        managed_objects[AUDIO_ENDPOINT_CHARACTERISTIC_PATH][
            "org.bluez.GattCharacteristic1"
        ]
    )
    assert audio_char["Flags"] == ["read"]
    assert audio_char["Descriptors"] == []


def test_notify_tolerates_properties_changed_failure(monkeypatch):
    def fail_emit(*args):
        raise RuntimeError("D-Bus connection is gone")

    monkeypatch.setattr(BatteryStatusCharacteristic, "PropertiesChanged", fail_emit)
    characteristic = BatteryStatusCharacteristic()
    characteristic.StartNotify()
    characteristic.update_status(0.67, "CHARGING")

    assert bytes(characteristic.Value).decode("utf-8") == "0.670 CHARGING"


def test_advertisement_release_triggers_recovery_callback():
    calls = []
    advertisement = ControlAdvertisement(
        "Xiaozhi",
        on_release=lambda: calls.append(True),
    )

    advertisement.Release()

    assert calls == [True]

    # The recovery callback is optional.
    ControlAdvertisement("Xiaozhi").Release()


def test_bluez_vanish_marks_server_unregistered():
    server = BleControlServer()
    server._registered = True
    server._gatt_registered = True
    server._advertisement_registered = True

    server._on_name_owner_changed("org.bluez", ":1.42", "")

    assert server._registered is False
    assert server._gatt_registered is False
    assert server._advertisement_registered is False

    # Unrelated name owner changes must not affect the registration flags.
    server._registered = True
    server._gatt_registered = True
    server._advertisement_registered = True
    server._on_name_owner_changed("org.example.other", ":1.43", "")

    assert server._registered is True
    assert server._gatt_registered is True
    assert server._advertisement_registered is True


def test_refresh_advertisement_restarts_registered_advertisement():
    class _Manager:
        def __init__(self) -> None:
            self.unregistered = []
            self.registered = []

        def UnregisterAdvertisement(self, path, callback=None):
            self.unregistered.append(path)
            callback(lambda: None)

        def RegisterAdvertisement(self, path, options, callback=None):
            self.registered.append(path)
            callback(lambda: None)

    server = BleControlServer()
    server._gatt_registered = True
    server._advertisement_registered = True
    server._advertising_manager = _Manager()

    assert server._refresh_advertisement() is False

    # Unregister, then re-register from the completion callback.
    assert server._advertising_manager.unregistered == [ADVERTISEMENT_PATH]
    assert server._advertising_manager.registered == [ADVERTISEMENT_PATH]
    assert server._advertisement_registered is True


def test_refresh_advertisement_registers_when_not_active():
    class _Manager:
        def __init__(self) -> None:
            self.registered = []

        def RegisterAdvertisement(self, path, options, callback=None):
            self.registered.append(path)
            callback(lambda: None)

    server = BleControlServer()
    server._gatt_registered = True
    server._advertising_manager = _Manager()

    assert server._refresh_advertisement() is False

    assert server._advertising_manager.registered == [ADVERTISEMENT_PATH]
    assert server._advertisement_registered is True


def test_refresh_advertisement_skipped_during_shutdown():
    server = BleControlServer()
    server._gatt_registered = True
    server._advertisement_registered = True
    server._shutdown_requested = True

    assert server._refresh_advertisement() is False


def test_refresh_advertisement_ignores_release_during_unregister(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        "xiaozhi_ble.gatt_server.GLib.timeout_add_seconds",
        lambda seconds, callback: scheduled.append((seconds, callback)) or 0,
    )

    server = BleControlServer()
    server._gatt_registered = True
    server._advertisement_registered = True

    class _Manager:
        def __init__(self) -> None:
            self.unregistered = []
            self.registered = []

        def UnregisterAdvertisement(self, path, callback=None):
            self.unregistered.append(path)
            # BlueZ calls Release() while UnregisterAdvertisement is in flight.
            server._on_advertisement_released()
            callback(lambda: None)

        def RegisterAdvertisement(self, path, options, callback=None):
            self.registered.append(path)
            callback(lambda: None)

    server._advertising_manager = _Manager()

    assert server._refresh_advertisement() is False

    assert server._advertising_manager.unregistered == [ADVERTISEMENT_PATH]
    assert server._advertising_manager.registered == [ADVERTISEMENT_PATH]
    assert server._advertisement_registered is True
    # Setting the flag False before Unregister must suppress the competing
    # 2s re-register that _on_advertisement_released would otherwise queue.
    assert scheduled == []


def test_client_disconnected_schedules_single_refresh(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        "xiaozhi_ble.gatt_server.GLib.timeout_add_seconds",
        lambda seconds, callback: scheduled.append((seconds, callback)) or 0,
    )
    server = BleControlServer()
    server._gatt_registered = True

    server._on_client_disconnected()
    server._on_client_disconnected()

    assert scheduled == [(2, server._refresh_advertisement)]
    assert server._advertisement_refresh_pending is True
    assert server._advertisement_refresh_count == 1


def test_client_disconnected_skipped_when_unregistered_or_shutting_down(
    monkeypatch,
):
    scheduled = []
    monkeypatch.setattr(
        "xiaozhi_ble.gatt_server.GLib.timeout_add_seconds",
        lambda seconds, callback: scheduled.append((seconds, callback)) or 0,
    )
    server = BleControlServer()

    server._on_client_disconnected()
    assert scheduled == []

    server._gatt_registered = True
    server._shutdown_requested = True
    server._on_client_disconnected()
    assert scheduled == []
    assert server._advertisement_refresh_count == 0


def test_unexpected_advertisement_release_schedules_reregister(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        "xiaozhi_ble.gatt_server.GLib.timeout_add_seconds",
        lambda seconds, callback: scheduled.append((seconds, callback)) or 0,
    )
    server = BleControlServer()
    server._advertisement_registered = True

    server._on_advertisement_released()

    assert scheduled == [(2, server._reregister_advertisement)]
    assert server._advertisement_registered is False
