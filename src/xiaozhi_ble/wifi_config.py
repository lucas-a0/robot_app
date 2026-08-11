"""WiFi provisioning via NetworkManager over D-Bus.

The App writes "<ssid>\n<password>" to the WiFi Config GATT characteristic;
this module asks NetworkManager (dasbus, no subprocesses) to create and
activate a persistent WiFi connection profile and reports the outcome
asynchronously through the result callback.

The connection profile uses a fixed id (``xiaozhi-ble``); a previous profile
with the same id is deleted first so a changed password actually takes effect.
Only one provisioning attempt runs at a time.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Callable

from dasbus.connection import SystemMessageBus
from dasbus.typing import Variant
from loguru import logger

# Receives (result_code, ssid); result_code is one of:
# "connected", "auth", "not_found", "timeout", "failed".
WifiResultCallback = Callable[[str, str], None]

NM_SERVICE = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"
NM_SETTINGS_PATH = "/org/freedesktop/NetworkManager/Settings"

# Fixed NetworkManager connection profile id owned by this bridge.
CONNECTION_ID = "xiaozhi-ble"

_NM_DEVICE_TYPE_WIFI = 2
_NM_DEVICE_STATE_ACTIVATED = 100
_NM_DEVICE_STATE_FAILED = 120
# NMDeviceStateReason values relevant to provisioning failures.
_REASONS_AUTH = {7, 8}  # NO_SECRETS, SUPPLICANT_DISCONNECT
_REASON_NOT_FOUND = 53  # SSID_NOT_FOUND

_SSID_MAX_BYTES = 32
_PASSWORD_MIN_LEN = 8
_PASSWORD_MAX_LEN = 63

_POLL_INTERVAL_SECS = 0.5


class WifiRequestError(ValueError):
    """A malformed WiFi provisioning request payload."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def parse_wifi_request(text: str) -> tuple[str, str]:
    """Parse a "<ssid>\n<password>" payload into (ssid, password).

    The password line may be empty or omitted entirely for open networks.
    Raises WifiRequestError for malformed input.
    """
    lines = text.split("\n")
    ssid = lines[0].strip()
    if not ssid:
        raise WifiRequestError("SSID must not be empty")
    if len(ssid.encode("utf-8")) > _SSID_MAX_BYTES:
        raise WifiRequestError(f"SSID exceeds {_SSID_MAX_BYTES} bytes")
    password = lines[1].strip() if len(lines) > 1 else ""
    if password and not (_PASSWORD_MIN_LEN <= len(password) <= _PASSWORD_MAX_LEN):
        raise WifiRequestError(
            f"Password must be {_PASSWORD_MIN_LEN}-{_PASSWORD_MAX_LEN} characters"
        )
    return ssid, password


def _build_connection_settings(ssid: str, password: str) -> dict:
    """Build the NetworkManager connection settings dict for a WPA-PSK AP."""
    settings = {
        "connection": {
            "id": Variant("s", CONNECTION_ID),
            "uuid": Variant("s", str(uuid.uuid4())),
            "type": Variant("s", "802-11-wireless"),
            "autoconnect": Variant("b", True),
        },
        "802-11-wireless": {
            "ssid": Variant("ay", list(ssid.encode("utf-8"))),
            "mode": Variant("s", "infrastructure"),
        },
        "ipv4": {"method": Variant("s", "auto")},
        "ipv6": {"method": Variant("s", "ignore")},
    }
    if password:
        settings["802-11-wireless-security"] = {
            "key-mgmt": Variant("s", "wpa-psk"),
            "psk": Variant("s", password),
        }
    return settings


class WifiConfigurator:
    """Connect the machine to a requested WiFi network via NetworkManager."""

    def __init__(
        self,
        on_result: WifiResultCallback,
        connect_timeout_secs: float = 30.0,
        bus_factory: Callable[[], SystemMessageBus] = SystemMessageBus,
    ) -> None:
        self._on_result = on_result
        self._connect_timeout_secs = connect_timeout_secs
        self._bus_factory = bus_factory
        self._bus: SystemMessageBus | None = None
        self._device_path: str | None = None
        self._lock = threading.Lock()
        self._busy = False

    def connect(self, ssid: str, password: str) -> str | None:
        """Start connecting; return an immediate error code or None when accepted.

        Immediate error codes: "busy" (a provisioning attempt is already
        running) and "unavailable" (no NetworkManager or no WiFi device).
        The final outcome is delivered asynchronously through on_result.
        """
        with self._lock:
            if self._busy:
                return "busy"
            device_path = self._find_wifi_device()
            if device_path is None:
                return "unavailable"
            self._busy = True
        threading.Thread(
            target=self._run,
            args=(ssid, password, device_path),
            name="xiaozhi-wifi-config",
            daemon=True,
        ).start()
        return None

    def _get_bus(self) -> SystemMessageBus:
        if self._bus is None:
            self._bus = self._bus_factory()
        return self._bus

    def _find_wifi_device(self) -> str | None:
        """Return the object path of the first WiFi device, or None."""
        if self._device_path is not None:
            return self._device_path
        try:
            bus = self._get_bus()
            nm = bus.get_proxy(NM_SERVICE, NM_PATH)
            for path in nm.GetDevices():
                device = bus.get_proxy(NM_SERVICE, path)
                if device.DeviceType == _NM_DEVICE_TYPE_WIFI:
                    self._device_path = path
                    logger.info(f"WiFi provisioning uses device: {path}")
                    return path
            logger.warning("WiFi provisioning unavailable: no WiFi device found")
        except Exception as exc:
            logger.warning(f"WiFi provisioning unavailable: {exc}")
        return None

    def _run(self, ssid: str, password: str, device_path: str) -> None:
        code = "failed"
        try:
            code = self._activate(ssid, password, device_path)
        except Exception:
            logger.exception(f"WiFi provisioning failed: ssid={ssid!r}")
        finally:
            with self._lock:
                self._busy = False
        logger.info(f"WiFi provisioning result: {code} ssid={ssid!r}")
        self._on_result(code, ssid)

    def _activate(self, ssid: str, password: str, device_path: str) -> str:
        """Create and activate the connection; return the result code."""
        bus = self._get_bus()
        self._delete_existing_profile(bus)
        nm = bus.get_proxy(NM_SERVICE, NM_PATH)
        nm.AddAndActivateConnection(
            _build_connection_settings(ssid, password),
            device_path,
            "/",
        )
        return self._wait_for_activation(bus, device_path)

    def _delete_existing_profile(self, bus: SystemMessageBus) -> None:
        """Delete a previous bridge-owned profile so new credentials apply."""
        settings_proxy = bus.get_proxy(NM_SERVICE, NM_SETTINGS_PATH)
        for path in settings_proxy.ListConnections():
            connection = bus.get_proxy(NM_SERVICE, path)
            try:
                settings = connection.GetSettings()
            except Exception:
                continue
            if settings.get("connection", {}).get("id") == CONNECTION_ID:
                connection.Delete()
                logger.info(f"Deleted previous WiFi profile: {path}")

    def _wait_for_activation(self, bus: SystemMessageBus, device_path: str) -> str:
        """Poll the device state until it activates, fails, or times out."""
        device = bus.get_proxy(NM_SERVICE, device_path)
        deadline = time.monotonic() + self._connect_timeout_secs
        while True:
            state, reason = device.StateReason
            if state == _NM_DEVICE_STATE_ACTIVATED:
                return "connected"
            if state == _NM_DEVICE_STATE_FAILED:
                if reason in _REASONS_AUTH:
                    return "auth"
                if reason == _REASON_NOT_FOUND:
                    return "not_found"
                return "failed"
            if time.monotonic() >= deadline:
                return "timeout"
            time.sleep(_POLL_INTERVAL_SECS)
