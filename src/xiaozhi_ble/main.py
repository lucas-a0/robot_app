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
from .bandwidth import BandwidthProvider
from .config import BridgeConfig
from .control_client import ControlClient, ControlRequestError, ControlUnavailable
from .cpu import CpuProvider
from .gatt_server import BleControlServer, BridgeError
from .latency import LatencyProvider
from .nav_tasks import NavTaskManager
from .network import NetworkProvider
from .robot_control import RobotControl
from .ros_runtime import RosRuntime
from .wifi_config import WifiConfigurator
from .zone_nav import ZoneNavigator


class _Bridge:
    """Wire the control-socket client to the BLE GATT server."""

    def __init__(self, config: BridgeConfig) -> None:
        self._client: ControlClient | None = None
        # Navigation launch tasks: two fixed ros2 launch commands run as
        # managed child processes (see nav_tasks.py); task templates are
        # hardcoded, asynchronous events come back through notify_nav_task.
        self._nav_tasks = NavTaskManager(on_notify=self._forward_nav_task_event)
        # Zone navigation: BLE zone names are published as std_msgs/String
        # messages on the configured topic from the shared ROS runtime node;
        # publishing is fire-and-forget, so execute() returns the final reply.
        self._zone_nav = ZoneNavigator(topic=config.zone_nav_topic)
        self._server = BleControlServer(
            callback=self._handle_command,
            url_callback=self._handle_url_update,
            adapter_path=config.bluetooth_adapter,
            device_name=config.bluetooth_name,
            initial_state="idle",
            initial_url="",
            initial_error=("VOICE_UNAVAILABLE", "control socket is not connected"),
            initial_battery_status=(None, None),
            robot_control_callback=self._handle_robot_command,
            wifi_config_callback=self._handle_wifi_config,
            nav_task_callback=self._nav_tasks.execute,
            zone_nav_callback=self._zone_nav.execute,
        )
        self._client = ControlClient(
            socket_path=config.control_socket_path,
            on_notification=self._handle_notification,
            on_connection_change=self._handle_connection_change,
        )
        # Battery, robot control and zone navigation share one rclpy node
        # owned by the ROS runtime (rclpy init/shutdown are process-global);
        # they attach to its node instead of creating their own contexts.
        self._ros_runtime = RosRuntime()
        self._battery_provider = BatteryProvider(
            topic=config.battery_topic,
            on_status=self._server.notify_battery_status,
        )
        # Robot control is also bridge-local: BLE command names map to
        # std_srvs/Trigger services from configuration; success stays
        # silent, failures come back through notify_robot_control_error.
        self._robot_control = RobotControl(
            commands=config.robot_control_commands,
            call_timeout_secs=config.robot_control_call_timeout_secs,
            on_error=self._server.notify_robot_control_error,
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
        # WiFi throughput: /proc/net/dev byte-counter deltas on the same
        # interface as the network provider.
        self._bandwidth_provider = BandwidthProvider(
            on_bandwidth=self._server.notify_bandwidth,
            interface=config.network_interface,
            poll_interval_secs=config.bandwidth_poll_interval_secs,
            notify_threshold=config.bandwidth_notify_threshold,
        )
        # Dialogue-server latency: TCP connect time against the effective
        # WebSocket URL pushed from the control socket (see _publish_url).
        self._latency_provider = LatencyProvider(
            on_latency=self._server.notify_latency,
            poll_interval_secs=config.latency_poll_interval_secs,
            notify_threshold_ms=config.latency_notify_threshold_ms,
            connect_timeout_secs=config.latency_connect_timeout_secs,
        )
        # WiFi provisioning: NetworkManager over D-Bus (dasbus, no
        # subprocesses), one attempt at a time; the result comes back
        # asynchronously through _publish_wifi_result.
        self._wifi_configurator = WifiConfigurator(
            on_result=self._publish_wifi_result,
            connect_timeout_secs=config.wifi_connect_timeout_secs,
        )

    def start(self) -> bool:
        assert self._client is not None
        self._client.start()
        # The shared rclpy node only comes up when a ROS-backed feature is
        # configured; without a ROS2 environment they all stay disabled.
        if (
            self._battery_provider.enabled
            or self._robot_control.enabled
            or self._zone_nav.enabled
        ):
            if self._ros_runtime.start():
                node = self._ros_runtime.node
                self._battery_provider.start(node)
                self._robot_control.start(node)
                self._zone_nav.start(node)
        self._network_provider.start()
        self._cpu_provider.start()
        self._bandwidth_provider.start()
        self._latency_provider.start()
        if self._server.start():
            # The client may have connected before the GATT server was up, in
            # which case the "connected" callback was dropped; sync the error
            # characteristic with the actual connection state now.
            if self._client.connected:
                self._server.notify_error("NONE", "")
                self._sync_websocket_url()
            return True
        logger.error(f"Failed to start BLE control: {self._server.error}")
        self._latency_provider.stop()
        self._bandwidth_provider.stop()
        self._cpu_provider.stop()
        self._network_provider.stop()
        self._zone_nav.stop()
        self._robot_control.stop()
        self._battery_provider.stop()
        self._ros_runtime.stop()
        self._client.stop()
        return False

    def stop(self) -> None:
        assert self._client is not None
        # Stop the GATT server first: its shutdown releases an outstanding
        # push-to-talk press, which needs the client to still be up.
        self._server.stop()
        # Then stop any running navigation launch tasks (SIGINT the process
        # groups, SIGKILL whatever ignores it).
        self._nav_tasks.stop_all()
        self._latency_provider.stop()
        self._bandwidth_provider.stop()
        self._cpu_provider.stop()
        self._network_provider.stop()
        # Detach the ROS-backed features before shutting down the shared
        # rclpy context they run on.
        self._zone_nav.stop()
        self._robot_control.stop()
        self._battery_provider.stop()
        self._ros_runtime.stop()
        self._client.stop()

    def _handle_robot_command(self, command: str) -> str | None:
        """Dispatch a robot-control command; an error text is relayed to the App."""
        return self._robot_control.execute(command)

    def _handle_wifi_config(self, ssid: str, password: str) -> str | None:
        """Dispatch a WiFi provisioning request; an error text is relayed to the App."""
        error = self._wifi_configurator.connect(ssid, password)
        return f"ERR {error}" if error is not None else None

    def _forward_nav_task_event(self, text: str) -> None:
        """Relay an asynchronous nav-task event (STOPPED/EXITED) to the App."""
        self._server.notify_nav_task(text)

    def _publish_wifi_result(self, code: str, ssid: str) -> None:
        """Relay the asynchronous provisioning outcome to the App."""
        if code == "connected":
            self._server.notify_wifi_config_result(f"CONNECTED {ssid}")
        else:
            self._server.notify_wifi_config_result(f"FAILED {code} {ssid}")

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
            self._publish_websocket_url(line[len("URL "):])
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

    def _publish_websocket_url(self, url: str) -> None:
        """Publish the effective URL over BLE and retarget the latency probe."""
        self._server.notify_websocket_url(url)
        self._latency_provider.set_url(url)

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
            self._publish_websocket_url(parts[2])
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
