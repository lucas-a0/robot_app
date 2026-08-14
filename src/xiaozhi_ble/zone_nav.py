"""Zone navigation through a std_msgs/String publisher.

The App writes one of the four zone names (charging_zone, mowing_zone,
pool_zone, equipment_zone) to the zone-nav BLE characteristic; the bridge
publishes it as the ``data`` of a ``std_msgs/String`` on the configured
topic (default ``/xiaozhi_topic``) using the shared ROS runtime node (see
ros_runtime.py). Publishing is fire-and-forget: the App gets an immediate
``OK <zone>`` reply once the message is handed to the publisher.

std_msgs is imported lazily on :meth:`start` so the rest of the bridge
still runs when the ROS2 environment is incomplete; in that case every
write is answered with ``ERR unavailable``.
"""

from __future__ import annotations

from loguru import logger

# The four zones the App can navigate the robot to; there are no others.
ZONES = ("charging_zone", "mowing_zone", "pool_zone", "equipment_zone")


class ZoneNavigator:
    """Publish zone names as std_msgs/String messages.

    An empty ``topic`` disables the navigator. The rclpy context and spin
    thread are owned by the shared ROS runtime; the navigator only creates
    and destroys its publisher on the runtime's node.
    """

    def __init__(self, topic: str) -> None:
        self._topic = topic
        self._string = None
        self._node = None
        self._publisher = None

    @property
    def enabled(self) -> bool:
        return bool(self._topic.strip())

    def start(self, node) -> None:
        """Create the publisher on the shared node (no-op when disabled)."""
        if not self.enabled:
            logger.info("Zone navigation disabled: zone_nav.topic is empty")
            return
        if self._publisher is not None:
            return
        try:
            from std_msgs.msg import String
        except ImportError:
            logger.error(
                "zone_nav.topic is set but std_msgs is not importable "
                "(is the ROS2 environment sourced?); zone navigation disabled"
            )
            return

        self._string = String
        self._node = node
        self._publisher = node.create_publisher(String, self._topic, 10)
        logger.info(f"Zone navigation started: topic={self._topic}")

    def stop(self) -> None:
        """Destroy the publisher; the runtime owns context shutdown."""
        if self._node is not None and self._publisher is not None:
            self._node.destroy_publisher(self._publisher)
        self._node = None
        self._publisher = None

    def execute(self, zone: str) -> str:
        """Publish a zone name and return the reply relayed to the App."""
        if self._publisher is None:
            return "ERR unavailable"
        if zone not in ZONES:
            return "ERR command"
        message = self._string()
        message.data = zone
        self._publisher.publish(message)
        logger.info(f"Published zone navigation goal: {zone}")
        return f"OK {zone}"
