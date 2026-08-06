"""Tests for the BLE push-to-talk GATT implementation."""

from xiaozhi_ble.gatt_server import ADVERTISEMENT_PATH
from xiaozhi_ble.gatt_server import BATTERY_STATUS_CHAR_UUID
from xiaozhi_ble.gatt_server import BATTERY_STATUS_CHARACTERISTIC_CCCD_PATH
from xiaozhi_ble.gatt_server import CCCD_UUID
from xiaozhi_ble.gatt_server import CHARACTERISTIC_CCCD_PATH
from xiaozhi_ble.gatt_server import CPU_CHAR_UUID
from xiaozhi_ble.gatt_server import CPU_CHARACTERISTIC_CCCD_PATH
from xiaozhi_ble.gatt_server import NETWORK_CHAR_UUID
from xiaozhi_ble.gatt_server import NETWORK_CHARACTERISTIC_CCCD_PATH
from xiaozhi_ble.gatt_server import BatteryStatusCharacteristic
from xiaozhi_ble.gatt_server import BleControlServer
from xiaozhi_ble.gatt_server import BridgeError
from xiaozhi_ble.gatt_server import ERROR_CHAR_UUID
from xiaozhi_ble.gatt_server import ERROR_CHARACTERISTIC_CCCD_PATH
from xiaozhi_ble.gatt_server import URL_CHAR_UUID
from xiaozhi_ble.gatt_server import URL_CHARACTERISTIC_CCCD_PATH
from xiaozhi_ble.gatt_server import CommandCharacteristic
from xiaozhi_ble.gatt_server import ControlAdvertisement
from xiaozhi_ble.gatt_server import CpuStatusCharacteristic
from xiaozhi_ble.gatt_server import ErrorCharacteristic
from xiaozhi_ble.gatt_server import GattApplication
from xiaozhi_ble.gatt_server import NetworkStatusCharacteristic
from xiaozhi_ble.gatt_server import WebsocketUrlCharacteristic


def _value(characteristic: CommandCharacteristic) -> str:
    return bytes(characteristic.Value).decode("utf-8")


def _unpack_properties(properties: dict) -> dict:
    return {key: value.unpack() for key, value in properties.items()}


def _ok_callback(calls: list):
    def callback(command: str, reason: str) -> str:
        calls.append((command, reason))
        return f"OK {command}"

    return callback


def test_start_requires_notification_subscription():
    calls = []
    characteristic = CommandCharacteristic(_ok_callback(calls))
    assert characteristic.Descriptors == [CHARACTERISTIC_CCCD_PATH]

    characteristic.WriteValue(list(b"start"), {})

    assert calls == []
    assert _value(characteristic) == "STATE idle"


def test_start_is_idempotent_and_disconnect_stops():
    calls = []
    characteristic = CommandCharacteristic(_ok_callback(calls))
    characteristic.StartNotify()

    characteristic.WriteValue(list(b"start"), {})
    characteristic.WriteValue(list(b"START"), {})
    characteristic.StopNotify()

    assert calls == [
        ("start", "bluetooth"),
        ("stop", "bluetooth_disconnect"),
    ]


def test_explicit_stop_prevents_duplicate_disconnect_stop():
    calls = []
    characteristic = CommandCharacteristic(_ok_callback(calls))
    characteristic.StartNotify()

    characteristic.WriteValue(list(b"start"), {})
    characteristic.WriteValue(list(b"stop"), {})
    characteristic.StopNotify()

    assert calls == [
        ("start", "bluetooth"),
        ("stop", "bluetooth"),
    ]
    assert _value(characteristic) == "OK stop"


def test_failed_start_is_not_marked_pressed():
    calls = []

    def callback(command: str, reason: str) -> str:
        calls.append((command, reason))
        return "ERR VOICE_UNAVAILABLE control socket is not connected"

    characteristic = CommandCharacteristic(callback)
    characteristic.StartNotify()

    characteristic.WriteValue(list(b"start"), {})
    characteristic.StopNotify()

    # The failed press must not trigger the automatic disconnect stop.
    assert calls == [("start", "bluetooth")]
    assert _value(characteristic).startswith("ERR VOICE_UNAVAILABLE")


def test_errors_and_state_notifications_update_value():
    calls = []
    characteristic = CommandCharacteristic(_ok_callback(calls))
    characteristic.StartNotify()

    characteristic.WriteValue([0xff], {})
    assert _value(characteristic) == "ERR encoding"

    characteristic.WriteValue(list(b"unknown"), {})
    assert _value(characteristic) == "ERR command"

    characteristic.update_state("listening")
    assert _value(characteristic) == "STATE listening"
    assert calls == []


def test_websocket_url_characteristic_dispatches_updates_and_errors():
    calls = []
    errors = []
    characteristic = WebsocketUrlCharacteristic(
        lambda url: calls.append(url),
        "ws://old.example/ws",
        lambda code, message: errors.append((code, message)),
    )
    assert characteristic.Flags == ["read", "write", "notify"]
    assert characteristic.Descriptors == [URL_CHARACTERISTIC_CCCD_PATH]

    characteristic.WriteValue(list(b"wss://new.example/ws"), {})
    characteristic.WriteValue([0xff], {})
    characteristic.WriteValue([], {})

    assert calls == ["wss://new.example/ws"]
    assert errors == [
        ("CONFIG_ENCODING", "URL is not valid UTF-8"),
        ("CONFIG_INVALID", "URL must not be empty"),
    ]
    characteristic.update_url("wss://new.example/ws")
    assert bytes(characteristic.Value).decode("utf-8") == "wss://new.example/ws"


def test_websocket_url_characteristic_relays_bridge_errors():
    errors = []

    def callback(url: str) -> None:
        raise BridgeError("CONFIG_BUSY", "cannot change websocket_url while speaking")

    characteristic = WebsocketUrlCharacteristic(
        callback,
        "ws://old.example/ws",
        lambda code, message: errors.append((code, message)),
    )

    characteristic.WriteValue(list(b"wss://new.example/ws"), {})

    assert errors == [("CONFIG_BUSY", "cannot change websocket_url while speaking")]


def test_error_characteristic_publishes_bounded_status():
    characteristic = ErrorCharacteristic()
    assert characteristic.Descriptors == [ERROR_CHARACTERISTIC_CCCD_PATH]
    assert bytes(characteristic.Value).decode("utf-8") == "NONE"

    characteristic.publish("WS_ERROR", "connection failed")

    assert bytes(characteristic.Value).decode("utf-8") == (
        "ERROR WS_ERROR connection failed"
    )


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


def test_dbus_signatures_match_bluez_gatt_contract():
    characteristic_xml = CommandCharacteristic.__dbus_xml__
    application_xml = GattApplication.__dbus_xml__

    assert 'method name="WriteValue"' in characteristic_xml
    assert 'name="value" type="ay" direction="in"' in characteristic_xml
    assert 'method name="GetManagedObjects"' in application_xml
    assert 'type="a{oa{sa{sv}}}"' in application_xml
    managed_objects = GattApplication().GetManagedObjects()
    assert URL_CHAR_UUID in str(managed_objects)
    assert ERROR_CHAR_UUID in str(managed_objects)
    assert BATTERY_STATUS_CHAR_UUID in str(managed_objects)
    assert NETWORK_CHAR_UUID in str(managed_objects)
    assert CPU_CHAR_UUID in str(managed_objects)
    command_cccd = _unpack_properties(
        managed_objects[CHARACTERISTIC_CCCD_PATH]["org.bluez.GattDescriptor1"]
    )
    assert command_cccd == {
        "UUID": CCCD_UUID,
        "Characteristic": "/org/xiaozhi/ble_app/service0/char0",
        "Flags": ["read", "write"],
    }
    assert URL_CHARACTERISTIC_CCCD_PATH in managed_objects
    assert ERROR_CHARACTERISTIC_CCCD_PATH in managed_objects
    assert BATTERY_STATUS_CHARACTERISTIC_CCCD_PATH in managed_objects
    assert NETWORK_CHARACTERISTIC_CCCD_PATH in managed_objects
    assert CPU_CHARACTERISTIC_CCCD_PATH in managed_objects
    command_char = _unpack_properties(
        managed_objects["/org/xiaozhi/ble_app/service0/char0"][
            "org.bluez.GattCharacteristic1"
        ]
    )
    assert command_char["Descriptors"] == [CHARACTERISTIC_CCCD_PATH]


def test_notify_tolerates_properties_changed_failure(monkeypatch):
    def fail_emit(*args):
        raise RuntimeError("D-Bus connection is gone")

    monkeypatch.setattr(CommandCharacteristic, "PropertiesChanged", fail_emit)
    characteristic = CommandCharacteristic(_ok_callback([]))
    characteristic.StartNotify()

    assert _value(characteristic) == "STATE idle"

    characteristic.update_state("listening")

    assert _value(characteristic) == "STATE listening"

    monkeypatch.setattr(ErrorCharacteristic, "PropertiesChanged", fail_emit)
    error_characteristic = ErrorCharacteristic()
    error_characteristic.StartNotify()
    error_characteristic.publish("WS_ERROR", "connection failed")

    assert bytes(error_characteristic.Value).decode("utf-8") == (
        "ERROR WS_ERROR connection failed"
    )


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
    server = BleControlServer(_ok_callback([]))
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


def test_stop_notify_invokes_disconnect_hook():
    calls = []
    characteristic = CommandCharacteristic(
        _ok_callback([]),
        on_disconnect=lambda: calls.append(True),
    )
    characteristic.StartNotify()

    assert calls == []

    characteristic.StopNotify()

    assert calls == [True]


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

    server = BleControlServer(_ok_callback([]))
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

    server = BleControlServer(_ok_callback([]))
    server._gatt_registered = True
    server._advertising_manager = _Manager()

    assert server._refresh_advertisement() is False

    assert server._advertising_manager.registered == [ADVERTISEMENT_PATH]
    assert server._advertisement_registered is True


def test_refresh_advertisement_skipped_during_shutdown():
    server = BleControlServer(_ok_callback([]))
    server._gatt_registered = True
    server._advertisement_registered = True
    server._shutdown_requested = True

    assert server._refresh_advertisement() is False
