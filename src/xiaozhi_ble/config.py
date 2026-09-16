"""Configuration for the xiaozhi BLE control bridge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class BridgeConfig:
    """Runtime configuration loaded from a YAML file."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._data: dict[str, Any] = {}

    @staticmethod
    def default_path() -> Path:
        return Path.home() / ".config" / "xiaozhi-ble" / "config.yaml"

    def load(self) -> bool:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = yaml.safe_load(f) or {}
        except FileNotFoundError:
            print(f"Config file not found: {self._path}", flush=True)
            return False
        except yaml.YAMLError as e:
            print(f"Failed to parse config file: {e}", flush=True)
            return False
        return True

    def _get_nested(self, key: str) -> Any:
        value: Any = self._data
        for part in key.split("."):
            if not isinstance(value, dict):
                raise KeyError(key)
            value = value[part]
        return value

    def _str(self, key: str, default: str) -> str:
        try:
            value = self._get_nested(key)
        except KeyError:
            return default
        if isinstance(value, str):
            return value
        return default

    @property
    def control_socket_path(self) -> str:
        return self._str("control_socket_path", "/tmp/xiaozhi-control.sock")

    @property
    def bluetooth_adapter(self) -> str:
        return self._str("bluetooth.adapter", "/org/bluez/hci0")

    @property
    def bluetooth_name(self) -> str:
        return self._str("bluetooth.name", "Xiaozhi")

    @property
    def battery_topic(self) -> str:
        return self._str("battery.topic", "/battery_state")

    @property
    def zone_nav_topic(self) -> str:
        return self._str("zone_nav.topic", "/xiaozhi_topic")

    @property
    def cmd_vel_topic(self) -> str:
        return self._str("cmd_vel.topic", "/cmd_vel")

    @property
    def zone_voice_socket_path(self) -> str:
        return self._str("zone_voice.socket_path", "/tmp/zone_voice_player.sock")

    @property
    def zone_voice_request_timeout_secs(self) -> float:
        try:
            value = self._get_nested("zone_voice.request_timeout_secs")
        except KeyError:
            return 2.0
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        return 2.0

    @property
    def zone_voice_poll_interval_secs(self) -> float:
        try:
            value = self._get_nested("zone_voice.poll_interval_secs")
        except KeyError:
            return 1.0
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        return 1.0

    @property
    def network_interface(self) -> str:
        """WiFi interface name; empty means auto-detect the first wlan*."""
        return self._str("network.interface", "")

    @property
    def network_poll_interval_secs(self) -> float:
        try:
            value = self._get_nested("network.poll_interval_secs")
        except KeyError:
            return 5.0
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        return 5.0

    @property
    def cpu_poll_interval_secs(self) -> float:
        try:
            value = self._get_nested("cpu.poll_interval_secs")
        except KeyError:
            return 5.0
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        return 5.0

    @property
    def cpu_notify_threshold(self) -> float:
        """Minimum CPU usage change (percentage points) that triggers a notify."""
        try:
            value = self._get_nested("cpu.notify_threshold")
        except KeyError:
            return 1.0
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
        return 1.0

    @property
    def memory_poll_interval_secs(self) -> float:
        try:
            value = self._get_nested("memory.poll_interval_secs")
        except KeyError:
            return 5.0
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        return 5.0

    @property
    def memory_notify_threshold(self) -> float:
        """Minimum memory occupancy change (percentage points) that triggers a notify."""
        try:
            value = self._get_nested("memory.notify_threshold")
        except KeyError:
            return 1.0
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
        return 1.0

    @property
    def bandwidth_poll_interval_secs(self) -> float:
        try:
            value = self._get_nested("bandwidth.poll_interval_secs")
        except KeyError:
            return 5.0
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        return 5.0

    @property
    def bandwidth_notify_threshold(self) -> float:
        """Minimum throughput change (KB/s) that triggers a notify."""
        try:
            value = self._get_nested("bandwidth.notify_threshold")
        except KeyError:
            return 10.0
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
        return 10.0

    @property
    def latency_poll_interval_secs(self) -> float:
        try:
            value = self._get_nested("latency.poll_interval_secs")
        except KeyError:
            return 5.0
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        return 5.0

    @property
    def latency_notify_threshold_ms(self) -> float:
        """Minimum latency change (milliseconds) that triggers a notify."""
        try:
            value = self._get_nested("latency.notify_threshold_ms")
        except KeyError:
            return 10.0
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
        return 10.0

    @property
    def latency_connect_timeout_secs(self) -> float:
        try:
            value = self._get_nested("latency.connect_timeout_secs")
        except KeyError:
            return 2.0
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        return 2.0

    @property
    def robot_control_commands(self) -> dict[str, str]:
        """BLE robot-control command name -> ROS2 Trigger service mapping."""
        try:
            value = self._get_nested("robot_control.commands")
        except KeyError:
            return {}
        if not isinstance(value, dict):
            return {}
        commands = {}
        for name, service in value.items():
            if isinstance(name, str) and isinstance(service, str) and service.startswith("/"):
                commands[name.strip().lower()] = service
            else:
                print(
                    f"Ignoring invalid robot_control.commands entry: "
                    f"{name!r} -> {service!r}",
                    flush=True,
                )
        return commands

    @property
    def robot_control_call_timeout_secs(self) -> float:
        try:
            value = self._get_nested("robot_control.call_timeout_secs")
        except KeyError:
            return 10.0
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        return 10.0

    @property
    def wifi_connect_timeout_secs(self) -> float:
        """Maximum time to wait for a WiFi provisioning attempt to finish."""
        try:
            value = self._get_nested("wifi.connect_timeout_secs")
        except KeyError:
            return 30.0
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        return 30.0

    @property
    def log_level(self) -> str:
        return self._str("log_level", "info")
