"""Entry point for the xiaozhi BLE / LAN control bridge.

The bridge owns the BlueZ GATT service and a LAN TCP control server
(see BLE_CONTROL_PROTOCOL.md and LAN_CONTROL_PROTOCOL.md). Both
transports dispatch into the same providers; notifications are fanned
out to whichever transport is connected.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading

from loguru import logger

from .audio_endpoint import AudioEndpointProvider, format_endpoint
from .battery import BatteryProvider
from .bandwidth import BandwidthProvider
from .cmd_vel import CmdVelPublisher
from .config import BridgeConfig
from .cpu import CpuProvider
from .gatt_server import BleControlServer
from .initial_pose import InitialPosePublisher
from .lan_server import LanControlServer
from .memory import MemoryProvider
from .nav_tasks import NavTaskManager
from .network import NetworkProvider
from .robot_control import RobotControl
from .ros_runtime import RosRuntime
from .wifi_config import WifiConfigurator
from .zone_nav import ZoneNavigator
from .zone_voice import ZoneVoiceController


class _Bridge:
    """Wire providers to the BLE GATT server and the LAN TCP server."""

    def __init__(self, config: BridgeConfig) -> None:
        self._lan_port = config.lan_port
        self._ipv4: str | None = None
        self._audio_port: int | None = None
        # Navigation launch tasks: two fixed ros2 launch commands run as
        # managed child processes (see nav_tasks.py); task templates are
        # hardcoded, asynchronous events come back through notify_nav_task.
        self._nav_tasks = NavTaskManager(on_notify=self._forward_nav_task_event)
        # Zone navigation: BLE zone names are published as std_msgs/String
        # messages on the configured topic from the shared ROS runtime node;
        # publishing is fire-and-forget, so execute() returns the final reply.
        self._zone_nav = ZoneNavigator(topic=config.zone_nav_topic)
        # Velocity control: BLE "<linear_x> <angular_z>" writes are published
        # as geometry_msgs/Twist on the configured topic from the shared ROS
        # runtime node; every write publishes exactly one message (no dedup).
        self._cmd_vel = CmdVelPublisher(topic=config.cmd_vel_topic)
        # Initial pose reset: any non-empty Initial Pose write publishes one
        # hardcoded geometry_msgs/PoseWithCovarianceStamped on the configured
        # topic from the shared ROS runtime node (the App only signals "reset
        # now"; the pose itself lives in initial_pose.py).
        self._initial_pose = InitialPosePublisher(topic=config.initial_pose_topic)
        # Zone voice: BLE LIST/PLAY/STOP/STATUS writes are translated into
        # JSON Lines requests against zone_voice_player; status changes
        # (natural end, ROS-triggered playback) come back through notify.
        self._zone_voice = ZoneVoiceController(
            socket_path=config.zone_voice_socket_path,
            on_notify=self._forward_zone_voice_event,
            request_timeout_secs=config.zone_voice_request_timeout_secs,
            poll_interval_secs=config.zone_voice_poll_interval_secs,
        )
        self._server = BleControlServer(
            adapter_path=config.bluetooth_adapter,
            device_name=config.bluetooth_name,
            initial_battery_status=(None, None),
            robot_control_callback=self._handle_robot_command,
            wifi_config_callback=self._handle_wifi_config,
            nav_task_callback=self._nav_tasks.execute,
            zone_nav_callback=self._zone_nav.execute,
            cmd_vel_callback=self._cmd_vel.execute,
            zone_voice_callback=self._zone_voice.execute,
            initial_pose_callback=self._initial_pose.execute,
        )
        self._lan = LanControlServer(
            host=config.lan_host,
            port=config.lan_port,
            robot_control_callback=self._handle_robot_command,
            wifi_config_callback=self._handle_wifi_config,
            nav_task_callback=self._nav_tasks.execute,
            zone_nav_callback=self._zone_nav.execute,
            cmd_vel_callback=self._cmd_vel.execute,
            zone_voice_callback=self._zone_voice.execute,
            initial_pose_callback=self._initial_pose.execute,
        )
        # Battery, robot control, zone navigation and velocity control share
        # one rclpy node owned by the ROS runtime (rclpy init/shutdown are
        # process-global); they attach to its node instead of creating their
        # own contexts.
        self._ros_runtime = RosRuntime()
        self._battery_provider = BatteryProvider(
            topic=config.battery_topic,
            on_status=self._on_battery,
        )
        # Robot control is also bridge-local: BLE command names map to
        # std_srvs/Trigger services and/or a std_msgs/String topic from
        # configuration; success stays silent, failures come back through
        # notify_robot_control_error.
        self._robot_control = RobotControl(
            commands=config.robot_control_commands,
            call_timeout_secs=config.robot_control_call_timeout_secs,
            on_error=self._on_robot_control_error,
            topic=config.robot_control_topic,
        )
        # Network status is also bridge-local: read the WiFi link from the
        # kernel (ioctl + /proc/net/wireless + netifaces), no subprocesses.
        self._network_provider = NetworkProvider(
            interface=config.network_interface,
            poll_interval_secs=config.network_poll_interval_secs,
            on_status=self._on_network,
        )
        # CPU usage likewise: sample /proc/stat and report the delta.
        self._cpu_provider = CpuProvider(
            on_usage=self._on_cpu,
            poll_interval_secs=config.cpu_poll_interval_secs,
            notify_threshold=config.cpu_notify_threshold,
        )
        # Memory occupancy: read /proc/meminfo (instantaneous used/total).
        self._memory_provider = MemoryProvider(
            on_usage=self._on_memory,
            poll_interval_secs=config.memory_poll_interval_secs,
            notify_threshold=config.memory_notify_threshold,
        )
        # WiFi throughput: /proc/net/dev byte-counter deltas on the same
        # interface as the network provider.
        self._bandwidth_provider = BandwidthProvider(
            on_bandwidth=self._on_bandwidth,
            interface=config.network_interface,
            poll_interval_secs=config.bandwidth_poll_interval_secs,
            notify_threshold=config.bandwidth_notify_threshold,
        )
        # WiFi provisioning: NetworkManager over D-Bus (dasbus, no
        # subprocesses), one attempt at a time; the result comes back
        # asynchronously through _publish_wifi_result.
        self._wifi_configurator = WifiConfigurator(
            on_result=self._publish_wifi_result,
            connect_timeout_secs=config.wifi_connect_timeout_secs,
        )
        self._audio_endpoint_provider = AudioEndpointProvider(
            on_port=self._on_audio_port,
            query_host=config.audio_query_host,
            query_port=config.audio_query_port,
            poll_interval_secs=config.audio_poll_interval_secs,
        )

    def start(self) -> bool:
        # The shared rclpy node only comes up when a ROS-backed feature is
        # configured; without a ROS2 environment they all stay disabled.
        if (
            self._battery_provider.enabled
            or self._robot_control.enabled
            or self._zone_nav.enabled
            or self._cmd_vel.enabled
            or self._initial_pose.enabled
        ):
            if self._ros_runtime.start():
                node = self._ros_runtime.node
                self._battery_provider.start(node)
                self._robot_control.start(node)
                self._zone_nav.start(node)
                self._cmd_vel.start(node)
                self._initial_pose.start(node)
        self._network_provider.start()
        self._cpu_provider.start()
        self._memory_provider.start()
        self._bandwidth_provider.start()
        self._audio_endpoint_provider.start()
        self._zone_voice.start()
        lan_ok = self._lan.start()
        if not lan_ok:
            logger.error("Failed to start LAN control server")
        if self._server.start():
            return True
        logger.error(f"Failed to start BLE control: {self._server.error}")
        self._lan.stop()
        self._zone_voice.stop()
        self._audio_endpoint_provider.stop()
        self._bandwidth_provider.stop()
        self._memory_provider.stop()
        self._cpu_provider.stop()
        self._network_provider.stop()
        self._zone_nav.stop()
        self._cmd_vel.stop()
        self._initial_pose.stop()
        self._robot_control.stop()
        self._battery_provider.stop()
        self._ros_runtime.stop()
        return False

    def stop(self) -> None:
        # Stop the GATT server first so BlueZ unregisters while the rest of
        # the bridge is still up for any in-flight command.
        self._server.stop()
        self._lan.stop()
        # Then stop any running navigation launch tasks (SIGINT the process
        # groups, SIGKILL whatever ignores it).
        self._nav_tasks.stop_all()
        self._zone_voice.stop()
        self._audio_endpoint_provider.stop()
        self._bandwidth_provider.stop()
        self._memory_provider.stop()
        self._cpu_provider.stop()
        self._network_provider.stop()
        # Detach the ROS-backed features before shutting down the shared
        # rclpy context they run on.
        self._zone_nav.stop()
        self._cmd_vel.stop()
        self._initial_pose.stop()
        self._robot_control.stop()
        self._battery_provider.stop()
        self._ros_runtime.stop()

    def _on_battery(self, percentage: float | None, supply_status: str | None) -> None:
        self._server.notify_battery_status(percentage, supply_status)
        self._lan.notify_battery_status(percentage, supply_status)

    def _on_network(
        self,
        ssid: str | None,
        rssi_dbm: int | None,
        ipv4: str | None,
    ) -> None:
        self._ipv4 = ipv4
        self._server.notify_network_status(ssid, rssi_dbm, ipv4)
        self._lan.notify_network_status(ssid, rssi_dbm, ipv4)
        self._publish_endpoints()

    def _on_cpu(self, usage: float | None) -> None:
        self._server.notify_cpu_usage(usage)
        self._lan.notify_cpu_usage(usage)

    def _on_memory(
        self,
        used_mb: int,
        total_mb: int,
        percent: float,
    ) -> None:
        self._server.notify_memory_usage(used_mb, total_mb, percent)
        self._lan.notify_memory_usage(used_mb, total_mb, percent)

    def _on_bandwidth(self, rx_kbps: float, tx_kbps: float) -> None:
        self._server.notify_bandwidth(rx_kbps, tx_kbps)
        self._lan.notify_bandwidth(rx_kbps, tx_kbps)

    def _on_audio_port(self, port: int | None) -> None:
        self._audio_port = port
        self._publish_endpoints()

    def _publish_endpoints(self) -> None:
        lan_text = format_endpoint(self._ipv4, self._lan_port)
        audio_text = format_endpoint(self._ipv4, self._audio_port)
        self._server.update_lan_endpoint(lan_text)
        self._server.update_audio_endpoint(audio_text)
        self._lan.set_lan_endpoint(lan_text)
        self._lan.set_audio_endpoint(audio_text)

    def _handle_robot_command(self, command: str) -> str | None:
        """Dispatch a robot-control command; an error text is relayed to the App."""
        return self._robot_control.execute(command)

    def _on_robot_control_error(self, text: str) -> None:
        self._server.notify_robot_control_error(text)
        self._lan.notify_robot_control_error(text)

    def _handle_wifi_config(self, ssid: str, password: str) -> str | None:
        """Dispatch a WiFi provisioning request; an error text is relayed to the App."""
        error = self._wifi_configurator.connect(ssid, password)
        return f"ERR {error}" if error is not None else None

    def _forward_nav_task_event(self, text: str) -> None:
        """Relay an asynchronous nav-task event (STOPPED/EXITED) to the App."""
        self._server.notify_nav_task(text)
        self._lan.notify_nav_task(text)

    def _forward_zone_voice_event(self, text: str) -> None:
        """Relay a zone-voice status change (PLAYING/IDLE/UNKNOWN) to the App."""
        self._server.notify_zone_voice(text)
        self._lan.notify_zone_voice(text)

    def _publish_wifi_result(self, code: str, ssid: str) -> None:
        """Relay the asynchronous provisioning outcome to the App."""
        if code == "connected":
            text = f"CONNECTED {ssid}"
        else:
            text = f"FAILED {code} {ssid}"
        self._server.notify_wifi_config_result(text)
        self._lan.notify_wifi_config_result(text)


def main() -> None:
    parser = argparse.ArgumentParser(description="xiaozhi BLE/LAN control bridge")
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
