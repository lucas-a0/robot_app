"""Tests for the navigation launch-task manager.

No ROS2 installation is required: the manager's shell wrapper is replaced
with ``exec "$@"`` and the task templates point at plain bash scripts, so
real child processes exercise the spawn/drain/wait/stop logic. The pure
argv builder is tested against the production templates.
"""

import subprocess
import threading
import time

import pytest

from xiaozhi_ble import nav_tasks
from xiaozhi_ble.nav_tasks import (
    TASK_TEMPLATES,
    NavTaskManager,
    NavTaskTemplate,
    build_launch_argv,
)

_NOTIFY_TIMEOUT = 5.0


def _write_script(tmp_path, name, body):
    script = tmp_path / name
    script.write_text(body)
    return str(script)


def _make_manager(tmp_path, scripts=None, on_notify=None):
    """Build a manager whose tasks run plain bash scripts instead of ROS2."""
    scripts = scripts or {}
    tasks = {
        "localization": NavTaskTemplate(
            package="/bin/bash",
            launch_file=scripts.get(
                "localization", _write_script(tmp_path, "loc.sh", "echo loc ready\nsleep 30\n")
            ),
            defaults={"map": "/tmp/map.yaml", "start_lio": "true"},
        ),
        "navigation": NavTaskTemplate(
            package="/bin/bash",
            launch_file=scripts.get(
                "navigation", _write_script(tmp_path, "nav.sh", "echo nav ready\nsleep 30\n")
            ),
            defaults={"autostart": "true"},
        ),
    }
    if "crash" in scripts:
        tasks["crash"] = NavTaskTemplate(
            package="/bin/bash",
            launch_file=scripts["crash"],
            defaults={},
        )
    return NavTaskManager(
        on_notify=on_notify,
        tasks=tasks,
        workspace_dir=tmp_path,
        log_dir=tmp_path / "logs",
        shell_command='exec "$@"',
    )


class _NotifyCollector:
    def __init__(self):
        self.texts = []
        self._event = threading.Event()

    def __call__(self, text):
        self.texts.append(text)
        self._event.set()

    def wait_for(self, predicate, timeout=_NOTIFY_TIMEOUT):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(predicate(text) for text in self.texts):
                return True
            self._event.wait(0.05)
            self._event.clear()
        return any(predicate(text) for text in self.texts)


@pytest.fixture
def manager(tmp_path):
    mgr = _make_manager(tmp_path)
    yield mgr
    mgr.stop_all()


def test_build_launch_argv_uses_defaults_in_order():
    template = TASK_TEMPLATES["localization"]
    argv = build_launch_argv(template, dict(template.defaults))
    assert argv[0] == "robot_nav617_navigation"
    assert argv[1] == "courtyard_localization_bringup.launch.py"
    assert argv[2].startswith("map:=")
    assert "scan_topic:=/scan" in argv
    assert "start_lidar:=true" in argv
    assert "start_localization:=true" in argv


def test_status_initial(manager):
    assert manager.execute("STATUS") == (
        "STATE localization STOPPED navigation STOPPED"
    )


def test_unknown_verb(manager):
    assert manager.execute("FOO") == "ERR command"
    assert manager.execute("START") == "ERR command"
    assert manager.execute("") == "ERR command"


def test_unknown_task(manager):
    assert manager.execute("START mapping") == "ERR task mapping"
    assert manager.execute("STOP mapping") == "ERR task mapping"


def test_param_rejected(manager):
    assert (
        manager.execute("START localization bad_key=1")
        == "ERR param localization bad_key"
    )
    assert manager.execute("START localization map=") == "ERR param localization map"
    assert (
        manager.execute("START localization verbose")
        == "ERR param localization verbose"
    )
    # Values with whitespace are impossible: the tail splits into a token
    # without "=", which is rejected as an unknown parameter.
    assert (
        manager.execute("START localization map=/a b.yaml")
        == "ERR param localization b.yaml"
    )


def test_stop_not_running(manager):
    assert manager.execute("STOP localization") == "ERR state localization not_running"


def test_navigation_requires_localization(manager):
    assert (
        manager.execute("START navigation")
        == "ERR state localization not_running"
    )


def test_start_and_stop(tmp_path):
    collector = _NotifyCollector()
    mgr = _make_manager(tmp_path, on_notify=collector)
    log_path = tmp_path / "logs" / "localization.log"
    try:
        assert mgr.execute("start LOCALIZATION") == "STARTED localization"
        assert mgr.execute("STATUS") == (
            "STATE localization RUNNING navigation STOPPED"
        )
        assert mgr.execute("START localization") == (
            "ERR state localization already_running"
        )
        # Wait for the drainer to land the script's first line in the log
        # before stopping; an instant SIGINT can kill the child before it
        # prints anything.
        deadline = time.monotonic() + _NOTIFY_TIMEOUT
        while time.monotonic() < deadline:
            if log_path.exists() and "loc ready" in log_path.read_text():
                break
            time.sleep(0.05)
        assert "loc ready" in log_path.read_text()
        assert mgr.execute("STOP localization") == "STOPPING localization"
        assert collector.wait_for(lambda t: t == "STOPPED localization")
        assert mgr.execute("STATUS") == (
            "STATE localization STOPPED navigation STOPPED"
        )
    finally:
        mgr.stop_all()


def test_start_navigation_after_localization(tmp_path):
    mgr = _make_manager(tmp_path)
    try:
        assert mgr.execute("START localization") == "STARTED localization"
        assert mgr.execute("START navigation") == "STARTED navigation"
        assert mgr.execute("STATUS") == (
            "STATE localization RUNNING navigation RUNNING"
        )
    finally:
        mgr.stop_all()


def test_crash_exit_notification(tmp_path):
    crash = _write_script(tmp_path, "crash.sh", "echo boom error\nexit 3\n")
    collector = _NotifyCollector()
    mgr = _make_manager(tmp_path, scripts={"crash": crash}, on_notify=collector)
    try:
        assert mgr.execute("START crash") == "STARTED crash"
        assert collector.wait_for(lambda t: t == "EXITED crash 3 boom error")
        assert "crash STOPPED" in mgr.execute("STATUS")
    finally:
        mgr.stop_all()


def test_override_reaches_argv(tmp_path, monkeypatch):
    captured = {}
    real_popen = subprocess.Popen

    def spy_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return real_popen(cmd, **kwargs)

    monkeypatch.setattr(nav_tasks.subprocess, "Popen", spy_popen)
    mgr = _make_manager(tmp_path)
    try:
        assert mgr.execute("START localization map=/tmp/other.yaml") == (
            "STARTED localization"
        )
        cmd = captured["cmd"]
        assert cmd[:4] == ["bash", "-c", 'exec "$@"', "xiaozhi-nav-task"]
        assert "map:=/tmp/other.yaml" in cmd
        assert "start_lio:=true" in cmd
    finally:
        mgr.stop_all()


def test_workspace_missing_disables_start(tmp_path):
    mgr = NavTaskManager(
        tasks=_make_manager(tmp_path)._tasks,
        workspace_dir=tmp_path / "missing",
        log_dir=tmp_path / "logs",
        shell_command='exec "$@"',
    )
    assert mgr.execute("START localization") == "ERR unavailable"


def test_stop_all_terminates_everything(tmp_path):
    collector = _NotifyCollector()
    mgr = _make_manager(tmp_path, on_notify=collector)
    assert mgr.execute("START localization") == "STARTED localization"
    assert mgr.execute("START navigation") == "STARTED navigation"
    mgr.stop_all()
    assert collector.wait_for(lambda t: t == "STOPPED localization")
    assert collector.wait_for(lambda t: t == "STOPPED navigation")
    assert mgr.execute("STATUS") == (
        "STATE localization STOPPED navigation STOPPED"
    )
