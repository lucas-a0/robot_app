"""Battery status provider backed by a ROS2 topic subscription.

The provider creates a small rclpy node that subscribes to a
``sensor_msgs/BatteryState`` topic (default ``/battery_state``) and reports
each message's ``percentage`` and ``power_supply_status``. This replaces the
old ``ros2 topic echo`` shell polling, which needed a full ROS environment
embedded in a command line and was fragile under systemd autostart.

rclpy is imported lazily so the rest of the bridge still runs when the ROS2
environment is not sourced; in that case the provider stays disabled and the
GATT characteristic keeps reporting UNKNOWN.
"""

from __future__ import annotations

import math
import threading
from typing import Callable

from loguru import logger

# Receives (percentage, supply_status); either field is None until the first
# valid reading for it arrives.
BatteryStatusCallback = Callable[[float | None, str | None], None]


class BatteryProvider:
    """Subscribe to a BatteryState topic and forward battery status.

    An empty ``topic`` disables the provider. The rclpy context is owned by
    the provider: it is initialized on :meth:`start` and shut down on
    :meth:`stop`.
    """

    def __init__(
        self,
        topic: str,
        on_status: BatteryStatusCallback,
        spin_timeout_secs: float = 0.2,
    ) -> None:
        self._topic = topic
        self._on_status = on_status
        self._spin_timeout_secs = spin_timeout_secs
        self._percentage: float | None = None
        self._supply_status: str | None = None
        self._status_names: dict[int, str] = {}
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._rclpy = None
        self._node = None

    @property
    def enabled(self) -> bool:
        return bool(self._topic.strip())

    def start(self) -> None:
        """Create the ROS2 node and start spinning (no-op when disabled)."""
        if not self.enabled:
            logger.info("Battery provider disabled: battery.topic is empty")
            return
        if self._thread is not None and self._thread.is_alive():
            return

        try:
            import rclpy
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

        if not rclpy.ok():
            rclpy.init(args=None)
        self._rclpy = rclpy
        self._node = rclpy.create_node("xiaozhi_ble_battery")
        # Sensor-data QoS (best-effort) is compatible with both reliable and
        # best-effort publishers, so it works regardless of the driver's QoS.
        self._node.create_subscription(
            BatteryState,
            self._topic,
            self._handle_message,
            qos_profile_sensor_data,
        )

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._spin,
            name="xiaozhi-battery",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"Battery provider started: topic={self._topic}")

    def stop(self, timeout: float = 5.0) -> None:
        """Stop spinning and shut down the ROS2 context."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
        if self._node is not None:
            self._node.destroy_node()
            self._node = None
        if self._rclpy is not None and self._rclpy.ok():
            self._rclpy.shutdown()
        self._rclpy = None

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

    def _spin(self) -> None:
        rclpy = self._rclpy
        node = self._node
        while not self._stop_event.is_set():
            try:
                rclpy.spin_once(node, timeout_sec=self._spin_timeout_secs)
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                logger.warning(f"Battery subscription error: {exc}")
                self._stop_event.wait(1.0)
