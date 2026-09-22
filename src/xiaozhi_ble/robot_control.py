"""Robot behavior control via ROS2 Trigger calls and topic publishes.

The App writes a command name to the robot-control BLE characteristic.
Configured posture commands (stand_up, squat, ...) map to
``std_srvs/Trigger`` services. Motion commands (go_forward, dance, nod,
...) are published as ``std_msgs/String`` on the configured topic — the
same plain-text names the old voice client published on
``/xiaozhi_topic``. Success is silent; Trigger failures are reported
through the error callback so the App gets a notification.

std_srvs / std_msgs are imported lazily on :meth:`start` so the rest of
the bridge still runs when the ROS2 environment is incomplete; in that
case every command is answered with ``ERR unavailable``.
"""

from __future__ import annotations

import threading
from typing import Callable

from loguru import logger

# Receives the notify text, e.g. "ERR failed stand_up low battery".
RobotErrorCallback = Callable[[str], None]

# How long execute() waits for the service to appear before answering
# ERR unavailable. Kept short: it runs on the GLib event-loop thread.
_SERVICE_WAIT_SECS = 1.0

# Command names published as std_msgs/String on robot_control.topic.
# Keep this set in sync with BLE_CONTROL_PROTOCOL.md section 7.
TOPIC_COMMANDS = frozenset(
    {
        "go_forward",
        "go_back",
        "turn_left",
        "turn_right",
        "dance",
        "nod",
        "squat",
        "stand_up",
        "lie_down",
    }
)


class RobotControl:
    """Dispatch App command names to Trigger services and/or a String topic.

    An empty ``commands`` mapping and an empty ``topic`` disable the
    actuator. The rclpy context and spin thread are owned by the shared
    ROS runtime; this class only creates clients and a publisher on that
    node.
    """

    def __init__(
        self,
        commands: dict[str, str],
        call_timeout_secs: float = 10.0,
        on_error: RobotErrorCallback | None = None,
        topic: str = "/xiaozhi_topic",
    ) -> None:
        self._commands = {
            name.strip().lower(): service for name, service in commands.items()
        }
        self._topic = topic.strip()
        self._call_timeout_secs = call_timeout_secs
        self._on_error = on_error or (lambda _text: None)
        self._trigger = None
        self._string = None
        self._node = None
        self._publisher = None
        self._clients: dict[str, object] = {}
        self._lock = threading.Lock()
        # future -> timeout timer, for calls still in flight.
        self._pending: dict[object, threading.Timer] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._commands) or bool(self._topic)

    def start(self, node) -> None:
        """Attach to the shared runtime node (no-op when disabled)."""
        if not self.enabled:
            logger.info(
                "Robot control disabled: robot_control.commands is empty "
                "and robot_control.topic is empty"
            )
            return
        if self._node is not None:
            return

        if self._commands:
            try:
                from std_srvs.srv import Trigger
            except ImportError:
                logger.error(
                    "robot_control.commands is set but std_srvs is not "
                    "importable (is the ROS2 environment sourced?); "
                    "Trigger commands disabled"
                )
            else:
                self._trigger = Trigger

        if self._topic:
            try:
                from std_msgs.msg import String
            except ImportError:
                logger.error(
                    "robot_control.topic is set but std_msgs is not "
                    "importable (is the ROS2 environment sourced?); "
                    "topic commands disabled"
                )
            else:
                self._string = String
                self._publisher = node.create_publisher(String, self._topic, 10)

        if self._trigger is None and self._publisher is None:
            return

        self._node = node
        logger.info(
            "Robot control started: "
            f"commands={sorted(self._commands)} topic={self._topic or '-'}"
        )

    def stop(self) -> None:
        """Detach from the node and cancel pending timeout timers."""
        with self._lock:
            timers = list(self._pending.values())
            self._pending.clear()
            self._clients.clear()
            node = self._node
            publisher = self._publisher
            self._node = None
            self._publisher = None
            self._trigger = None
            self._string = None
        for timer in timers:
            timer.cancel()
        if node is not None and publisher is not None:
            node.destroy_publisher(publisher)

    def execute(self, command: str) -> str | None:
        """Dispatch a command; returns an immediate error text or None.

        None means the work was accepted: a Trigger call is in flight, or
        a topic message was published. Trigger failures arrive later
        through the error callback. Topic publishes are fire-and-forget.
        """
        with self._lock:
            node = self._node
            trigger = self._trigger
            publisher = self._publisher
            string_type = self._string
        if node is None:
            return "ERR unavailable"

        service = self._commands.get(command)
        if service is not None and trigger is not None:
            return self._call_trigger(node, trigger, command, service)

        if command in TOPIC_COMMANDS:
            if publisher is None or string_type is None:
                return f"ERR unavailable {command}"
            message = string_type()
            message.data = command
            publisher.publish(message)
            logger.info(f"Published robot command: {command}")
            return None

        if service is not None:
            return f"ERR unavailable {command}"
        return "ERR command"

    def _call_trigger(self, node, trigger, command: str, service: str) -> str | None:
        with self._lock:
            client = self._clients.get(service)
            if client is None:
                client = node.create_client(trigger, service)
                self._clients[service] = client
        if not client.wait_for_service(timeout_sec=_SERVICE_WAIT_SECS):
            return f"ERR unavailable {command}"
        future = client.call_async(trigger.Request())
        timer = threading.Timer(
            self._call_timeout_secs,
            self._on_timeout,
            args=(command, future),
        )
        timer.daemon = True
        with self._lock:
            self._pending[future] = timer
        timer.start()
        future.add_done_callback(lambda fut: self._on_done(command, fut))
        return None

    def _on_done(self, command: str, future) -> None:
        """Handle a completed Trigger call (runs on the ROS spin thread)."""
        with self._lock:
            timer = self._pending.pop(future, None)
        if timer is None:
            # Already reported as timed out, or stop() cancelled it.
            return
        timer.cancel()
        try:
            response = future.result()
        except Exception as exc:
            self._report(f"ERR failed {command} {exc}")
            return
        if not response.success:
            message = " ".join(str(response.message).splitlines())
            text = f"ERR failed {command}"
            if message:
                text += f" {message}"
            self._report(text)

    def _on_timeout(self, command: str, future) -> None:
        with self._lock:
            timer = self._pending.pop(future, None)
        if timer is None:
            # The call completed first.
            return
        try:
            future.cancel()
        except Exception:
            pass
        self._report(f"ERR timeout {command}")

    def _report(self, text: str) -> None:
        logger.warning(f"Robot control command failed: {text}")
        self._on_error(text)
