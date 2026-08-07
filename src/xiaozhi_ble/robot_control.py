"""Robot behavior control via ROS2 Trigger service calls.

The App writes a command name to the robot-control BLE characteristic; the
bridge maps it to a ``std_srvs/Trigger`` service from configuration and
calls it asynchronously on the shared ROS runtime node (see
ros_runtime.py). Success is silent; failures are reported back through the
error callback so the App gets a notification.

std_srvs is imported lazily on :meth:`start` so the rest of the bridge
still runs when the ROS2 environment is incomplete; in that case every
command is answered with ``ERR unavailable``.
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


class RobotControl:
    """Call configured std_srvs/Trigger services by command name.

    An empty ``commands`` mapping disables the actuator. The rclpy context
    and spin thread are owned by the shared ROS runtime; the actuator only
    creates service clients on the runtime's node.
    """

    def __init__(
        self,
        commands: dict[str, str],
        call_timeout_secs: float = 10.0,
        on_error: RobotErrorCallback | None = None,
    ) -> None:
        self._commands = {
            name.strip().lower(): service for name, service in commands.items()
        }
        self._call_timeout_secs = call_timeout_secs
        self._on_error = on_error or (lambda _text: None)
        self._trigger = None
        self._node = None
        self._clients: dict[str, object] = {}
        self._lock = threading.Lock()
        # future -> timeout timer, for calls still in flight.
        self._pending: dict[object, threading.Timer] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._commands)

    def start(self, node) -> None:
        """Attach to the shared runtime node (no-op when disabled)."""
        if not self.enabled:
            logger.info("Robot control disabled: robot_control.commands is empty")
            return
        try:
            from std_srvs.srv import Trigger
        except ImportError:
            logger.error(
                "robot_control.commands is set but std_srvs is not importable "
                "(is the ROS2 environment sourced?); robot control disabled"
            )
            return
        self._trigger = Trigger
        self._node = node
        logger.info(f"Robot control started: commands={sorted(self._commands)}")

    def stop(self) -> None:
        """Detach from the node and cancel pending timeout timers."""
        with self._lock:
            timers = list(self._pending.values())
            self._pending.clear()
            self._clients.clear()
            self._node = None
        for timer in timers:
            timer.cancel()

    def execute(self, command: str) -> str | None:
        """Dispatch a command; returns an immediate error text or None.

        None means the Trigger call is in flight; a later failure arrives
        through the error callback. Success stays silent by design.
        """
        with self._lock:
            node = self._node
        if node is None:
            return "ERR unavailable"
        service = self._commands.get(command)
        if service is None:
            return "ERR command"
        with self._lock:
            client = self._clients.get(service)
            if client is None:
                client = node.create_client(self._trigger, service)
                self._clients[service] = client
        if not client.wait_for_service(timeout_sec=_SERVICE_WAIT_SECS):
            return f"ERR unavailable {command}"
        future = client.call_async(self._trigger.Request())
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
