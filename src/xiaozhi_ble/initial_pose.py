"""Initial pose reset through a geometry_msgs/PoseWithCovarianceStamped publisher.

The App only signals "re-localize now": any non-empty write to the
initial-pose BLE characteristic makes the bridge publish one fixed
``geometry_msgs/PoseWithCovarianceStamped`` message on the configured topic
(default ``/initialpose``) using the shared ROS runtime node (see
ros_runtime.py). The pose itself is hardcoded here — the App neither sends
nor chooses it — and matches the manual ``ros2 topic pub`` the operator used
before. Publishing is fire-and-forget: the App gets an immediate ``OK``
reply once the message is handed to the publisher.

geometry_msgs is imported lazily on :meth:`start` so the rest of the bridge
still runs when the ROS2 environment is incomplete; in that case every
write is answered with ``ERR unavailable``.
"""

from __future__ import annotations

from loguru import logger

# The pose published on every trigger; values are the fixed re-localization
# set agreed for the App button (map frame, yaw ~1.95 degrees).
FRAME_ID = "map"
POSITION_X = 0.9512255787849426
POSITION_Y = -0.6430897116661072
POSITION_Z = 0.0
ORIENTATION_X = 0.0
ORIENTATION_Y = 0.0
ORIENTATION_Z = 0.017019178285167157
ORIENTATION_W = 0.9998551632964134
# Row-major 6x6 covariance: x, y, z, roll, pitch, yaw variances on the
# diagonal, everything else zero. Kept identical to the manual command so
# AMCL gets the same (loose in x/y, tight in yaw) uncertainty.
COVARIANCE = (
    0.25, 0.0, 0.0, 0.0, 0.0, 0.0,
    0.0, 0.25, 0.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 0.0, 0.0, 0.06853891945200942,
)


class InitialPosePublisher:
    """Publish the fixed initial pose as a geometry_msgs/PoseWithCovarianceStamped.

    An empty ``topic`` disables the publisher. The rclpy context and spin
    thread are owned by the shared ROS runtime; the publisher only creates
    and destroys its publisher on the runtime's node.
    """

    def __init__(self, topic: str) -> None:
        self._topic = topic
        self._message_type = None
        self._node = None
        self._publisher = None

    @property
    def enabled(self) -> bool:
        return bool(self._topic.strip())

    def start(self, node) -> None:
        """Create the publisher on the shared node (no-op when disabled)."""
        if not self.enabled:
            logger.info("Initial pose reset disabled: initial_pose.topic is empty")
            return
        if self._publisher is not None:
            return
        try:
            from geometry_msgs.msg import PoseWithCovarianceStamped
        except ImportError:
            logger.error(
                "initial_pose.topic is set but geometry_msgs is not importable "
                "(is the ROS2 environment sourced?); initial pose reset disabled"
            )
            return

        self._message_type = PoseWithCovarianceStamped
        self._node = node
        self._publisher = node.create_publisher(
            PoseWithCovarianceStamped, self._topic, 10
        )
        logger.info(f"Initial pose reset started: topic={self._topic}")

    def stop(self) -> None:
        """Destroy the publisher; the runtime owns context shutdown."""
        if self._node is not None and self._publisher is not None:
            self._node.destroy_publisher(self._publisher)
        self._node = None
        self._publisher = None

    def execute(self, text: str) -> str:
        """Publish one fixed initial pose and return the reply relayed to the App.

        The write payload is not interpreted: it only means "reset the pose
        now", so any non-empty text triggers exactly one publication.
        """
        if self._publisher is None:
            return "ERR unavailable"
        if not text.strip():
            return "ERR command"
        message = self._message_type()
        message.header.stamp = self._node.get_clock().now().to_msg()
        message.header.frame_id = FRAME_ID
        message.pose.pose.position.x = POSITION_X
        message.pose.pose.position.y = POSITION_Y
        message.pose.pose.position.z = POSITION_Z
        message.pose.pose.orientation.x = ORIENTATION_X
        message.pose.pose.orientation.y = ORIENTATION_Y
        message.pose.pose.orientation.z = ORIENTATION_Z
        message.pose.pose.orientation.w = ORIENTATION_W
        message.pose.covariance = list(COVARIANCE)
        self._publisher.publish(message)
        logger.info(
            f"Published initial pose: x={POSITION_X} y={POSITION_Y} "
            f"qz={ORIENTATION_Z} qw={ORIENTATION_W}"
        )
        return "OK"
