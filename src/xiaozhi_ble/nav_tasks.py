"""Navigation launch-task control via managed subprocesses.

The App writes START/STOP/STATUS commands to the Nav Task BLE characteristic;
the bridge maps them to two fixed ``ros2 launch`` tasks (localization bringup
and Nav2 navigation) and runs them as managed child processes. This module is
the project's sanctioned exception to the "no subprocess" rule: launching Nav2
is inherently process-based, and the children are fully managed here (own
process group, SIGINT teardown with a SIGKILL fallback, output captured to a
log file, exit reported back to the App). The output is never parsed for data.

Both task templates are hardcoded by design; there is no configuration
surface. The only environment the children need (workspace setup scripts,
ROS_DOMAIN_ID, ROS_LOCALHOST_ONLY) is baked into the launch wrapper below.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from loguru import logger

# Receives an asynchronous notify text, e.g. "STOPPED navigation" or
# "EXITED navigation 1 [amcl]: map could not be loaded".
NavTaskNotifyCallback = Callable[[str], None]

_WORKSPACE_DIR = Path("~/mid360_nav_project/ros2_ws").expanduser()
_LOG_DIR = Path("~/.cache/xiaozhi-ble/logs").expanduser()

# The wrapper sources the ROS2 and workspace environments, then replaces
# itself with ros2 launch; task arguments go through "$@" so they never
# become part of the shell command string (no quoting/injection issues).
_SHELL_COMMAND = (
    "source /opt/ros/humble/setup.bash && "
    "source install/setup.bash && "
    'exec ros2 launch "$@"'
)
_ENV_OVERRIDES = {
    "ROS_DOMAIN_ID": "42",
    "ROS_LOCALHOST_ONLY": "0",
}

# How long stop()/stop_all() waits for the process group after SIGINT
# before escalating to SIGKILL.
_STOP_TIMEOUT_SECS = 10.0

# Ring-buffer depth kept from the task output; the last non-empty line is
# attached to EXITED notifications as a crash summary.
_TAIL_LINES = 20


class NavTaskTemplate:
    """A fixed launch task: launch file plus its overridable arguments."""

    def __init__(
        self,
        package: str,
        launch_file: str,
        defaults: dict[str, str],
    ) -> None:
        self.package = package
        self.launch_file = launch_file
        self.defaults = dict(defaults)


_PARAMS_FILE = (
    "/home/sunrise/mid360_nav_project/ros2_ws/src/robot_nav617/"
    "navigation/config/nav2_mid360_params_exhibition.yaml"
)

TASK_TEMPLATES: dict[str, NavTaskTemplate] = {
    "localization": NavTaskTemplate(
        package="robot_nav617_navigation",
        launch_file="courtyard_localization_bringup.launch.py",
        defaults={
            "map": (
                "/home/sunrise/mid360_nav_project/ros2_ws/src/robot_nav617/"
                "navigation/maps/fastlio_map_nav2_v1/map.yaml"
            ),
            "params_file": _PARAMS_FILE,
            "scan_topic": "/scan",
            "start_lidar": "true",
            "start_lio": "true",
            "start_scan": "true",
            "start_localization": "true",
        },
    ),
    "navigation": NavTaskTemplate(
        package="robot_nav617_navigation",
        launch_file="courtyard_navigation.launch.py",
        defaults={
            "params_file": _PARAMS_FILE,
            "start_lio_odom_twist": "true",
            "start_xiaozhi_bridge": "false",
            "use_sim_time": "false",
            "autostart": "true",
        },
    ),
}


def build_launch_argv(template: NavTaskTemplate, params: dict[str, str]) -> list[str]:
    """Build the ros2 launch argument vector (``key:=value`` launch args)."""
    return [
        template.package,
        template.launch_file,
        *(f"{key}:={value}" for key, value in params.items()),
    ]


class _RunningTask:
    """Bookkeeping for one launched task process."""

    def __init__(self, name: str, proc: subprocess.Popen, log_file) -> None:
        self.name = name
        self.proc = proc
        self.log_file = log_file
        self.stop_requested = False
        self.tail: deque[str] = deque(maxlen=_TAIL_LINES)
        self.drainer: threading.Thread | None = None


class NavTaskManager:
    """Start/stop the fixed navigation launch tasks on BLE command.

    ``execute()`` parses a Nav Task write and returns the immediate reply
    text; asynchronous events (STOPPED, EXITED) go through ``on_notify``.
    All public methods are safe to call from the GLib event-loop thread:
    process spawn is fast and the SIGINT wait happens on the waiter thread.
    """

    def __init__(
        self,
        on_notify: NavTaskNotifyCallback | None = None,
        tasks: dict[str, NavTaskTemplate] | None = None,
        workspace_dir: Path = _WORKSPACE_DIR,
        log_dir: Path = _LOG_DIR,
        shell_command: str = _SHELL_COMMAND,
        stop_timeout_secs: float = _STOP_TIMEOUT_SECS,
    ) -> None:
        self._on_notify = on_notify or (lambda _text: None)
        self._tasks = tasks if tasks is not None else TASK_TEMPLATES
        self._workspace_dir = Path(workspace_dir)
        self._log_dir = Path(log_dir)
        self._shell_command = shell_command
        self._stop_timeout_secs = stop_timeout_secs
        self._lock = threading.Lock()
        self._running: dict[str, _RunningTask] = {}

    def execute(self, text: str) -> str:
        """Handle a Nav Task write; returns the immediate reply text."""
        parts = text.split()
        if not parts:
            return "ERR command"
        verb = parts[0].lower()
        if verb == "status":
            return self._status_text()
        if verb not in ("start", "stop") or len(parts) < 2:
            return "ERR command"
        task = parts[1].lower()
        template = self._tasks.get(task)
        if template is None:
            return f"ERR task {task}"
        if verb == "stop":
            if len(parts) != 2:
                return "ERR command"
            return self._stop(task)
        overrides: dict[str, str] = {}
        for token in parts[2:]:
            key, sep, value = token.partition("=")
            key = key.lower()
            if not sep or not value or key not in template.defaults:
                return f"ERR param {task} {key or token}"
            overrides[key] = value
        return self._start(task, template, overrides)

    def stop_all(self) -> None:
        """SIGINT every running task group; SIGKILL whatever survives."""
        with self._lock:
            running = list(self._running.values())
        for rt in running:
            rt.stop_requested = True
            self._signal_group(rt, signal.SIGINT)
        deadline = time.monotonic() + self._stop_timeout_secs
        for rt in running:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                rt.proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                logger.warning(f"Nav task {rt.name} ignored SIGINT; sending SIGKILL")
                self._signal_group(rt, signal.SIGKILL)
        # The waiter threads do the final cleanup and STOPPED notifications.

    def _start(
        self,
        task: str,
        template: NavTaskTemplate,
        overrides: dict[str, str],
    ) -> str:
        with self._lock:
            existing = self._running.get(task)
            if existing is not None:
                state = "stopping" if existing.stop_requested else "already_running"
                return f"ERR state {task} {state}"
            if task == "navigation" and "localization" not in self._running:
                return "ERR state localization not_running"
        if not self._workspace_dir.is_dir():
            logger.error(
                f"Nav task workspace missing: {self._workspace_dir}; "
                "nav task control unavailable"
            )
            return "ERR unavailable"

        params = {**template.defaults, **overrides}
        argv = build_launch_argv(template, params)
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            log_file = open(self._log_dir / f"{task}.log", "w", encoding="utf-8")
        except OSError as exc:
            logger.exception(f"Failed to open nav task log for {task}")
            return f"ERR spawn {task} {exc}"
        try:
            proc = subprocess.Popen(
                ["bash", "-c", self._shell_command, "xiaozhi-nav-task", *argv],
                cwd=self._workspace_dir,
                env={**os.environ, **_ENV_OVERRIDES},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            log_file.close()
            logger.exception(f"Failed to spawn nav task {task}")
            return f"ERR spawn {task} {exc}"

        rt = _RunningTask(task, proc, log_file)
        with self._lock:
            self._running[task] = rt
        rt.drainer = threading.Thread(
            target=self._drain_output,
            args=(rt,),
            name=f"xiaozhi-nav-out-{task}",
            daemon=True,
        )
        rt.drainer.start()
        threading.Thread(
            target=self._wait_exit,
            args=(rt,),
            name=f"xiaozhi-nav-wait-{task}",
            daemon=True,
        ).start()
        logger.info(f"Nav task started: {task} pid={proc.pid} argv={argv}")
        return f"STARTED {task}"

    def _stop(self, task: str) -> str:
        with self._lock:
            rt = self._running.get(task)
            if rt is None:
                return f"ERR state {task} not_running"
            if rt.stop_requested:
                return f"ERR state {task} stopping"
            rt.stop_requested = True
        self._signal_group(rt, signal.SIGINT)
        return f"STOPPING {task}"

    def _signal_group(self, rt: _RunningTask, sig: signal.Signals) -> None:
        # start_new_session=True made the child its own process-group leader,
        # so signalling the pgid reaches the whole launch tree at once.
        try:
            os.killpg(rt.proc.pid, sig)
        except (ProcessLookupError, PermissionError) as exc:
            logger.debug(f"Could not signal nav task {rt.name}: {exc}")

    def _drain_output(self, rt: _RunningTask) -> None:
        """Forward the task's merged stdout/stderr to its log file."""
        assert rt.proc.stdout is not None
        try:
            for raw in rt.proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                rt.tail.append(line)
                try:
                    rt.log_file.write(line + "\n")
                    rt.log_file.flush()
                except OSError:
                    pass
        finally:
            try:
                rt.log_file.close()
            except OSError:
                pass

    def _wait_exit(self, rt: _RunningTask) -> None:
        code = rt.proc.wait()
        # Wait for the drainer to flush the remaining pipe output so the
        # EXITED summary below sees the final lines.
        if rt.drainer is not None:
            rt.drainer.join(timeout=5.0)
        with self._lock:
            self._running.pop(rt.name, None)
        if rt.stop_requested:
            logger.info(f"Nav task stopped: {rt.name} code={code}")
            self._on_notify(f"STOPPED {rt.name}")
            return
        logger.warning(f"Nav task exited on its own: {rt.name} code={code}")
        detail = next((line for line in reversed(rt.tail) if line.strip()), "")
        text = f"EXITED {rt.name} {code}"
        if detail.strip():
            text += f" {detail.strip()}"
        self._on_notify(text)

    def _status_text(self) -> str:
        with self._lock:
            fields = []
            for name in self._tasks:
                rt = self._running.get(name)
                if rt is None:
                    state = "STOPPED"
                elif rt.stop_requested:
                    state = "STOPPING"
                else:
                    state = "RUNNING"
                fields.append(f"{name} {state}")
        return f"STATE {' '.join(fields)}"
