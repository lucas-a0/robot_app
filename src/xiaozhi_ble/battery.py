"""Battery status provider backed by a ROS2 topic subscription.

The provider attaches a ``sensor_msgs/BatteryState`` subscription (default
topic ``/battery_state``) to the shared ROS runtime node (see
ros_runtime.py) and reports each message's ``percentage`` and
``power_supply_status``. This replaces the old ``ros2 topic echo`` shell
polling, which needed a full ROS environment embedded in a command line and
was fragile under systemd autostart.

sensor_msgs is imported lazily on :meth:`start` so the rest of the bridge
still runs when the ROS2 environment is incomplete; in that case the
provider stays disabled and the GATT characteristic keeps reporting UNKNOWN.
"""

from __future__ import annotations

import math
from typing import Callable

from loguru import logger

# Receives (percentage, supply_status); either field is None until the first
# valid reading for it arrives.
BatteryStatusCallback = Callable[[float | None, str | None], None]


class BatteryProvider:
    """Subscribe to a BatteryState topic and forward battery status.

    An empty ``topic`` disables the provider. The rclpy context and spin
    thread are owned by the shared ROS runtime; the provider only creates
    and destroys its subscription on the runtime's node.
    """

    def __init__(self, topic: str, on_status: BatteryStatusCallback) -> None:
        self._topic = topic
        self._on_status = on_status
        self._percentage: float | None = None
        self._supply_status: str | None = None
        self._status_names: dict[int, str] = {}
        self._node = None
        self._subscription = None

    @property
    def enabled(self) -> bool:
        return bool(self._topic.strip())

    def start(self, node) -> None:
        """Create the subscription on the shared node (no-op when disabled)."""
        if not self.enabled:
            logger.info("Battery provider disabled: battery.topic is empty")
            return
        if self._subscription is not None:
            return

        try:
            from rclpy.qos import qos_profile_sensor_data
            from sensor_msgs.msg import BatteryState
        except ImportError:
            logger.error(
                "battery.topic is set but rclpy/sensor_msgs are not importable "
                "(is the ROS2 environment sourced?); battery reporting disabled"
            )
            return

        self._status_names = {
            BatteryState.POWER_SUPPLY_STATUS_UNKNOWN: "UNKNOWN",
            BatteryState.POWER_SUPPLY_STATUS_CHARGING: "CHARGING",
            BatteryState.POWER_SUPPLY_STATUS_DISCHARGING: "DISCHARGING",
            BatteryState.POWER_SUPPLY_STATUS_NOT_CHARGING: "NOT_CHARGING",
            BatteryState.POWER_SUPPLY_STATUS_FULL: "FULL",
        }

        # Sensor-data QoS (best-effort) is compatible with both reliable and
        # best-effort publishers, so it works regardless of the driver's QoS.
        self._node = node
        self._subscription = node.create_subscription(
            BatteryState,
            self._topic,
            self._handle_message,
            qos_profile_sensor_data,
        )
        logger.info(f"Battery provider started: topic={self._topic}")

    def stop(self) -> None:
        """Destroy the subscription; the runtime owns context shutdown."""
        if self._node is not None and self._subscription is not None:
            self._node.destroy_subscription(self._subscription)
        self._node = None
        self._subscription = None

    def _handle_message(self, msg) -> None:
        changed = False
        percentage = float(msg.percentage)
        # BatteryState.percentage is NaN when the driver has no reading;
        # keep the last known value in that case.
        if math.isfinite(percentage):
            self._percentage = percentage
            changed = True
        # Unknown or out-of-range supply status keeps the last known value.
        status = self._status_names.get(int(msg.power_supply_status))
        if status is not None and status != "UNKNOWN":
            self._supply_status = status
            changed = True
        elif self._supply_status is None and status == "UNKNOWN":
            changed = True
        if changed:
            self._on_status(self._percentage, self._supply_status)
