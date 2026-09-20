"""BlueZ GATT server for push-to-talk control.

This module runs the BLE peripheral side of the bridge. It does not talk to
the voice client directly; command and URL callbacks forward to the xiaozhi
control socket (see control_client.py) and return the socket response, which
is then relayed to the BLE App as a notification.
"""

import threading
from typing import Callable

from dasbus.connection import SystemMessageBus
from dasbus.loop import EventLoop
from dasbus.server.interface import dbus_interface
from dasbus.server.template import InterfaceTemplate
from dasbus.typing import Bool, Byte, Dict, List, ObjPath, Str, Variant
from gi.repository import GLib
from loguru import logger

from .wifi_config import WifiRequestError, parse_wifi_request


SERVICE_UUID = "12345678-1234-5678-1234-56789abcdef0"
COMMAND_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef1"
URL_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef2"
ERROR_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef3"
BATTERY_STATUS_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef4"
NETWORK_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef5"
CPU_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef6"
BANDWIDTH_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef7"
LATENCY_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef8"
ROBOT_CONTROL_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef9"
WIFI_CONFIG_CHAR_UUID = "12345678-1234-5678-1234-56789abcdefa"
NAV_TASK_CHAR_UUID = "12345678-1234-5678-1234-56789abcdefb"
ZONE_NAV_CHAR_UUID = "12345678-1234-5678-1234-56789abcdefc"
CMD_VEL_CHAR_UUID = "12345678-1234-5678-1234-56789abcdefd"
MEMORY_CHAR_UUID = "12345678-1234-5678-1234-56789abcdefe"
ZONE_VOICE_CHAR_UUID = "12345678-1234-5678-1234-56789abcdeff"
# The abcdefN tail is exhausted; new characteristics continue with the
# abcd0NN series (see AGENTS.md section 3).
INITIAL_POSE_CHAR_UUID = "12345678-1234-5678-1234-56789abcd010"
CCCD_UUID = "00002902-0000-1000-8000-00805f9b34fb"

BLUEZ_SERVICE_NAME = "org.bluez"
APP_PATH = "/org/xiaozhi/ble_app"
SERVICE_PATH = f"{APP_PATH}/service0"
CHARACTERISTIC_PATH = f"{SERVICE_PATH}/char0"
URL_CHARACTERISTIC_PATH = f"{SERVICE_PATH}/char1"
ERROR_CHARACTERISTIC_PATH = f"{SERVICE_PATH}/char2"
BATTERY_STATUS_CHARACTERISTIC_PATH = f"{SERVICE_PATH}/char3"
NETWORK_CHARACTERISTIC_PATH = f"{SERVICE_PATH}/char4"
CPU_CHARACTERISTIC_PATH = f"{SERVICE_PATH}/char5"
BANDWIDTH_CHARACTERISTIC_PATH = f"{SERVICE_PATH}/char6"
LATENCY_CHARACTERISTIC_PATH = f"{SERVICE_PATH}/char7"
ROBOT_CONTROL_CHARACTERISTIC_PATH = f"{SERVICE_PATH}/char8"
WIFI_CONFIG_CHARACTERISTIC_PATH = f"{SERVICE_PATH}/char9"
NAV_TASK_CHARACTERISTIC_PATH = f"{SERVICE_PATH}/char10"
ZONE_NAV_CHARACTERISTIC_PATH = f"{SERVICE_PATH}/char11"
CMD_VEL_CHARACTERISTIC_PATH = f"{SERVICE_PATH}/char12"
MEMORY_CHARACTERISTIC_PATH = f"{SERVICE_PATH}/char13"
ZONE_VOICE_CHARACTERISTIC_PATH = f"{SERVICE_PATH}/char14"
INITIAL_POSE_CHARACTERISTIC_PATH = f"{SERVICE_PATH}/char15"
CHARACTERISTIC_CCCD_PATH = f"{CHARACTERISTIC_PATH}/desc0"
URL_CHARACTERISTIC_CCCD_PATH = f"{URL_CHARACTERISTIC_PATH}/desc0"
ERROR_CHARACTERISTIC_CCCD_PATH = f"{ERROR_CHARACTERISTIC_PATH}/desc0"
BATTERY_STATUS_CHARACTERISTIC_CCCD_PATH = (
    f"{BATTERY_STATUS_CHARACTERISTIC_PATH}/desc0"
)
NETWORK_CHARACTERISTIC_CCCD_PATH = f"{NETWORK_CHARACTERISTIC_PATH}/desc0"
CPU_CHARACTERISTIC_CCCD_PATH = f"{CPU_CHARACTERISTIC_PATH}/desc0"
BANDWIDTH_CHARACTERISTIC_CCCD_PATH = f"{BANDWIDTH_CHARACTERISTIC_PATH}/desc0"
LATENCY_CHARACTERISTIC_CCCD_PATH = f"{LATENCY_CHARACTERISTIC_PATH}/desc0"
ROBOT_CONTROL_CHARACTERISTIC_CCCD_PATH = f"{ROBOT_CONTROL_CHARACTERISTIC_PATH}/desc0"
WIFI_CONFIG_CHARACTERISTIC_CCCD_PATH = f"{WIFI_CONFIG_CHARACTERISTIC_PATH}/desc0"
NAV_TASK_CHARACTERISTIC_CCCD_PATH = f"{NAV_TASK_CHARACTERISTIC_PATH}/desc0"
ZONE_NAV_CHARACTERISTIC_CCCD_PATH = f"{ZONE_NAV_CHARACTERISTIC_PATH}/desc0"
CMD_VEL_CHARACTERISTIC_CCCD_PATH = f"{CMD_VEL_CHARACTERISTIC_PATH}/desc0"
MEMORY_CHARACTERISTIC_CCCD_PATH = f"{MEMORY_CHARACTERISTIC_PATH}/desc0"
ZONE_VOICE_CHARACTERISTIC_CCCD_PATH = f"{ZONE_VOICE_CHARACTERISTIC_PATH}/desc0"
INITIAL_POSE_CHARACTERISTIC_CCCD_PATH = (
    f"{INITIAL_POSE_CHARACTERISTIC_PATH}/desc0"
)
ADVERTISEMENT_PATH = "/org/xiaozhi/ble_adv"

# Receives (command, reason), returns the response text relayed to the App,
# e.g. "OK start" or "ERR VOICE_UNAVAILABLE ...".
CommandCallback = Callable[[str, str], str]
UrlCallback = Callable[[str], None]
ErrorCallback = Callable[[str, str], None]
# Receives the command name, returns an immediate error text relayed to the
# App (e.g. "ERR command"), or None when the command was dispatched and its
# result is reported asynchronously.
RobotControlCallback = Callable[[str], str | None]
# Receives (ssid, password), returns an immediate error text relayed to the
# App (e.g. "ERR busy"), or None when the request was accepted and its result
# is reported asynchronously ("CONNECTED ..." / "FAILED ...").
WifiConfigCallback = Callable[[str, str], str | None]
# Receives the raw Nav Task write text, returns the immediate reply relayed
# to the App (e.g. "STARTED navigation", "ERR task foo" or the STATUS text).
NavTaskCallback = Callable[[str], str]
# Receives the zone name, returns the immediate reply relayed to the App
# (e.g. "OK charging_zone" or "ERR command"); publishing is fire-and-forget,
# so the immediate reply is the final result.
ZoneNavCallback = Callable[[str], str]
# Receives the raw "<linear_x> <angular_z>" write text, returns the immediate
# reply relayed to the App (e.g. "OK -0.3 0.0" or "ERR command"); every write
# publishes exactly one Twist, repeated values included.
CmdVelCallback = Callable[[str], str]
# Receives the raw Zone Voice write text, returns the immediate reply relayed
# to the App (e.g. "PLAYING pool_zone.mp3", "LIST ...", or "ERR command").
ZoneVoiceCallback = Callable[[str], str]
# Receives the raw Initial Pose write text (any non-empty text means "reset
# the pose now"), returns the immediate reply relayed to the App ("OK" or
# "ERR ..."); publishing is fire-and-forget, so the immediate reply is the
# final result.
InitialPoseCallback = Callable[[str], str]


class BridgeError(Exception):
    """A control-socket failure with a protocol error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _read_bytes(value: List[Byte], options: Dict[Str, Variant]) -> List[Byte]:
    """Apply a BlueZ long-read offset to a characteristic value."""
    offset = options.get("offset")
    if offset is None:
        return value
    try:
        offset_value = int(offset.unpack())
    except (AttributeError, TypeError, ValueError):
        offset_value = 0
    return value[offset_value:]


def _bounded_text(text: str, limit: int = 180) -> str:
    """Keep notifications within the negotiated ATT payload budget."""
    data = text.encode("utf-8")[:limit]
    return data.decode("utf-8", errors="ignore")


@dbus_interface("org.bluez.GattCharacteristic1")
class CommandCharacteristic(InterfaceTemplate):
    """Receive start/stop writes and publish acknowledgements and state."""

    def __init__(
        self,
        callback: CommandCallback,
        initial_state: str = "idle",
        on_disconnect: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(self)
        self._callback = callback
        self._on_disconnect = on_disconnect
        self._flags = ["read", "write", "notify"]
        self._notifying = False
        self._pressed = False
        self._state = initial_state
        self._value: List[Byte] = list(f"STATE {initial_state}".encode("utf-8"))

    @property
    def UUID(self) -> Str:
        return COMMAND_CHAR_UUID

    @property
    def Service(self) -> ObjPath:
        return SERVICE_PATH

    @property
    def Flags(self) -> List[Str]:
        return self._flags

    @property
    def Descriptors(self) -> List[ObjPath]:
        return [CHARACTERISTIC_CCCD_PATH]

    @property
    def Value(self) -> List[Byte]:
        return self._value

    def ReadValue(self, options: Dict[Str, Variant]) -> List[Byte]:
        """Return the most recently published response or state."""
        return _read_bytes(self._value, options)

    def WriteValue(self, value: List[Byte], options: Dict[Str, Variant]) -> None:
        """Handle a UTF-8 start or stop command."""
        try:
            command = bytes(value).decode("utf-8").strip().lower()
        except UnicodeDecodeError:
            self._notify("ERR encoding")
            return

        logger.info(f"Received BLE command: {command!r}")
        if command == "start":
            if not self._notifying:
                logger.warning("Ignoring BLE start before notifications are enabled")
                return
            if self._pressed:
                self._notify("OK start")
                return
            try:
                response = self._callback("start", "bluetooth")
            except Exception:
                logger.exception("Failed to dispatch BLE start command")
                self._notify("ERR internal")
                return
            if response.startswith("OK"):
                self._pressed = True
            self._notify(response)
            return

        if command == "stop":
            try:
                response = self._callback("stop", "bluetooth")
            except Exception:
                logger.exception("Failed to dispatch BLE stop command")
                self._notify("ERR internal")
                return
            if response.startswith("OK"):
                self._pressed = False
            self._notify(response)
            return

        self._notify("ERR command")

    def StartNotify(self) -> None:
        """Enable notifications and immediately publish current state."""
        logger.info("BLE notifications enabled")
        self._notifying = True
        self._notify(f"STATE {self._state}")

    def StopNotify(self) -> None:
        """Stop notifications and release an outstanding push-to-talk press."""
        logger.info("BLE notifications disabled or client disconnected")
        self._notifying = False
        self._force_stop("bluetooth_disconnect")
        # Also fires when the App merely unsubscribes; the hook must stay
        # harmless in that case.
        if self._on_disconnect is not None:
            try:
                self._on_disconnect()
            except Exception:
                logger.exception("Failed to dispatch BLE disconnect hook")

    def update_state(self, state: str) -> None:
        """Store and notify the current application state."""
        self._state = state
        if self._notifying:
            self._notify(f"STATE {state}")

    def force_stop(self, reason: str) -> None:
        """Release an outstanding press while shutting down the BLE server."""
        self._force_stop(reason)

    def _force_stop(self, reason: str) -> None:
        if not self._pressed:
            return
        self._pressed = False
        try:
            self._callback("stop", reason)
        except Exception:
            logger.exception("Failed to dispatch automatic BLE stop command")

    def _notify(self, text: str) -> None:
        data = list(_bounded_text(text).encode("utf-8"))
        self._value = data
        if not self._notifying:
            return
        try:
            self.PropertiesChanged(
                "org.bluez.GattCharacteristic1",
                {"Value": Variant("ay", data)},
                [],
            )
        except Exception:
            logger.exception(f"Failed to emit BLE notification: {text!r}")
            return
        logger.debug(f"Sent BLE notification: {text!r}")


@dbus_interface("org.bluez.GattCharacteristic1")
class WebsocketUrlCharacteristic(InterfaceTemplate):
    """Read and write the persisted WebSocket URL over BLE."""

    def __init__(
        self,
        callback: UrlCallback,
        initial_url: str,
        on_error: ErrorCallback,
    ) -> None:
        super().__init__(self)
        self._callback = callback
        self._on_error = on_error
        self._flags = [
            "read",
            "write",
            "notify",
        ]
        self._notifying = False
        self._value: List[Byte] = list(_bounded_text(initial_url).encode("utf-8"))

    @property
    def UUID(self) -> Str:
        return URL_CHAR_UUID

    @property
    def Service(self) -> ObjPath:
        return SERVICE_PATH

    @property
    def Flags(self) -> List[Str]:
        return self._flags

    @property
    def Descriptors(self) -> List[ObjPath]:
        return [URL_CHARACTERISTIC_CCCD_PATH]

    @property
    def Value(self) -> List[Byte]:
        return self._value

    def ReadValue(self, options: Dict[Str, Variant]) -> List[Byte]:
        return _read_bytes(self._value, options)

    def WriteValue(self, value: List[Byte], options: Dict[Str, Variant]) -> None:
        try:
            url = bytes(value).decode("utf-8").strip()
        except UnicodeDecodeError:
            self._on_error("CONFIG_ENCODING", "URL is not valid UTF-8")
            return
        if not url:
            self._on_error("CONFIG_INVALID", "URL must not be empty")
            return
        try:
            self._callback(url)
        except BridgeError as exc:
            self._on_error(exc.code, exc.message)
            return
        except Exception as exc:
            logger.exception("Failed to dispatch BLE URL update")
            self._on_error("CONFIG_INTERNAL", str(exc))
            return
        logger.info(f"Received BLE websocket URL update: {url!r}")

    def StartNotify(self) -> None:
        self._notifying = True
        self._notify()

    def StopNotify(self) -> None:
        self._notifying = False

    def update_url(self, url: str) -> None:
        self._value = list(_bounded_text(url).encode("utf-8"))
        if self._notifying:
            self._notify()

    def _notify(self) -> None:
        try:
            self.PropertiesChanged(
                "org.bluez.GattCharacteristic1",
                {"Value": Variant("ay", self._value)},
                [],
            )
        except Exception:
            logger.exception("Failed to emit BLE websocket URL notification")
            return
        logger.debug(f"Sent BLE websocket URL: {bytes(self._value)!r}")


@dbus_interface("org.bluez.GattCharacteristic1")
class ErrorCharacteristic(InterfaceTemplate):
    """Publish the latest connection or configuration error over BLE."""

    def __init__(self, initial_error: tuple[str, str] | None = None) -> None:
        super().__init__(self)
        self._flags = ["read", "notify"]
        self._notifying = False
        self._value: List[Byte] = []
        self.publish(*(initial_error or ("NONE", "")), notify=False)

    @property
    def UUID(self) -> Str:
        return ERROR_CHAR_UUID

    @property
    def Service(self) -> ObjPath:
        return SERVICE_PATH

    @property
    def Flags(self) -> List[Str]:
        return self._flags

    @property
    def Descriptors(self) -> List[ObjPath]:
        return [ERROR_CHARACTERISTIC_CCCD_PATH]

    @property
    def Value(self) -> List[Byte]:
        return self._value

    def ReadValue(self, options: Dict[Str, Variant]) -> List[Byte]:
        return _read_bytes(self._value, options)

    def StartNotify(self) -> None:
        self._notifying = True
        self._notify()

    def StopNotify(self) -> None:
        self._notifying = False

    def publish(self, code: str, message: str, notify: bool = True) -> None:
        clean_message = " ".join(str(message).splitlines())
        text = "NONE" if code == "NONE" else f"ERROR {code} {clean_message}".strip()
        self._value = list(_bounded_text(text).encode("utf-8"))
        if notify and self._notifying:
            self._notify()

    def _notify(self) -> None:
        try:
            self.PropertiesChanged(
                "org.bluez.GattCharacteristic1",
                {"Value": Variant("ay", self._value)},
                [],
            )
        except Exception:
            logger.exception("Failed to emit BLE error notification")
            return
        logger.debug(f"Sent BLE error: {bytes(self._value)!r}")


@dbus_interface("org.bluez.GattCharacteristic1")
class BatteryStatusCharacteristic(InterfaceTemplate):
    """Publish the latest battery percentage and supply status once per second."""

    def __init__(
        self,
        initial_percentage: float | None = None,
        initial_supply_status: str | None = None,
    ) -> None:
        super().__init__(self)
        self._flags = ["read", "notify"]
        self._notifying = False
        self._value: List[Byte] = []
        self.update_status(initial_percentage, initial_supply_status, notify=False)

    @property
    def UUID(self) -> Str:
        return BATTERY_STATUS_CHAR_UUID

    @property
    def Service(self) -> ObjPath:
        return SERVICE_PATH

    @property
    def Flags(self) -> List[Str]:
        return self._flags

    @property
    def Descriptors(self) -> List[ObjPath]:
        return [BATTERY_STATUS_CHARACTERISTIC_CCCD_PATH]

    @property
    def Value(self) -> List[Byte]:
        return self._value

    def ReadValue(self, options: Dict[Str, Variant]) -> List[Byte]:
        return _read_bytes(self._value, options)

    def StartNotify(self) -> None:
        self._notifying = True
        self._notify()

    def StopNotify(self) -> None:
        self._notifying = False

    def update_status(
        self,
        percentage: float | None,
        supply_status: str | None,
        notify: bool = True,
    ) -> None:
        if percentage is None and supply_status is None:
            text = "UNKNOWN"
        else:
            percentage_text = "UNKNOWN" if percentage is None else f"{percentage:.3f}"
            text = f"{percentage_text} {supply_status or 'UNKNOWN'}"
        self._value = list(_bounded_text(text).encode("utf-8"))
        if notify and self._notifying:
            self._notify()

    def tick(self) -> bool:
        if self._notifying:
            self._notify()
        return True

    def _notify(self) -> None:
        try:
            self.PropertiesChanged(
                "org.bluez.GattCharacteristic1",
                {"Value": Variant("ay", self._value)},
                [],
            )
        except Exception:
            logger.exception("Failed to emit BLE battery notification")
            return
        logger.debug(f"Sent BLE battery status: {bytes(self._value)!r}")


@dbus_interface("org.bluez.GattCharacteristic1")
class NetworkStatusCharacteristic(InterfaceTemplate):
    """Publish the WiFi link status; notify only on change."""

    def __init__(self) -> None:
        super().__init__(self)
        self._flags = ["read", "notify"]
        self._notifying = False
        # UNKNOWN until the network provider delivers its first poll result.
        self._value: List[Byte] = list(b"UNKNOWN")

    @property
    def UUID(self) -> Str:
        return NETWORK_CHAR_UUID

    @property
    def Service(self) -> ObjPath:
        return SERVICE_PATH

    @property
    def Flags(self) -> List[Str]:
        return self._flags

    @property
    def Descriptors(self) -> List[ObjPath]:
        return [NETWORK_CHARACTERISTIC_CCCD_PATH]

    @property
    def Value(self) -> List[Byte]:
        return self._value

    def ReadValue(self, options: Dict[Str, Variant]) -> List[Byte]:
        return _read_bytes(self._value, options)

    def StartNotify(self) -> None:
        self._notifying = True
        self._notify()

    def StopNotify(self) -> None:
        self._notifying = False

    def update_status(
        self,
        ssid: str | None,
        rssi_dbm: int | None,
        ipv4: str | None,
        notify: bool = True,
    ) -> None:
        if ssid is None:
            text = "DISCONNECTED"
        else:
            signal_text = "-" if rssi_dbm is None else str(rssi_dbm)
            ip_text = ipv4 or "-"
            text = f"WIFI {signal_text} {ip_text} {ssid}"
        self._value = list(_bounded_text(text).encode("utf-8"))
        if notify and self._notifying:
            self._notify()

    def _notify(self) -> None:
        try:
            self.PropertiesChanged(
                "org.bluez.GattCharacteristic1",
                {"Value": Variant("ay", self._value)},
                [],
            )
        except Exception:
            logger.exception("Failed to emit BLE network notification")
            return
        logger.debug(f"Sent BLE network status: {bytes(self._value)!r}")


@dbus_interface("org.bluez.GattCharacteristic1")
class CpuStatusCharacteristic(InterfaceTemplate):
    """Publish the whole-machine CPU usage; notify only on change."""

    def __init__(self) -> None:
        super().__init__(self)
        self._flags = ["read", "notify"]
        self._notifying = False
        # UNKNOWN until the CPU provider delivers its first delta reading.
        self._value: List[Byte] = list(b"UNKNOWN")

    @property
    def UUID(self) -> Str:
        return CPU_CHAR_UUID

    @property
    def Service(self) -> ObjPath:
        return SERVICE_PATH

    @property
    def Flags(self) -> List[Str]:
        return self._flags

    @property
    def Descriptors(self) -> List[ObjPath]:
        return [CPU_CHARACTERISTIC_CCCD_PATH]

    @property
    def Value(self) -> List[Byte]:
        return self._value

    def ReadValue(self, options: Dict[Str, Variant]) -> List[Byte]:
        return _read_bytes(self._value, options)

    def StartNotify(self) -> None:
        self._notifying = True
        self._notify()

    def StopNotify(self) -> None:
        self._notifying = False

    def update_usage(self, usage: float | None, notify: bool = True) -> None:
        text = "UNKNOWN" if usage is None else f"CPU {usage:.1f}"
        self._value = list(_bounded_text(text).encode("utf-8"))
        if notify and self._notifying:
            self._notify()

    def _notify(self) -> None:
        try:
            self.PropertiesChanged(
                "org.bluez.GattCharacteristic1",
                {"Value": Variant("ay", self._value)},
                [],
            )
        except Exception:
            logger.exception("Failed to emit BLE CPU notification")
            return
        logger.debug(f"Sent BLE CPU usage: {bytes(self._value)!r}")


@dbus_interface("org.bluez.GattCharacteristic1")
class BandwidthStatusCharacteristic(InterfaceTemplate):
    """Publish the WiFi rx/tx throughput; notify only on change."""

    def __init__(self) -> None:
        super().__init__(self)
        self._flags = ["read", "notify"]
        self._notifying = False
        # UNKNOWN until the bandwidth provider delivers its first delta.
        self._value: List[Byte] = list(b"UNKNOWN")

    @property
    def UUID(self) -> Str:
        return BANDWIDTH_CHAR_UUID

    @property
    def Service(self) -> ObjPath:
        return SERVICE_PATH

    @property
    def Flags(self) -> List[Str]:
        return self._flags

    @property
    def Descriptors(self) -> List[ObjPath]:
        return [BANDWIDTH_CHARACTERISTIC_CCCD_PATH]

    @property
    def Value(self) -> List[Byte]:
        return self._value

    def ReadValue(self, options: Dict[Str, Variant]) -> List[Byte]:
        return _read_bytes(self._value, options)

    def StartNotify(self) -> None:
        self._notifying = True
        self._notify()

    def StopNotify(self) -> None:
        self._notifying = False

    def update_bandwidth(
        self,
        rx_kbps: float | None,
        tx_kbps: float | None,
        notify: bool = True,
    ) -> None:
        text = (
            "UNKNOWN"
            if rx_kbps is None or tx_kbps is None
            else f"BANDWIDTH {rx_kbps:.1f} {tx_kbps:.1f}"
        )
        self._value = list(_bounded_text(text).encode("utf-8"))
        if notify and self._notifying:
            self._notify()

    def _notify(self) -> None:
        try:
            self.PropertiesChanged(
                "org.bluez.GattCharacteristic1",
                {"Value": Variant("ay", self._value)},
                [],
            )
        except Exception:
            logger.exception("Failed to emit BLE bandwidth notification")
            return
        logger.debug(f"Sent BLE bandwidth: {bytes(self._value)!r}")


@dbus_interface("org.bluez.GattCharacteristic1")
class LatencyStatusCharacteristic(InterfaceTemplate):
    """Publish the dialogue-server connect latency; notify only on change."""

    def __init__(self) -> None:
        super().__init__(self)
        self._flags = ["read", "notify"]
        self._notifying = False
        # UNKNOWN until the latency provider gets a URL and its first probe.
        self._value: List[Byte] = list(b"UNKNOWN")

    @property
    def UUID(self) -> Str:
        return LATENCY_CHAR_UUID

    @property
    def Service(self) -> ObjPath:
        return SERVICE_PATH

    @property
    def Flags(self) -> List[Str]:
        return self._flags

    @property
    def Descriptors(self) -> List[ObjPath]:
        return [LATENCY_CHARACTERISTIC_CCCD_PATH]

    @property
    def Value(self) -> List[Byte]:
        return self._value

    def ReadValue(self, options: Dict[Str, Variant]) -> List[Byte]:
        return _read_bytes(self._value, options)

    def StartNotify(self) -> None:
        self._notifying = True
        self._notify()

    def StopNotify(self) -> None:
        self._notifying = False

    def update_latency(self, latency_ms: float | None, notify: bool = True) -> None:
        # None means the server is unreachable, not "no reading yet".
        text = "LATENCY -" if latency_ms is None else f"LATENCY {latency_ms:.0f}"
        self._value = list(_bounded_text(text).encode("utf-8"))
        if notify and self._notifying:
            self._notify()

    def _notify(self) -> None:
        try:
            self.PropertiesChanged(
                "org.bluez.GattCharacteristic1",
                {"Value": Variant("ay", self._value)},
                [],
            )
        except Exception:
            logger.exception("Failed to emit BLE latency notification")
            return
        logger.debug(f"Sent BLE latency: {bytes(self._value)!r}")


@dbus_interface("org.bluez.GattCharacteristic1")
class RobotControlCharacteristic(InterfaceTemplate):
    """Receive robot-control commands and notify only on errors."""

    def __init__(self, callback: RobotControlCallback) -> None:
        super().__init__(self)
        self._callback = callback
        self._flags = ["read", "write", "notify"]
        self._notifying = False
        self._value: List[Byte] = []

    @property
    def UUID(self) -> Str:
        return ROBOT_CONTROL_CHAR_UUID

    @property
    def Service(self) -> ObjPath:
        return SERVICE_PATH

    @property
    def Flags(self) -> List[Str]:
        return self._flags

    @property
    def Descriptors(self) -> List[ObjPath]:
        return [ROBOT_CONTROL_CHARACTERISTIC_CCCD_PATH]

    @property
    def Value(self) -> List[Byte]:
        return self._value

    def ReadValue(self, options: Dict[Str, Variant]) -> List[Byte]:
        """Return the most recently reported error (empty when none)."""
        return _read_bytes(self._value, options)

    def WriteValue(self, value: List[Byte], options: Dict[Str, Variant]) -> None:
        """Dispatch a robot-control command; success stays silent."""
        try:
            command = bytes(value).decode("utf-8").strip().lower()
        except UnicodeDecodeError:
            self._notify("ERR encoding")
            return

        logger.info(f"Received BLE robot-control command: {command!r}")
        try:
            error = self._callback(command)
        except Exception:
            logger.exception("Failed to dispatch BLE robot-control command")
            self._notify("ERR internal")
            return
        if error is not None:
            self._notify(error)

    def StartNotify(self) -> None:
        self._notifying = True

    def StopNotify(self) -> None:
        self._notifying = False

    def report_error(self, text: str) -> None:
        """Publish an asynchronous command failure."""
        self._notify(text)

    def _notify(self, text: str) -> None:
        data = list(_bounded_text(text).encode("utf-8"))
        self._value = data
        if not self._notifying:
            return
        try:
            self.PropertiesChanged(
                "org.bluez.GattCharacteristic1",
                {"Value": Variant("ay", data)},
                [],
            )
        except Exception:
            logger.exception(f"Failed to emit BLE robot-control notification: {text!r}")
            return
        logger.debug(f"Sent BLE robot-control notification: {text!r}")


@dbus_interface("org.bluez.GattCharacteristic1")
class WifiConfigCharacteristic(InterfaceTemplate):
    """Receive WiFi provisioning requests and notify progress and results."""

    def __init__(self, callback: WifiConfigCallback) -> None:
        super().__init__(self)
        self._callback = callback
        self._flags = ["read", "write", "notify"]
        self._notifying = False
        self._value: List[Byte] = []

    @property
    def UUID(self) -> Str:
        return WIFI_CONFIG_CHAR_UUID

    @property
    def Service(self) -> ObjPath:
        return SERVICE_PATH

    @property
    def Flags(self) -> List[Str]:
        return self._flags

    @property
    def Descriptors(self) -> List[ObjPath]:
        return [WIFI_CONFIG_CHARACTERISTIC_CCCD_PATH]

    @property
    def Value(self) -> List[Byte]:
        return self._value

    def ReadValue(self, options: Dict[Str, Variant]) -> List[Byte]:
        """Return the most recently published progress or result."""
        return _read_bytes(self._value, options)

    def WriteValue(self, value: List[Byte], options: Dict[Str, Variant]) -> None:
        """Handle a UTF-8 "<ssid>\n<password>" provisioning request."""
        try:
            text = bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            self._notify("ERR encoding")
            return

        try:
            ssid, password = parse_wifi_request(text)
        except WifiRequestError as exc:
            logger.warning(f"Invalid BLE WiFi config request: {exc}")
            self._notify("ERR invalid")
            return

        # Never log the password.
        logger.info(f"Received BLE WiFi config request: ssid={ssid!r}")
        try:
            error = self._callback(ssid, password)
        except Exception:
            logger.exception("Failed to dispatch BLE WiFi config request")
            self._notify("ERR internal")
            return
        if error is not None:
            self._notify(error)
            return
        self._notify(f"CONNECTING {ssid}")

    def StartNotify(self) -> None:
        self._notifying = True

    def StopNotify(self) -> None:
        self._notifying = False

    def report_result(self, text: str) -> None:
        """Publish the asynchronous provisioning result."""
        self._notify(text)

    def _notify(self, text: str) -> None:
        data = list(_bounded_text(text).encode("utf-8"))
        self._value = data
        if not self._notifying:
            return
        try:
            self.PropertiesChanged(
                "org.bluez.GattCharacteristic1",
                {"Value": Variant("ay", data)},
                [],
            )
        except Exception:
            logger.exception(f"Failed to emit BLE WiFi config notification: {text!r}")
            return
        logger.debug(f"Sent BLE WiFi config notification: {text!r}")


@dbus_interface("org.bluez.GattCharacteristic1")
class NavTaskCharacteristic(InterfaceTemplate):
    """Receive nav-task commands and notify replies and task events."""

    def __init__(self, callback: NavTaskCallback) -> None:
        super().__init__(self)
        self._callback = callback
        self._flags = ["read", "write", "notify"]
        self._notifying = False
        try:
            initial = callback("status")
        except Exception:
            logger.exception("Failed to query initial nav-task status")
            initial = ""
        self._value: List[Byte] = list(initial.encode("utf-8"))

    @property
    def UUID(self) -> Str:
        return NAV_TASK_CHAR_UUID

    @property
    def Service(self) -> ObjPath:
        return SERVICE_PATH

    @property
    def Flags(self) -> List[Str]:
        return self._flags

    @property
    def Descriptors(self) -> List[ObjPath]:
        return [NAV_TASK_CHARACTERISTIC_CCCD_PATH]

    @property
    def Value(self) -> List[Byte]:
        return self._value

    def ReadValue(self, options: Dict[Str, Variant]) -> List[Byte]:
        """Return the most recently published reply or task event."""
        return _read_bytes(self._value, options)

    def WriteValue(self, value: List[Byte], options: Dict[Str, Variant]) -> None:
        """Handle a UTF-8 START/STOP/STATUS nav-task command."""
        try:
            text = bytes(value).decode("utf-8").strip()
        except UnicodeDecodeError:
            self._notify("ERR encoding")
            return

        logger.info(f"Received BLE nav-task command: {text!r}")
        try:
            reply = self._callback(text)
        except Exception:
            logger.exception("Failed to dispatch BLE nav-task command")
            self._notify("ERR internal")
            return
        self._notify(reply)

    def StartNotify(self) -> None:
        self._notifying = True
        try:
            self._notify(self._callback("status"))
        except Exception:
            logger.exception("Failed to query nav-task status for notification")

    def StopNotify(self) -> None:
        self._notifying = False

    def report(self, text: str) -> None:
        """Publish an asynchronous task event (STOPPED/EXITED)."""
        self._notify(text)

    def _notify(self, text: str) -> None:
        data = list(_bounded_text(text).encode("utf-8"))
        self._value = data
        if not self._notifying:
            return
        try:
            self.PropertiesChanged(
                "org.bluez.GattCharacteristic1",
                {"Value": Variant("ay", data)},
                [],
            )
        except Exception:
            logger.exception(f"Failed to emit BLE nav-task notification: {text!r}")
            return
        logger.debug(f"Sent BLE nav-task notification: {text!r}")


@dbus_interface("org.bluez.GattCharacteristic1")
class ZoneNavCharacteristic(InterfaceTemplate):
    """Receive zone-navigation writes and notify the publish result."""

    def __init__(self, callback: ZoneNavCallback) -> None:
        super().__init__(self)
        self._callback = callback
        self._flags = ["read", "write", "notify"]
        self._notifying = False
        self._value: List[Byte] = []

    @property
    def UUID(self) -> Str:
        return ZONE_NAV_CHAR_UUID

    @property
    def Service(self) -> ObjPath:
        return SERVICE_PATH

    @property
    def Flags(self) -> List[Str]:
        return self._flags

    @property
    def Descriptors(self) -> List[ObjPath]:
        return [ZONE_NAV_CHARACTERISTIC_CCCD_PATH]

    @property
    def Value(self) -> List[Byte]:
        return self._value

    def ReadValue(self, options: Dict[Str, Variant]) -> List[Byte]:
        """Return the most recently published reply (empty when none)."""
        return _read_bytes(self._value, options)

    def WriteValue(self, value: List[Byte], options: Dict[Str, Variant]) -> None:
        """Handle a UTF-8 zone name; the reply is the final result."""
        try:
            zone = bytes(value).decode("utf-8").strip().lower()
        except UnicodeDecodeError:
            self._notify("ERR encoding")
            return

        logger.info(f"Received BLE zone-nav command: {zone!r}")
        if not zone:
            self._notify("ERR command")
            return
        try:
            reply = self._callback(zone)
        except Exception:
            logger.exception("Failed to dispatch BLE zone-nav command")
            self._notify("ERR internal")
            return
        self._notify(reply)

    def StartNotify(self) -> None:
        self._notifying = True

    def StopNotify(self) -> None:
        self._notifying = False

    def _notify(self, text: str) -> None:
        data = list(_bounded_text(text).encode("utf-8"))
        self._value = data
        if not self._notifying:
            return
        try:
            self.PropertiesChanged(
                "org.bluez.GattCharacteristic1",
                {"Value": Variant("ay", data)},
                [],
            )
        except Exception:
            logger.exception(f"Failed to emit BLE zone-nav notification: {text!r}")
            return
        logger.debug(f"Sent BLE zone-nav notification: {text!r}")


@dbus_interface("org.bluez.GattCharacteristic1")
class CmdVelCharacteristic(InterfaceTemplate):
    """Receive velocity-command writes and notify the publish result."""

    def __init__(self, callback: CmdVelCallback) -> None:
        super().__init__(self)
        self._callback = callback
        self._flags = ["read", "write", "notify"]
        self._notifying = False
        self._value: List[Byte] = []

    @property
    def UUID(self) -> Str:
        return CMD_VEL_CHAR_UUID

    @property
    def Service(self) -> ObjPath:
        return SERVICE_PATH

    @property
    def Flags(self) -> List[Str]:
        return self._flags

    @property
    def Descriptors(self) -> List[ObjPath]:
        return [CMD_VEL_CHARACTERISTIC_CCCD_PATH]

    @property
    def Value(self) -> List[Byte]:
        return self._value

    def ReadValue(self, options: Dict[Str, Variant]) -> List[Byte]:
        """Return the most recently published reply (empty when none)."""
        return _read_bytes(self._value, options)

    def WriteValue(self, value: List[Byte], options: Dict[Str, Variant]) -> None:
        """Handle a UTF-8 "<linear_x> <angular_z>" write; one write, one Twist."""
        try:
            text = bytes(value).decode("utf-8").strip()
        except UnicodeDecodeError:
            self._notify("ERR encoding")
            return

        # Writes can arrive in quick succession (joystick-style control);
        # identical values must still publish, so only reject empty input.
        if not text:
            self._notify("ERR command")
            return
        logger.info(f"Received BLE cmd-vel command: {text!r}")
        try:
            reply = self._callback(text)
        except Exception:
            logger.exception("Failed to dispatch BLE cmd-vel command")
            self._notify("ERR internal")
            return
        self._notify(reply)

    def StartNotify(self) -> None:
        self._notifying = True

    def StopNotify(self) -> None:
        self._notifying = False

    def _notify(self, text: str) -> None:
        data = list(_bounded_text(text).encode("utf-8"))
        self._value = data
        if not self._notifying:
            return
        try:
            self.PropertiesChanged(
                "org.bluez.GattCharacteristic1",
                {"Value": Variant("ay", data)},
                [],
            )
        except Exception:
            logger.exception(f"Failed to emit BLE cmd-vel notification: {text!r}")
            return
        logger.debug(f"Sent BLE cmd-vel notification: {text!r}")


@dbus_interface("org.bluez.GattCharacteristic1")
class MemoryStatusCharacteristic(InterfaceTemplate):
    """Publish whole-machine memory occupancy; notify only on change."""

    def __init__(self) -> None:
        super().__init__(self)
        self._flags = ["read", "notify"]
        self._notifying = False
        # UNKNOWN until the memory provider delivers its first reading.
        self._value: List[Byte] = list(b"UNKNOWN")

    @property
    def UUID(self) -> Str:
        return MEMORY_CHAR_UUID

    @property
    def Service(self) -> ObjPath:
        return SERVICE_PATH

    @property
    def Flags(self) -> List[Str]:
        return self._flags

    @property
    def Descriptors(self) -> List[ObjPath]:
        return [MEMORY_CHARACTERISTIC_CCCD_PATH]

    @property
    def Value(self) -> List[Byte]:
        return self._value

    def ReadValue(self, options: Dict[Str, Variant]) -> List[Byte]:
        return _read_bytes(self._value, options)

    def StartNotify(self) -> None:
        self._notifying = True
        self._notify()

    def StopNotify(self) -> None:
        self._notifying = False

    def update_usage(
        self,
        used_mb: int | None,
        total_mb: int | None,
        percent: float | None,
        notify: bool = True,
    ) -> None:
        text = (
            "UNKNOWN"
            if used_mb is None or total_mb is None or percent is None
            else f"MEM {used_mb} {total_mb} {percent:.1f}"
        )
        self._value = list(_bounded_text(text).encode("utf-8"))
        if notify and self._notifying:
            self._notify()

    def _notify(self) -> None:
        try:
            self.PropertiesChanged(
                "org.bluez.GattCharacteristic1",
                {"Value": Variant("ay", self._value)},
                [],
            )
        except Exception:
            logger.exception("Failed to emit BLE memory notification")
            return
        logger.debug(f"Sent BLE memory usage: {bytes(self._value)!r}")


@dbus_interface("org.bluez.GattCharacteristic1")
class ZoneVoiceCharacteristic(InterfaceTemplate):
    """Receive zone-voice commands and notify replies and status changes."""

    def __init__(self, callback: ZoneVoiceCallback) -> None:
        super().__init__(self)
        self._callback = callback
        self._flags = ["read", "write", "notify"]
        self._notifying = False
        try:
            initial = callback("status")
        except Exception:
            logger.exception("Failed to query initial zone-voice status")
            initial = "UNKNOWN"
        self._value: List[Byte] = list(initial.encode("utf-8"))

    @property
    def UUID(self) -> Str:
        return ZONE_VOICE_CHAR_UUID

    @property
    def Service(self) -> ObjPath:
        return SERVICE_PATH

    @property
    def Flags(self) -> List[Str]:
        return self._flags

    @property
    def Descriptors(self) -> List[ObjPath]:
        return [ZONE_VOICE_CHARACTERISTIC_CCCD_PATH]

    @property
    def Value(self) -> List[Byte]:
        return self._value

    def ReadValue(self, options: Dict[Str, Variant]) -> List[Byte]:
        """Return the most recently published reply or status."""
        return _read_bytes(self._value, options)

    def WriteValue(self, value: List[Byte], options: Dict[Str, Variant]) -> None:
        """Handle a UTF-8 LIST/PLAY/STOP/STATUS zone-voice command."""
        try:
            text = bytes(value).decode("utf-8").strip()
        except UnicodeDecodeError:
            self._notify("ERR encoding")
            return

        logger.info(f"Received BLE zone-voice command: {text!r}")
        if not text:
            self._notify("ERR command")
            return
        try:
            reply = self._callback(text)
        except Exception:
            logger.exception("Failed to dispatch BLE zone-voice command")
            self._notify("ERR internal")
            return
        self._notify(reply)

    def StartNotify(self) -> None:
        self._notifying = True
        try:
            self._notify(self._callback("status"))
        except Exception:
            logger.exception("Failed to query zone-voice status for notification")

    def StopNotify(self) -> None:
        self._notifying = False

    def report(self, text: str) -> None:
        """Publish an asynchronous status change (PLAYING/IDLE/UNKNOWN)."""
        self._notify(text)

    def _notify(self, text: str) -> None:
        data = list(_bounded_text(text).encode("utf-8"))
        self._value = data
        if not self._notifying:
            return
        try:
            self.PropertiesChanged(
                "org.bluez.GattCharacteristic1",
                {"Value": Variant("ay", data)},
                [],
            )
        except Exception:
            logger.exception(f"Failed to emit BLE zone-voice notification: {text!r}")
            return
        logger.debug(f"Sent BLE zone-voice notification: {text!r}")


@dbus_interface("org.bluez.GattCharacteristic1")
class InitialPoseCharacteristic(InterfaceTemplate):
    """Receive initial-pose triggers and notify the publish result."""

    def __init__(self, callback: InitialPoseCallback) -> None:
        super().__init__(self)
        self._callback = callback
        self._flags = ["read", "write", "notify"]
        self._notifying = False
        self._value: List[Byte] = []

    @property
    def UUID(self) -> Str:
        return INITIAL_POSE_CHAR_UUID

    @property
    def Service(self) -> ObjPath:
        return SERVICE_PATH

    @property
    def Flags(self) -> List[Str]:
        return self._flags

    @property
    def Descriptors(self) -> List[ObjPath]:
        return [INITIAL_POSE_CHARACTERISTIC_CCCD_PATH]

    @property
    def Value(self) -> List[Byte]:
        return self._value

    def ReadValue(self, options: Dict[Str, Variant]) -> List[Byte]:
        """Return the most recently published reply (empty when none)."""
        return _read_bytes(self._value, options)

    def WriteValue(self, value: List[Byte], options: Dict[Str, Variant]) -> None:
        """Handle any non-empty UTF-8 write as "publish the fixed initial pose".

        The payload itself is meaningless; the App only signals intent.
        """
        try:
            text = bytes(value).decode("utf-8").strip()
        except UnicodeDecodeError:
            self._notify("ERR encoding")
            return

        if not text:
            self._notify("ERR command")
            return
        logger.info(f"Received BLE initial-pose trigger: {text!r}")
        try:
            reply = self._callback(text)
        except Exception:
            logger.exception("Failed to dispatch BLE initial-pose command")
            self._notify("ERR internal")
            return
        self._notify(reply)

    def StartNotify(self) -> None:
        self._notifying = True

    def StopNotify(self) -> None:
        self._notifying = False

    def _notify(self, text: str) -> None:
        data = list(_bounded_text(text).encode("utf-8"))
        self._value = data
        if not self._notifying:
            return
        try:
            self.PropertiesChanged(
                "org.bluez.GattCharacteristic1",
                {"Value": Variant("ay", data)},
                [],
            )
        except Exception:
            logger.exception(
                f"Failed to emit BLE initial-pose notification: {text!r}"
            )
            return
        logger.debug(f"Sent BLE initial-pose notification: {text!r}")


@dbus_interface("org.bluez.GattDescriptor1")
class ClientCharacteristicConfigurationDescriptor(InterfaceTemplate):
    """Expose the standard descriptor clients write to enable notifications."""

    def __init__(
        self,
        characteristic_path: str,
        start_notify: Callable[[], None],
        stop_notify: Callable[[], None],
    ) -> None:
        super().__init__(self)
        self._characteristic_path = characteristic_path
        self._start_notify = start_notify
        self._stop_notify = stop_notify
        self._value: List[Byte] = [0x00, 0x00]

    @property
    def UUID(self) -> Str:
        return CCCD_UUID

    @property
    def Characteristic(self) -> ObjPath:
        return self._characteristic_path

    @property
    def Flags(self) -> List[Str]:
        return ["read", "write"]

    @property
    def Value(self) -> List[Byte]:
        return self._value

    def ReadValue(self, options: Dict[Str, Variant]) -> List[Byte]:
        return _read_bytes(self._value, options)

    def WriteValue(self, value: List[Byte], options: Dict[Str, Variant]) -> None:
        self._value = list(value[:2])
        enabled = self._value == [0x01, 0x00]
        disabled = self._value == [0x00, 0x00]
        if enabled:
            self._start_notify()
        elif disabled:
            self._stop_notify()


@dbus_interface("org.bluez.GattService1")
class ControlService(InterfaceTemplate):
    """Primary GATT service containing the control characteristic."""

    def __init__(self) -> None:
        super().__init__(self)

    @property
    def UUID(self) -> Str:
        return SERVICE_UUID

    @property
    def Primary(self) -> Bool:
        return True

    @property
    def Characteristics(self) -> List[ObjPath]:
        return [
            CHARACTERISTIC_PATH,
            URL_CHARACTERISTIC_PATH,
            ERROR_CHARACTERISTIC_PATH,
            BATTERY_STATUS_CHARACTERISTIC_PATH,
            NETWORK_CHARACTERISTIC_PATH,
            CPU_CHARACTERISTIC_PATH,
            BANDWIDTH_CHARACTERISTIC_PATH,
            LATENCY_CHARACTERISTIC_PATH,
            ROBOT_CONTROL_CHARACTERISTIC_PATH,
            WIFI_CONFIG_CHARACTERISTIC_PATH,
            NAV_TASK_CHARACTERISTIC_PATH,
            ZONE_NAV_CHARACTERISTIC_PATH,
            CMD_VEL_CHARACTERISTIC_PATH,
            MEMORY_CHARACTERISTIC_PATH,
            ZONE_VOICE_CHARACTERISTIC_PATH,
            INITIAL_POSE_CHARACTERISTIC_PATH,
        ]


@dbus_interface("org.bluez.LEAdvertisement1")
class ControlAdvertisement(InterfaceTemplate):
    """Connectable BLE advertisement for the control service."""

    def __init__(
        self,
        local_name: str,
        on_release: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(self)
        self._local_name = local_name
        self._on_release = on_release

    @property
    def Type(self) -> Str:
        return "peripheral"

    @property
    def ServiceUUIDs(self) -> List[Str]:
        return [SERVICE_UUID]

    @property
    def LocalName(self) -> Str:
        return self._local_name

    @property
    def IncludeTxPower(self) -> Bool:
        return True

    def Release(self) -> None:
        """Handle BlueZ releasing the advertisement.

        BlueZ also calls this when we UnregisterAdvertisement ourselves.
        The server handler decides whether the release is unexpected.
        """
        if self._on_release is not None:
            try:
                self._on_release()
            except Exception:
                logger.exception("Failed to schedule BLE advertisement recovery")


@dbus_interface("org.freedesktop.DBus.ObjectManager")
class GattApplication(InterfaceTemplate):
    """Expose the complete GATT object tree to BlueZ."""

    def __init__(self) -> None:
        super().__init__(self)

    def GetManagedObjects(
        self,
    ) -> Dict[ObjPath, Dict[Str, Dict[Str, Variant]]]:
        """Return the service and characteristic properties."""
        return {
            SERVICE_PATH: {
                "org.bluez.GattService1": {
                    "UUID": Variant("s", SERVICE_UUID),
                    "Primary": Variant("b", True),
                    "Characteristics": Variant(
                        "ao",
                        [
                            CHARACTERISTIC_PATH,
                            URL_CHARACTERISTIC_PATH,
                            ERROR_CHARACTERISTIC_PATH,
                            BATTERY_STATUS_CHARACTERISTIC_PATH,
                            NETWORK_CHARACTERISTIC_PATH,
                            CPU_CHARACTERISTIC_PATH,
                            BANDWIDTH_CHARACTERISTIC_PATH,
                            LATENCY_CHARACTERISTIC_PATH,
                            ROBOT_CONTROL_CHARACTERISTIC_PATH,
                            WIFI_CONFIG_CHARACTERISTIC_PATH,
                            NAV_TASK_CHARACTERISTIC_PATH,
                            ZONE_NAV_CHARACTERISTIC_PATH,
                            CMD_VEL_CHARACTERISTIC_PATH,
                            MEMORY_CHARACTERISTIC_PATH,
                            ZONE_VOICE_CHARACTERISTIC_PATH,
                            INITIAL_POSE_CHARACTERISTIC_PATH,
                        ],
                    ),
                }
            },
            CHARACTERISTIC_PATH: {
                "org.bluez.GattCharacteristic1": {
                    "UUID": Variant("s", COMMAND_CHAR_UUID),
                    "Service": Variant("o", SERVICE_PATH),
                    "Flags": Variant("as", ["read", "write", "notify"]),
                    "Descriptors": Variant("ao", [CHARACTERISTIC_CCCD_PATH]),
                }
            },
            URL_CHARACTERISTIC_PATH: {
                "org.bluez.GattCharacteristic1": {
                    "UUID": Variant("s", URL_CHAR_UUID),
                    "Service": Variant("o", SERVICE_PATH),
                    "Flags": Variant(
                        "as",
                        ["read", "write", "notify"],
                    ),
                    "Descriptors": Variant("ao", [URL_CHARACTERISTIC_CCCD_PATH]),
                }
            },
            ERROR_CHARACTERISTIC_PATH: {
                "org.bluez.GattCharacteristic1": {
                    "UUID": Variant("s", ERROR_CHAR_UUID),
                    "Service": Variant("o", SERVICE_PATH),
                    "Flags": Variant("as", ["read", "notify"]),
                    "Descriptors": Variant("ao", [ERROR_CHARACTERISTIC_CCCD_PATH]),
                }
            },
            BATTERY_STATUS_CHARACTERISTIC_PATH: {
                "org.bluez.GattCharacteristic1": {
                    "UUID": Variant("s", BATTERY_STATUS_CHAR_UUID),
                    "Service": Variant("o", SERVICE_PATH),
                    "Flags": Variant("as", ["read", "notify"]),
                    "Descriptors": Variant(
                        "ao", [BATTERY_STATUS_CHARACTERISTIC_CCCD_PATH]
                    ),
                }
            },
            NETWORK_CHARACTERISTIC_PATH: {
                "org.bluez.GattCharacteristic1": {
                    "UUID": Variant("s", NETWORK_CHAR_UUID),
                    "Service": Variant("o", SERVICE_PATH),
                    "Flags": Variant("as", ["read", "notify"]),
                    "Descriptors": Variant("ao", [NETWORK_CHARACTERISTIC_CCCD_PATH]),
                }
            },
            CPU_CHARACTERISTIC_PATH: {
                "org.bluez.GattCharacteristic1": {
                    "UUID": Variant("s", CPU_CHAR_UUID),
                    "Service": Variant("o", SERVICE_PATH),
                    "Flags": Variant("as", ["read", "notify"]),
                    "Descriptors": Variant("ao", [CPU_CHARACTERISTIC_CCCD_PATH]),
                }
            },
            BANDWIDTH_CHARACTERISTIC_PATH: {
                "org.bluez.GattCharacteristic1": {
                    "UUID": Variant("s", BANDWIDTH_CHAR_UUID),
                    "Service": Variant("o", SERVICE_PATH),
                    "Flags": Variant("as", ["read", "notify"]),
                    "Descriptors": Variant(
                        "ao", [BANDWIDTH_CHARACTERISTIC_CCCD_PATH]
                    ),
                }
            },
            LATENCY_CHARACTERISTIC_PATH: {
                "org.bluez.GattCharacteristic1": {
                    "UUID": Variant("s", LATENCY_CHAR_UUID),
                    "Service": Variant("o", SERVICE_PATH),
                    "Flags": Variant("as", ["read", "notify"]),
                    "Descriptors": Variant(
                        "ao", [LATENCY_CHARACTERISTIC_CCCD_PATH]
                    ),
                }
            },
            ROBOT_CONTROL_CHARACTERISTIC_PATH: {
                "org.bluez.GattCharacteristic1": {
                    "UUID": Variant("s", ROBOT_CONTROL_CHAR_UUID),
                    "Service": Variant("o", SERVICE_PATH),
                    "Flags": Variant("as", ["read", "write", "notify"]),
                    "Descriptors": Variant(
                        "ao", [ROBOT_CONTROL_CHARACTERISTIC_CCCD_PATH]
                    ),
                }
            },
            WIFI_CONFIG_CHARACTERISTIC_PATH: {
                "org.bluez.GattCharacteristic1": {
                    "UUID": Variant("s", WIFI_CONFIG_CHAR_UUID),
                    "Service": Variant("o", SERVICE_PATH),
                    "Flags": Variant("as", ["read", "write", "notify"]),
                    "Descriptors": Variant(
                        "ao", [WIFI_CONFIG_CHARACTERISTIC_CCCD_PATH]
                    ),
                }
            },
            NAV_TASK_CHARACTERISTIC_PATH: {
                "org.bluez.GattCharacteristic1": {
                    "UUID": Variant("s", NAV_TASK_CHAR_UUID),
                    "Service": Variant("o", SERVICE_PATH),
                    "Flags": Variant("as", ["read", "write", "notify"]),
                    "Descriptors": Variant(
                        "ao", [NAV_TASK_CHARACTERISTIC_CCCD_PATH]
                    ),
                }
            },
            ZONE_NAV_CHARACTERISTIC_PATH: {
                "org.bluez.GattCharacteristic1": {
                    "UUID": Variant("s", ZONE_NAV_CHAR_UUID),
                    "Service": Variant("o", SERVICE_PATH),
                    "Flags": Variant("as", ["read", "write", "notify"]),
                    "Descriptors": Variant(
                        "ao", [ZONE_NAV_CHARACTERISTIC_CCCD_PATH]
                    ),
                }
            },
            CMD_VEL_CHARACTERISTIC_PATH: {
                "org.bluez.GattCharacteristic1": {
                    "UUID": Variant("s", CMD_VEL_CHAR_UUID),
                    "Service": Variant("o", SERVICE_PATH),
                    "Flags": Variant("as", ["read", "write", "notify"]),
                    "Descriptors": Variant(
                        "ao", [CMD_VEL_CHARACTERISTIC_CCCD_PATH]
                    ),
                }
            },
            MEMORY_CHARACTERISTIC_PATH: {
                "org.bluez.GattCharacteristic1": {
                    "UUID": Variant("s", MEMORY_CHAR_UUID),
                    "Service": Variant("o", SERVICE_PATH),
                    "Flags": Variant("as", ["read", "notify"]),
                    "Descriptors": Variant("ao", [MEMORY_CHARACTERISTIC_CCCD_PATH]),
                }
            },
            ZONE_VOICE_CHARACTERISTIC_PATH: {
                "org.bluez.GattCharacteristic1": {
                    "UUID": Variant("s", ZONE_VOICE_CHAR_UUID),
                    "Service": Variant("o", SERVICE_PATH),
                    "Flags": Variant("as", ["read", "write", "notify"]),
                    "Descriptors": Variant(
                        "ao", [ZONE_VOICE_CHARACTERISTIC_CCCD_PATH]
                    ),
                }
            },
            INITIAL_POSE_CHARACTERISTIC_PATH: {
                "org.bluez.GattCharacteristic1": {
                    "UUID": Variant("s", INITIAL_POSE_CHAR_UUID),
                    "Service": Variant("o", SERVICE_PATH),
                    "Flags": Variant("as", ["read", "write", "notify"]),
                    "Descriptors": Variant(
                        "ao", [INITIAL_POSE_CHARACTERISTIC_CCCD_PATH]
                    ),
                }
            },
            CHARACTERISTIC_CCCD_PATH: {
                "org.bluez.GattDescriptor1": {
                    "UUID": Variant("s", CCCD_UUID),
                    "Characteristic": Variant("o", CHARACTERISTIC_PATH),
                    "Flags": Variant("as", ["read", "write"]),
                }
            },
            URL_CHARACTERISTIC_CCCD_PATH: {
                "org.bluez.GattDescriptor1": {
                    "UUID": Variant("s", CCCD_UUID),
                    "Characteristic": Variant("o", URL_CHARACTERISTIC_PATH),
                    "Flags": Variant("as", ["read", "write"]),
                }
            },
            ERROR_CHARACTERISTIC_CCCD_PATH: {
                "org.bluez.GattDescriptor1": {
                    "UUID": Variant("s", CCCD_UUID),
                    "Characteristic": Variant("o", ERROR_CHARACTERISTIC_PATH),
                    "Flags": Variant("as", ["read", "write"]),
                }
            },
            BATTERY_STATUS_CHARACTERISTIC_CCCD_PATH: {
                "org.bluez.GattDescriptor1": {
                    "UUID": Variant("s", CCCD_UUID),
                    "Characteristic": Variant("o", BATTERY_STATUS_CHARACTERISTIC_PATH),
                    "Flags": Variant("as", ["read", "write"]),
                }
            },
            NETWORK_CHARACTERISTIC_CCCD_PATH: {
                "org.bluez.GattDescriptor1": {
                    "UUID": Variant("s", CCCD_UUID),
                    "Characteristic": Variant("o", NETWORK_CHARACTERISTIC_PATH),
                    "Flags": Variant("as", ["read", "write"]),
                }
            },
            CPU_CHARACTERISTIC_CCCD_PATH: {
                "org.bluez.GattDescriptor1": {
                    "UUID": Variant("s", CCCD_UUID),
                    "Characteristic": Variant("o", CPU_CHARACTERISTIC_PATH),
                    "Flags": Variant("as", ["read", "write"]),
                }
            },
            BANDWIDTH_CHARACTERISTIC_CCCD_PATH: {
                "org.bluez.GattDescriptor1": {
                    "UUID": Variant("s", CCCD_UUID),
                    "Characteristic": Variant("o", BANDWIDTH_CHARACTERISTIC_PATH),
                    "Flags": Variant("as", ["read", "write"]),
                }
            },
            LATENCY_CHARACTERISTIC_CCCD_PATH: {
                "org.bluez.GattDescriptor1": {
                    "UUID": Variant("s", CCCD_UUID),
                    "Characteristic": Variant("o", LATENCY_CHARACTERISTIC_PATH),
                    "Flags": Variant("as", ["read", "write"]),
                }
            },
            ROBOT_CONTROL_CHARACTERISTIC_CCCD_PATH: {
                "org.bluez.GattDescriptor1": {
                    "UUID": Variant("s", CCCD_UUID),
                    "Characteristic": Variant("o", ROBOT_CONTROL_CHARACTERISTIC_PATH),
                    "Flags": Variant("as", ["read", "write"]),
                }
            },
            WIFI_CONFIG_CHARACTERISTIC_CCCD_PATH: {
                "org.bluez.GattDescriptor1": {
                    "UUID": Variant("s", CCCD_UUID),
                    "Characteristic": Variant("o", WIFI_CONFIG_CHARACTERISTIC_PATH),
                    "Flags": Variant("as", ["read", "write"]),
                }
            },
            NAV_TASK_CHARACTERISTIC_CCCD_PATH: {
                "org.bluez.GattDescriptor1": {
                    "UUID": Variant("s", CCCD_UUID),
                    "Characteristic": Variant("o", NAV_TASK_CHARACTERISTIC_PATH),
                    "Flags": Variant("as", ["read", "write"]),
                }
            },
            ZONE_NAV_CHARACTERISTIC_CCCD_PATH: {
                "org.bluez.GattDescriptor1": {
                    "UUID": Variant("s", CCCD_UUID),
                    "Characteristic": Variant("o", ZONE_NAV_CHARACTERISTIC_PATH),
                    "Flags": Variant("as", ["read", "write"]),
                }
            },
            CMD_VEL_CHARACTERISTIC_CCCD_PATH: {
                "org.bluez.GattDescriptor1": {
                    "UUID": Variant("s", CCCD_UUID),
                    "Characteristic": Variant("o", CMD_VEL_CHARACTERISTIC_PATH),
                    "Flags": Variant("as", ["read", "write"]),
                }
            },
            MEMORY_CHARACTERISTIC_CCCD_PATH: {
                "org.bluez.GattDescriptor1": {
                    "UUID": Variant("s", CCCD_UUID),
                    "Characteristic": Variant("o", MEMORY_CHARACTERISTIC_PATH),
                    "Flags": Variant("as", ["read", "write"]),
                }
            },
            ZONE_VOICE_CHARACTERISTIC_CCCD_PATH: {
                "org.bluez.GattDescriptor1": {
                    "UUID": Variant("s", CCCD_UUID),
                    "Characteristic": Variant("o", ZONE_VOICE_CHARACTERISTIC_PATH),
                    "Flags": Variant("as", ["read", "write"]),
                }
            },
            INITIAL_POSE_CHARACTERISTIC_CCCD_PATH: {
                "org.bluez.GattDescriptor1": {
                    "UUID": Variant("s", CCCD_UUID),
                    "Characteristic": Variant("o", INITIAL_POSE_CHARACTERISTIC_PATH),
                    "Flags": Variant("as", ["read", "write"]),
                }
            },
        }


class BleControlServer:
    """Own the BlueZ GATT service and its dedicated GLib event-loop thread."""

    def __init__(
        self,
        callback: CommandCallback,
        adapter_path: str = "/org/bluez/hci0",
        device_name: str = "Xiaozhi",
        initial_state: str = "idle",
        initial_url: str = "",
        initial_error: tuple[str, str] | None = None,
        initial_battery_status: tuple[float | None, str | None] = (None, None),
        url_callback: UrlCallback | None = None,
        robot_control_callback: RobotControlCallback | None = None,
        wifi_config_callback: WifiConfigCallback | None = None,
        nav_task_callback: NavTaskCallback | None = None,
        zone_nav_callback: ZoneNavCallback | None = None,
        cmd_vel_callback: CmdVelCallback | None = None,
        zone_voice_callback: ZoneVoiceCallback | None = None,
        initial_pose_callback: InitialPoseCallback | None = None,
    ) -> None:
        self._callback = callback
        self._url_callback = url_callback or (lambda _url: None)
        self._robot_control_callback = robot_control_callback or (
            lambda _command: "ERR unavailable"
        )
        self._wifi_config_callback = wifi_config_callback or (
            lambda _ssid, _password: "ERR unavailable"
        )
        self._nav_task_callback = nav_task_callback or (
            lambda _text: "ERR unavailable"
        )
        self._zone_nav_callback = zone_nav_callback or (
            lambda _zone: "ERR unavailable"
        )
        self._cmd_vel_callback = cmd_vel_callback or (
            lambda _text: "ERR unavailable"
        )
        def _unavailable_zone_voice(text: str) -> str:
            verb = text.strip().split(None, 1)[0].lower() if text.strip() else ""
            if verb == "status":
                return "UNKNOWN"
            return "ERR unavailable"

        self._zone_voice_callback = zone_voice_callback or _unavailable_zone_voice
        self._initial_pose_callback = initial_pose_callback or (
            lambda _text: "ERR unavailable"
        )
        self._adapter_path = adapter_path
        self._device_name = device_name
        self._initial_state = initial_state
        self._initial_url = initial_url
        self._initial_error = initial_error
        self._initial_battery_status = initial_battery_status
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._registered = False
        self._gatt_registered = False
        self._advertisement_registered = False
        self._advertisement_refresh_pending = False
        self._advertisement_refresh_count = 0
        self._shutdown_requested = False
        self._error: str | None = None
        self._bus: SystemMessageBus | None = None
        self._event_loop: EventLoop | None = None
        self._dbus_daemon_proxy = None
        self._gatt_manager = None
        self._advertising_manager = None
        self._characteristic: CommandCharacteristic | None = None
        self._url_characteristic: WebsocketUrlCharacteristic | None = None
        self._error_characteristic: ErrorCharacteristic | None = None
        self._battery_characteristic: BatteryStatusCharacteristic | None = None
        self._network_characteristic: NetworkStatusCharacteristic | None = None
        self._cpu_characteristic: CpuStatusCharacteristic | None = None
        self._bandwidth_characteristic: BandwidthStatusCharacteristic | None = None
        self._latency_characteristic: LatencyStatusCharacteristic | None = None
        self._robot_control_characteristic: RobotControlCharacteristic | None = None
        self._wifi_config_characteristic: WifiConfigCharacteristic | None = None
        self._nav_task_characteristic: NavTaskCharacteristic | None = None
        self._zone_nav_characteristic: ZoneNavCharacteristic | None = None
        self._cmd_vel_characteristic: CmdVelCharacteristic | None = None
        self._memory_characteristic: MemoryStatusCharacteristic | None = None
        self._zone_voice_characteristic: ZoneVoiceCharacteristic | None = None
        self._initial_pose_characteristic: InitialPoseCharacteristic | None = None

    @property
    def error(self) -> str | None:
        return self._error

    def start(self, timeout: float = 5.0) -> bool:
        """Start the server and wait until BlueZ registration completes."""
        if self._thread is not None and self._thread.is_alive():
            return self._registered

        self._ready.clear()
        self._registered = False
        self._gatt_registered = False
        self._advertisement_registered = False
        self._advertisement_refresh_pending = False
        self._advertisement_refresh_count = 0
        self._shutdown_requested = False
        self._error = None
        self._thread = threading.Thread(
            target=self._run,
            name="xiaozhi-ble",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout):
            self._error = f"BLE server startup timed out after {timeout:.1f}s"
            logger.error(self._error)
            return False
        return self._registered

    def stop(self, timeout: float = 5.0) -> None:
        """Unregister the service and stop its GLib event loop."""
        if self._thread is None or not self._thread.is_alive():
            return
        GLib.idle_add(self._begin_shutdown)
        self._thread.join(timeout)
        if self._thread.is_alive():
            logger.warning(f"BLE server did not stop within {timeout:.1f}s")

    def notify_state(self, state: str) -> None:
        """Publish an application state from any thread."""
        if self._characteristic is None:
            return

        def update() -> bool:
            if self._characteristic is not None:
                self._characteristic.update_state(state)
            return False

        GLib.idle_add(update)

    def notify_websocket_url(self, url: str) -> None:
        """Publish the URL after it has been persisted and applied."""
        if self._url_characteristic is None:
            return

        def update() -> bool:
            if self._url_characteristic is not None:
                self._url_characteristic.update_url(url)
            return False

        GLib.idle_add(update)

    def notify_error(self, code: str, message: str) -> None:
        """Publish an error from any thread."""
        if self._error_characteristic is None:
            return

        def update() -> bool:
            if self._error_characteristic is not None:
                self._error_characteristic.publish(code, message)
            return False

        GLib.idle_add(update)

    def notify_battery_status(
        self,
        percentage: float | None,
        supply_status: str | None,
    ) -> None:
        """Update the cached battery status from any thread."""
        if self._battery_characteristic is None:
            return

        def update() -> bool:
            if self._battery_characteristic is not None:
                self._battery_characteristic.update_status(
                    percentage, supply_status, notify=False
                )
            return False

        GLib.idle_add(update)

    def notify_cpu_usage(self, usage: float | None) -> None:
        """Update the CPU usage from any thread (notifies on change)."""
        if self._cpu_characteristic is None:
            return

        def update() -> bool:
            if self._cpu_characteristic is not None:
                self._cpu_characteristic.update_usage(usage)
            return False

        GLib.idle_add(update)

    def notify_memory_usage(
        self,
        used_mb: int,
        total_mb: int,
        percent: float,
    ) -> None:
        """Update the memory occupancy from any thread (notifies on change)."""
        if self._memory_characteristic is None:
            return

        def update() -> bool:
            if self._memory_characteristic is not None:
                self._memory_characteristic.update_usage(used_mb, total_mb, percent)
            return False

        GLib.idle_add(update)

    def notify_network_status(
        self,
        ssid: str | None,
        rssi_dbm: int | None,
        ipv4: str | None,
    ) -> None:
        """Update the WiFi link status from any thread (notifies on change)."""
        if self._network_characteristic is None:
            return

        def update() -> bool:
            if self._network_characteristic is not None:
                self._network_characteristic.update_status(ssid, rssi_dbm, ipv4)
            return False

        GLib.idle_add(update)

    def notify_bandwidth(self, rx_kbps: float, tx_kbps: float) -> None:
        """Update the WiFi throughput from any thread (notifies on change)."""
        if self._bandwidth_characteristic is None:
            return

        def update() -> bool:
            if self._bandwidth_characteristic is not None:
                self._bandwidth_characteristic.update_bandwidth(rx_kbps, tx_kbps)
            return False

        GLib.idle_add(update)

    def notify_latency(self, latency_ms: float | None) -> None:
        """Update the dialogue-server latency from any thread."""
        if self._latency_characteristic is None:
            return

        def update() -> bool:
            if self._latency_characteristic is not None:
                self._latency_characteristic.update_latency(latency_ms)
            return False

        GLib.idle_add(update)

    def notify_robot_control_error(self, text: str) -> None:
        """Publish an asynchronous robot-control failure from any thread."""
        if self._robot_control_characteristic is None:
            return

        def update() -> bool:
            if self._robot_control_characteristic is not None:
                self._robot_control_characteristic.report_error(text)
            return False

        GLib.idle_add(update)

    def notify_wifi_config_result(self, text: str) -> None:
        """Publish an asynchronous WiFi provisioning result from any thread."""
        if self._wifi_config_characteristic is None:
            return

        def update() -> bool:
            if self._wifi_config_characteristic is not None:
                self._wifi_config_characteristic.report_result(text)
            return False

        GLib.idle_add(update)

    def notify_nav_task(self, text: str) -> None:
        """Publish an asynchronous nav-task event from any thread."""
        if self._nav_task_characteristic is None:
            return

        def update() -> bool:
            if self._nav_task_characteristic is not None:
                self._nav_task_characteristic.report(text)
            return False

        GLib.idle_add(update)

    def notify_zone_voice(self, text: str) -> None:
        """Publish an asynchronous zone-voice status change from any thread."""
        if self._zone_voice_characteristic is None:
            return

        def update() -> bool:
            if self._zone_voice_characteristic is not None:
                self._zone_voice_characteristic.report(text)
            return False

        GLib.idle_add(update)

    def _run(self) -> None:
        try:
            self._bus = SystemMessageBus()
            self._event_loop = EventLoop()
            self._characteristic = CommandCharacteristic(
                self._callback,
                initial_state=self._initial_state,
                on_disconnect=self._on_client_disconnected,
            )
            self._url_characteristic = WebsocketUrlCharacteristic(
                self._url_callback,
                self._initial_url,
                self._publish_local_error,
            )
            self._error_characteristic = ErrorCharacteristic(self._initial_error)
            self._battery_characteristic = BatteryStatusCharacteristic(
                *self._initial_battery_status
            )
            self._network_characteristic = NetworkStatusCharacteristic()
            self._cpu_characteristic = CpuStatusCharacteristic()
            self._bandwidth_characteristic = BandwidthStatusCharacteristic()
            self._latency_characteristic = LatencyStatusCharacteristic()
            self._robot_control_characteristic = RobotControlCharacteristic(
                self._robot_control_callback
            )
            self._wifi_config_characteristic = WifiConfigCharacteristic(
                self._wifi_config_callback
            )
            self._nav_task_characteristic = NavTaskCharacteristic(
                self._nav_task_callback
            )
            self._zone_nav_characteristic = ZoneNavCharacteristic(
                self._zone_nav_callback
            )
            self._cmd_vel_characteristic = CmdVelCharacteristic(
                self._cmd_vel_callback
            )
            self._memory_characteristic = MemoryStatusCharacteristic()
            self._zone_voice_characteristic = ZoneVoiceCharacteristic(
                self._zone_voice_callback
            )
            self._initial_pose_characteristic = InitialPoseCharacteristic(
                self._initial_pose_callback
            )
            command_cccd = ClientCharacteristicConfigurationDescriptor(
                CHARACTERISTIC_PATH,
                self._characteristic.StartNotify,
                self._characteristic.StopNotify,
            )
            url_cccd = ClientCharacteristicConfigurationDescriptor(
                URL_CHARACTERISTIC_PATH,
                self._url_characteristic.StartNotify,
                self._url_characteristic.StopNotify,
            )
            error_cccd = ClientCharacteristicConfigurationDescriptor(
                ERROR_CHARACTERISTIC_PATH,
                self._error_characteristic.StartNotify,
                self._error_characteristic.StopNotify,
            )
            battery_cccd = ClientCharacteristicConfigurationDescriptor(
                BATTERY_STATUS_CHARACTERISTIC_PATH,
                self._battery_characteristic.StartNotify,
                self._battery_characteristic.StopNotify,
            )
            network_cccd = ClientCharacteristicConfigurationDescriptor(
                NETWORK_CHARACTERISTIC_PATH,
                self._network_characteristic.StartNotify,
                self._network_characteristic.StopNotify,
            )
            cpu_cccd = ClientCharacteristicConfigurationDescriptor(
                CPU_CHARACTERISTIC_PATH,
                self._cpu_characteristic.StartNotify,
                self._cpu_characteristic.StopNotify,
            )
            bandwidth_cccd = ClientCharacteristicConfigurationDescriptor(
                BANDWIDTH_CHARACTERISTIC_PATH,
                self._bandwidth_characteristic.StartNotify,
                self._bandwidth_characteristic.StopNotify,
            )
            latency_cccd = ClientCharacteristicConfigurationDescriptor(
                LATENCY_CHARACTERISTIC_PATH,
                self._latency_characteristic.StartNotify,
                self._latency_characteristic.StopNotify,
            )
            robot_control_cccd = ClientCharacteristicConfigurationDescriptor(
                ROBOT_CONTROL_CHARACTERISTIC_PATH,
                self._robot_control_characteristic.StartNotify,
                self._robot_control_characteristic.StopNotify,
            )
            wifi_config_cccd = ClientCharacteristicConfigurationDescriptor(
                WIFI_CONFIG_CHARACTERISTIC_PATH,
                self._wifi_config_characteristic.StartNotify,
                self._wifi_config_characteristic.StopNotify,
            )
            nav_task_cccd = ClientCharacteristicConfigurationDescriptor(
                NAV_TASK_CHARACTERISTIC_PATH,
                self._nav_task_characteristic.StartNotify,
                self._nav_task_characteristic.StopNotify,
            )
            zone_nav_cccd = ClientCharacteristicConfigurationDescriptor(
                ZONE_NAV_CHARACTERISTIC_PATH,
                self._zone_nav_characteristic.StartNotify,
                self._zone_nav_characteristic.StopNotify,
            )
            cmd_vel_cccd = ClientCharacteristicConfigurationDescriptor(
                CMD_VEL_CHARACTERISTIC_PATH,
                self._cmd_vel_characteristic.StartNotify,
                self._cmd_vel_characteristic.StopNotify,
            )
            memory_cccd = ClientCharacteristicConfigurationDescriptor(
                MEMORY_CHARACTERISTIC_PATH,
                self._memory_characteristic.StartNotify,
                self._memory_characteristic.StopNotify,
            )
            zone_voice_cccd = ClientCharacteristicConfigurationDescriptor(
                ZONE_VOICE_CHARACTERISTIC_PATH,
                self._zone_voice_characteristic.StartNotify,
                self._zone_voice_characteristic.StopNotify,
            )
            initial_pose_cccd = ClientCharacteristicConfigurationDescriptor(
                INITIAL_POSE_CHARACTERISTIC_PATH,
                self._initial_pose_characteristic.StartNotify,
                self._initial_pose_characteristic.StopNotify,
            )
            service = ControlService()
            advertisement = ControlAdvertisement(
                self._device_name,
                on_release=self._on_advertisement_released,
            )

            self._bus.publish_object(APP_PATH, GattApplication())
            self._bus.publish_object(SERVICE_PATH, service)
            self._bus.publish_object(CHARACTERISTIC_PATH, self._characteristic)
            self._bus.publish_object(CHARACTERISTIC_CCCD_PATH, command_cccd)
            self._bus.publish_object(URL_CHARACTERISTIC_PATH, self._url_characteristic)
            self._bus.publish_object(URL_CHARACTERISTIC_CCCD_PATH, url_cccd)
            self._bus.publish_object(ERROR_CHARACTERISTIC_PATH, self._error_characteristic)
            self._bus.publish_object(ERROR_CHARACTERISTIC_CCCD_PATH, error_cccd)
            self._bus.publish_object(
                BATTERY_STATUS_CHARACTERISTIC_PATH,
                self._battery_characteristic,
            )
            self._bus.publish_object(
                BATTERY_STATUS_CHARACTERISTIC_CCCD_PATH, battery_cccd
            )
            self._bus.publish_object(
                NETWORK_CHARACTERISTIC_PATH,
                self._network_characteristic,
            )
            self._bus.publish_object(NETWORK_CHARACTERISTIC_CCCD_PATH, network_cccd)
            self._bus.publish_object(
                CPU_CHARACTERISTIC_PATH,
                self._cpu_characteristic,
            )
            self._bus.publish_object(CPU_CHARACTERISTIC_CCCD_PATH, cpu_cccd)
            self._bus.publish_object(
                BANDWIDTH_CHARACTERISTIC_PATH,
                self._bandwidth_characteristic,
            )
            self._bus.publish_object(
                BANDWIDTH_CHARACTERISTIC_CCCD_PATH, bandwidth_cccd
            )
            self._bus.publish_object(
                LATENCY_CHARACTERISTIC_PATH,
                self._latency_characteristic,
            )
            self._bus.publish_object(
                LATENCY_CHARACTERISTIC_CCCD_PATH, latency_cccd
            )
            self._bus.publish_object(
                ROBOT_CONTROL_CHARACTERISTIC_PATH,
                self._robot_control_characteristic,
            )
            self._bus.publish_object(
                ROBOT_CONTROL_CHARACTERISTIC_CCCD_PATH, robot_control_cccd
            )
            self._bus.publish_object(
                WIFI_CONFIG_CHARACTERISTIC_PATH,
                self._wifi_config_characteristic,
            )
            self._bus.publish_object(
                WIFI_CONFIG_CHARACTERISTIC_CCCD_PATH, wifi_config_cccd
            )
            self._bus.publish_object(
                NAV_TASK_CHARACTERISTIC_PATH,
                self._nav_task_characteristic,
            )
            self._bus.publish_object(
                NAV_TASK_CHARACTERISTIC_CCCD_PATH, nav_task_cccd
            )
            self._bus.publish_object(
                ZONE_NAV_CHARACTERISTIC_PATH,
                self._zone_nav_characteristic,
            )
            self._bus.publish_object(
                ZONE_NAV_CHARACTERISTIC_CCCD_PATH, zone_nav_cccd
            )
            self._bus.publish_object(
                CMD_VEL_CHARACTERISTIC_PATH,
                self._cmd_vel_characteristic,
            )
            self._bus.publish_object(
                CMD_VEL_CHARACTERISTIC_CCCD_PATH, cmd_vel_cccd
            )
            self._bus.publish_object(
                MEMORY_CHARACTERISTIC_PATH,
                self._memory_characteristic,
            )
            self._bus.publish_object(MEMORY_CHARACTERISTIC_CCCD_PATH, memory_cccd)
            self._bus.publish_object(
                ZONE_VOICE_CHARACTERISTIC_PATH,
                self._zone_voice_characteristic,
            )
            self._bus.publish_object(
                ZONE_VOICE_CHARACTERISTIC_CCCD_PATH, zone_voice_cccd
            )
            self._bus.publish_object(
                INITIAL_POSE_CHARACTERISTIC_PATH,
                self._initial_pose_characteristic,
            )
            self._bus.publish_object(
                INITIAL_POSE_CHARACTERISTIC_CCCD_PATH, initial_pose_cccd
            )
            self._bus.publish_object(ADVERTISEMENT_PATH, advertisement)
            GLib.timeout_add_seconds(1, self._notify_battery)

            # Watch bluetoothd so a daemon restart triggers re-registration;
            # without this the service silently disappears until process restart.
            self._dbus_daemon_proxy = self._bus.get_proxy(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
            )
            self._dbus_daemon_proxy.NameOwnerChanged.connect(
                self._on_name_owner_changed
            )
            self._setup_bluez()
            self._event_loop.run()
        except Exception as exc:
            self._error = str(exc)
            logger.exception(f"BLE control server failed: {exc}")
            self._ready.set()
        finally:
            self._registered = False
            if self._bus is not None:
                self._bus.disconnect()
            self._ready.set()

    def _on_gatt_registered(self, get_result) -> None:
        try:
            get_result()
        except Exception as exc:
            if not self._ready.is_set():
                self._fail_startup(f"Failed to register GATT application: {exc}")
            else:
                logger.warning(f"GATT re-registration failed: {exc}; retrying in 5s")
                GLib.timeout_add_seconds(5, self._retry_register_application)
            return
        self._gatt_registered = True
        self._advertising_manager.RegisterAdvertisement(
            ADVERTISEMENT_PATH,
            {},
            callback=self._on_advertisement_registered,
        )

    def _on_advertisement_registered(self, get_result) -> None:
        try:
            get_result()
        except Exception as exc:
            if not self._ready.is_set():
                self._fail_startup(f"Failed to register BLE advertisement: {exc}")
            else:
                logger.warning(
                    f"BLE advertisement registration failed: {exc}; retrying in 5s"
                )
                GLib.timeout_add_seconds(5, self._reregister_advertisement)
            return
        self._advertisement_registered = True
        self._registered = True
        logger.info(
            f"BLE control ready: name={self._device_name!r} "
            f"service={SERVICE_UUID} command={COMMAND_CHAR_UUID} "
            f"url={URL_CHAR_UUID} error={ERROR_CHAR_UUID}"
        )
        self._ready.set()

    def _setup_bluez(self) -> None:
        """Create BlueZ proxies, configure the adapter, and start registration."""
        adapter_properties = self._bus.get_proxy(
            BLUEZ_SERVICE_NAME,
            self._adapter_path,
            interface_name="org.freedesktop.DBus.Properties",
        )
        self._gatt_manager = self._bus.get_proxy(
            BLUEZ_SERVICE_NAME,
            self._adapter_path,
            interface_name="org.bluez.GattManager1",
        )
        self._advertising_manager = self._bus.get_proxy(
            BLUEZ_SERVICE_NAME,
            self._adapter_path,
            interface_name="org.bluez.LEAdvertisingManager1",
        )

        try:
            adapter_properties.Set(
                "org.bluez.Adapter1", "Powered", Variant("b", True)
            )
            adapter_properties.Set(
                "org.bluez.Adapter1", "Alias", Variant("s", self._device_name)
            )
        except Exception as exc:
            logger.warning(f"Could not configure Bluetooth adapter: {exc}")
        # Registration must be asynchronous. BlueZ calls back into this
        # process for GetManagedObjects while RegisterApplication is in
        # flight, so the GLib loop must already be able to serve requests.
        self._gatt_manager.RegisterApplication(
            APP_PATH,
            {},
            callback=self._on_gatt_registered,
        )

    def _on_name_owner_changed(self, name: str, old_owner: str, new_owner: str) -> None:
        """Re-register with BlueZ when bluetoothd restarts."""
        if name != BLUEZ_SERVICE_NAME or self._shutdown_requested:
            return
        if not new_owner:
            logger.warning("bluetoothd vanished; BLE registrations are lost")
            self._registered = False
            self._gatt_registered = False
            self._advertisement_registered = False
        elif not old_owner:
            logger.info("bluetoothd is back; re-registering the BLE service")
            try:
                self._setup_bluez()
            except Exception as exc:
                logger.warning(f"Could not contact BlueZ: {exc}; retrying in 5s")
                GLib.timeout_add_seconds(5, self._retry_register_application)

    def _retry_register_application(self) -> bool:
        """Retry GATT registration once (GLib one-shot timer callback)."""
        if self._shutdown_requested or self._gatt_registered:
            return False
        try:
            self._setup_bluez()
        except Exception as exc:
            logger.warning(f"Could not contact BlueZ: {exc}; retrying in 5s")
            GLib.timeout_add_seconds(5, self._retry_register_application)
        return False

    def _on_advertisement_released(self) -> None:
        """BlueZ dropped the advertisement; schedule its re-registration."""
        if self._shutdown_requested or not self._advertisement_registered:
            return
        self._advertisement_registered = False
        logger.warning("BlueZ released the BLE advertisement; re-registering in 2s")
        GLib.timeout_add_seconds(2, self._reregister_advertisement)

    def _reregister_advertisement(self) -> bool:
        """Attempt advertisement registration once (GLib timer callback)."""
        if self._shutdown_requested or self._advertisement_registered:
            return False
        if not self._gatt_registered or self._advertising_manager is None:
            # The watchdog re-registration chain will register it instead.
            return False
        self._advertising_manager.RegisterAdvertisement(
            ADVERTISEMENT_PATH,
            {},
            callback=self._on_advertisement_reregistered,
        )
        return False

    def _on_advertisement_reregistered(self, get_result) -> None:
        try:
            get_result()
        except Exception as exc:
            logger.warning(
                f"BLE advertisement re-registration failed: {exc}; retrying in 5s"
            )
            GLib.timeout_add_seconds(5, self._reregister_advertisement)
            return
        self._advertisement_registered = True
        logger.info("BLE advertisement re-registered")

    def _on_client_disconnected(self) -> None:
        """Schedule an advertisement refresh after Command notify stops.

        BlueZ is supposed to resume the registered advertisement on its own
        once a connection drops, but several adapter/driver combinations
        never re-activate it after the first connection, leaving the robot
        invisible to scans. There is no Device1.Connected watch; this hook
        runs from CommandCharacteristic.StopNotify, so it also fires when
        the App merely unsubscribes and does not fire if Command notify was
        never enabled. The Unregister/Register refresh is idempotent; it
        briefly opens an advertising gap even when BlueZ already resumed.
        """
        if self._shutdown_requested or not self._gatt_registered:
            return
        if self._advertisement_refresh_pending:
            return
        self._advertisement_refresh_pending = True
        self._advertisement_refresh_count += 1
        logger.info(
            "Command notify stopped; scheduling BLE advertisement refresh "
            f"in 2s (count={self._advertisement_refresh_count})"
        )
        # Small delay: BlueZ processes the disconnection first.
        GLib.timeout_add_seconds(2, self._refresh_advertisement)

    def _refresh_advertisement(self) -> bool:
        """Restart advertising once (GLib one-shot timer callback)."""
        self._advertisement_refresh_pending = False
        if self._shutdown_requested or not self._gatt_registered:
            return False
        if self._advertising_manager is None:
            return False
        logger.info(
            "Refreshing BLE advertisement after notify stop "
            f"(count={self._advertisement_refresh_count})"
        )
        if self._advertisement_registered:
            # Mark unregistered first: BlueZ may call Release() on the
            # advertisement object during unregistration, and the release
            # handler must not schedule a competing re-registration.
            self._advertisement_registered = False
            self._advertising_manager.UnregisterAdvertisement(
                ADVERTISEMENT_PATH,
                callback=self._on_advertisement_refresh_unregistered,
            )
        else:
            self._reregister_advertisement()
        return False

    def _on_advertisement_refresh_unregistered(self, get_result) -> None:
        try:
            get_result()
        except Exception as exc:
            logger.warning(
                f"Failed to unregister BLE advertisement for refresh: {exc}"
            )
        if self._shutdown_requested or not self._gatt_registered:
            return
        self._reregister_advertisement()

    def _fail_startup(self, message: str) -> None:
        self._error = message
        logger.error(message)
        self._ready.set()
        self._begin_shutdown()

    def _publish_local_error(self, code: str, message: str) -> None:
        if self._error_characteristic is not None:
            self._error_characteristic.publish(code, message)

    def _notify_battery(self) -> bool:
        if self._battery_characteristic is not None:
            return self._battery_characteristic.tick()
        return not self._shutdown_requested

    def _begin_shutdown(self) -> bool:
        if self._shutdown_requested:
            return False
        self._shutdown_requested = True
        if self._characteristic is not None:
            self._characteristic.force_stop("bluetooth_shutdown")
        self._registered = False
        if self._advertisement_registered:
            self._advertising_manager.UnregisterAdvertisement(
                ADVERTISEMENT_PATH,
                callback=self._on_advertisement_unregistered,
            )
        else:
            self._unregister_gatt()
        return False

    def _on_advertisement_unregistered(self, get_result) -> None:
        try:
            get_result()
        except Exception as exc:
            logger.warning(f"Failed to unregister BLE advertisement: {exc}")
        self._advertisement_registered = False
        self._unregister_gatt()

    def _unregister_gatt(self) -> None:
        if self._gatt_registered:
            self._gatt_manager.UnregisterApplication(
                APP_PATH,
                callback=self._on_gatt_unregistered,
            )
        else:
            self._finish_shutdown()

    def _on_gatt_unregistered(self, get_result) -> None:
        try:
            get_result()
        except Exception as exc:
            logger.warning(f"Failed to unregister GATT application: {exc}")
        self._gatt_registered = False
        self._finish_shutdown()

    def _finish_shutdown(self) -> None:
        if self._event_loop is not None:
            self._event_loop.quit()
