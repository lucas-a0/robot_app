"""Shared rclpy runtime for the ROS-backed features.

Both the battery provider and the robot-control actuator need an rclpy node,
and ``rclpy.init()``/``rclpy.shutdown()`` are process-global, so a single
runtime owns the context, one node and its spin thread; features attach
their subscriptions and service clients to that node instead of creating
their own contexts. rclpy is imported lazily so the rest of the bridge
still runs when the ROS2 environment is not sourced.
"""

from __future__ import annotations

import threading

from loguru import logger


class RosRuntime:
    """Own the shared rclpy context, node, and spin thread."""

    def __init__(
        self,
        node_name: str = "xiaozhi_ble",
        spin_timeout_secs: float = 0.2,
    ) -> None:
        self._node_name = node_name
        self._spin_timeout_secs = spin_timeout_secs
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._rclpy = None
        self._node = None

    @property
    def node(self):
        """The shared rclpy node; None until :meth:`start` succeeds."""
        return self._node

    def start(self) -> bool:
        """Init rclpy and start spinning (no-op when already started).

        Returns False when rclpy is not importable; ROS-backed features
        stay disabled in that case and the rest of the bridge is unaffected.
        """
        if self._node is not None:
            return True
        try:
            import rclpy
        except ImportError:
            logger.error(
                "rclpy is not importable (is the ROS2 environment sourced?); "
                "ROS-backed features (battery, robot control) are disabled"
            )
            return False

        if not rclpy.ok():
            rclpy.init(args=None)
        self._rclpy = rclpy
        self._node = rclpy.create_node(self._node_name)

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._spin,
            name="xiaozhi-ros",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"ROS runtime started: node={self._node_name}")
        return True

    def stop(self, timeout: float = 5.0) -> None:
        """Stop spinning and shut down the rclpy context."""
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

    def _spin(self) -> None:
        rclpy = self._rclpy
        node = self._node
        while not self._stop_event.is_set():
            try:
                rclpy.spin_once(node, timeout_sec=self._spin_timeout_secs)
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                logger.warning(f"ROS spin error: {exc}")
                self._stop_event.wait(1.0)
