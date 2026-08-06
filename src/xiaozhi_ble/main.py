"""Entry point for the xiaozhi BLE control bridge.

The bridge owns the BlueZ GATT service and translates between the BLE
protocol (see BLE_CONTROL_PROTOCOL.md) and the voice client's Unix socket
control protocol:

- App start/stop writes        -> "start"/"stop" requests
- App URL writes               -> "set_url <url>" requests
- Control socket (re)connect   -> "get_url" request to sync the URL characteristic
- STATE/URL/ERROR push        -> GATT characteristic notifications
- BLE disconnect while pressed -> automatic "stop" request
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading

from loguru import logger

from .battery import BatteryProvider
from .config import BridgeConfig
from .control_client import ControlClient, ControlRequestError, ControlUnavailable
from .cpu import CpuProvider
from .gatt_server import BleControlServer, BridgeError
from .network import NetworkProvider


class _Bridge:
    """Wire the control-socket client to the BLE GATT server."""

    def __init__(self, config: BridgeConfig) -> None:
        self._client: ControlClient | None = None
        self._server = BleControlServer(
            callback=self._handle_command,
            url_callback=self._handle_url_update,
            adapter_path=config.bluetooth_adapter,
            device_name=config.bluetooth_name,
            initial_state="idle",
            initial_url="",
            initial_error=("VOICE_UNAVAILABLE", "control socket is not connected"),
            initial_battery_status=(None, None),
        )
        self._client = ControlClient(
            socket_path=config.control_socket_path,
            on_notification=self._handle_notification,
            on_connection_change=self._handle_connection_change,
        )
        # Battery is a bridge-local concern: the conversation module knows
        # nothing about it. A small rclpy node subscribes to the configured
        # BatteryState topic instead of shelling out to `ros2 topic echo`.
        self._battery_provider = BatteryProvider(
            topic=config.battery_topic,
            on_status=self._server.notify_battery_status,
        )
        # Network status is also bridge-local: read the WiFi link from the
        # kernel (ioctl + /proc/net/wireless + netifaces), no subprocesses.
        self._network_provider = NetworkProvider(
            interface=config.network_interface,
            poll_interval_secs=config.network_poll_interval_secs,
            on_status=self._server.notify_network_status,
        )
        # CPU usage likewise: sample /proc/stat and report the delta.
        self._cpu_provider = CpuProvider(
            on_usage=self._server.notify_cpu_usage,
            poll_interval_secs=config.cpu_poll_interval_secs,
            notify_threshold=config.cpu_notify_threshold,
        )

    def start(self) -> bool:
        assert self._client is not None
        self._client.start()
        self._battery_provider.start()
        self._network_provider.start()
        self._cpu_provider.start()
        if self._server.start():
            # The client may have connected before the GATT server was up, in
            # which case the "connected" callback was dropped; sync the error
            # characteristic with the actual connection state now.
            if self._client.connected:
                self._server.notify_error("NONE", "")
                self._sync_websocket_url()
            return True
        logger.error(f"Failed to start BLE control: {self._server.error}")
        self._cpu_provider.stop()
        self._network_provider.stop()
        self._battery_provider.stop()
        self._client.stop()
        return False

    def stop(self) -> None:
        assert self._client is not None
        # Stop the GATT server first: its shutdown releases an outstanding
        # push-to-talk press, which needs the client to still be up.
        self._server.stop()
        self._cpu_provider.stop()
        self._network_provider.stop()
        self._battery_provider.stop()
        self._client.stop()

    def _handle_command(self, command: str, reason: str) -> str:
        """Forward a start/stop command; the return value is relayed to the App."""
        assert self._client is not None
        try:
            return self._client.request(command)
        except ControlRequestError as exc:
            return f"ERR {exc.code} {exc.message}"
        except ControlUnavailable as exc:
            return f"ERR VOICE_UNAVAILABLE {exc}"

    def _handle_url_update(self, url: str) -> None:
        assert self._client is not None
        try:
            self._client.request(f"set_url {url}")
        except ControlRequestError as exc:
            raise BridgeError(exc.code, exc.message) from exc
        except ControlUnavailable as exc:
            raise BridgeError("VOICE_UNAVAILABLE", str(exc)) from exc

    def _handle_notification(self, line: str) -> None:
        if line.startswith("STATE "):
            self._server.notify_state(line[len("STATE "):])
        elif line.startswith("URL "):
            self._server.notify_websocket_url(line[len("URL "):])
        elif line == "NONE":
            self._server.notify_error("NONE", "")
        elif line.startswith("ERROR "):
            parts = line.split(" ", 2)
            code = parts[1]
            message = parts[2] if len(parts) > 2 else ""
            self._server.notify_error(code, message)
        else:
            logger.warning(f"Unknown control notification: {line!r}")

    def _handle_connection_change(self, connected: bool) -> None:
        if connected:
            # Clear the initial/reconnect VOICE_UNAVAILABLE error once the
            # control socket is reachable again.
            self._server.notify_error("NONE", "")
            # This callback fires on the client's reader thread, which is
            # also responsible for dispatching request responses; a blocking
            # request() here would time out, so sync from a worker thread.
            threading.Thread(
                target=self._sync_websocket_url,
                name="xiaozhi-url-sync",
                daemon=True,
            ).start()
        else:
            self._server.notify_error(
                "VOICE_UNAVAILABLE",
                "control socket is not connected",
            )

    def _sync_websocket_url(self) -> None:
        """Query the effective WebSocket URL and publish it over BLE.

        The URL push on the control socket only fires when the address
        changes, and any snapshot sent before the GATT server is up is
        dropped, so the bridge must ask for the current value explicitly
        on (re)connect. Without this the App sees an empty URL.
        """
        assert self._client is not None
        try:
            response = self._client.request("get_url")
        except (ControlRequestError, ControlUnavailable) as exc:
            logger.warning(f"Could not query websocket URL: {exc}")
            return
        parts = response.split(" ", 2)
        if len(parts) == 3 and parts[1] == "url" and parts[2]:
            self._server.notify_websocket_url(parts[2])
        else:
            logger.warning(f"Unexpected get_url response: {response!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="xiaozhi BLE control bridge")
    parser.add_argument(
        "--config",
        default=str(BridgeConfig.default_path()),
        help="Path to the bridge YAML config file",
    )
    args = parser.parse_args()

    config = BridgeConfig(args.config)
    if not config.load():
        sys.exit(1)

    logger.remove()
    logger.add(sys.stderr, level=config.log_level.upper())

    bridge = _Bridge(config)
    if not bridge.start():
        sys.exit(1)

    stop_event = threading.Event()

    def _request_stop(signum, _frame) -> None:
        logger.info(f"Received signal {signum}, shutting down")
        stop_event.set()

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    stop_event.wait()
    bridge.stop()
    logger.info("BLE control bridge stopped")


if __name__ == "__main__":
    main()
