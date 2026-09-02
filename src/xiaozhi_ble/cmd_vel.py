"""Velocity control through a geometry_msgs/Twist publisher.

The App writes "<linear_x> <angular_z>" (two floats, m/s and rad/s) to the
cmd-vel BLE characteristic; the bridge publishes it as a
``geometry_msgs/Twist`` on the configured topic (default ``/cmd_vel``)
using the shared ROS runtime node (see ros_runtime.py). Every write
publishes exactly one message — repeated writes of the same values are
published again, there is no dedup. Publishing is fire-and-forget: the App
gets an immediate ``OK <linear_x> <angular_z>`` reply once the message is
handed to the publisher.

geometry_msgs is imported lazily on :meth:`start` so the rest of the
bridge still runs when the ROS2 environment is incomplete; in that case
every write is answered with ``ERR unavailable``.
"""

from __future__ import annotations

import math

from loguru import logger


class CmdVelPublisher:
    """Publish linear/angular velocity commands as geometry_msgs/Twist.

    An empty ``topic`` disables the publisher. The rclpy context and spin
    thread are owned by the shared ROS runtime; the publisher only creates
    and destroys its publisher on the runtime's node.
    """

    def __init__(self, topic: str) -> None:
        self._topic = topic
        self._twist = None
        self._node = None
        self._publisher = None

    @property
    def enabled(self) -> bool:
        return bool(self._topic.strip())

    def start(self, node) -> None:
        """Create the publisher on the shared node (no-op when disabled)."""
        if not self.enabled:
            logger.info("Velocity control disabled: cmd_vel.topic is empty")
            return
        if self._publisher is not None:
            return
        try:
            from geometry_msgs.msg import Twist
        except ImportError:
            logger.error(
                "cmd_vel.topic is set but geometry_msgs is not importable "
                "(is the ROS2 environment sourced?); velocity control disabled"
            )
            return

        self._twist = Twist
        self._node = node
        self._publisher = node.create_publisher(Twist, self._topic, 10)
        logger.info(f"Velocity control started: topic={self._topic}")

    def stop(self) -> None:
        """Destroy the publisher; the runtime owns context shutdown."""
        if self._node is not None and self._publisher is not None:
            self._node.destroy_publisher(self._publisher)
        self._node = None
        self._publisher = None

    def execute(self, text: str) -> str:
        """Publish one Twist per write and return the reply relayed to the App."""
        if self._publisher is None:
            return "ERR unavailable"
        parts = text.split()
        if len(parts) != 2:
            return "ERR command"
        try:
            linear_x = float(parts[0])
            angular_z = float(parts[1])
        except ValueError:
            return "ERR command"
        if not (math.isfinite(linear_x) and math.isfinite(angular_z)):
            return "ERR command"
        message = self._twist()
        message.linear.x = linear_x
        message.angular.z = angular_z
        self._publisher.publish(message)
        logger.debug(f"Published velocity command: linear_x={linear_x} angular_z={angular_z}")
        return f"OK {linear_x} {angular_z}"
