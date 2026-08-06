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
    def log_level(self) -> str:
        return self._str("log_level", "info")
