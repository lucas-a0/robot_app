"""Tests for the shared ROS runtime.

rclpy is imported lazily, so tests inject a fake ``rclpy`` module into
``sys.modules``; no real ROS2 installation is required.
"""

import sys
import time
import types

import pytest

from xiaozhi_ble.ros_runtime import RosRuntime


class _FakeNode:
    def __init__(self):
        self.destroyed = False

    def destroy_node(self):
        self.destroyed = True


class _FakeRclpy(types.ModuleType):
    def __init__(self):
        super().__init__("rclpy")
        self.initialized = False
        self.node = _FakeNode()

    def init(self, args=None):
        self.initialized = True

    def ok(self):
        return self.initialized

    def create_node(self, name):
        return self.node

    def spin_once(self, node, timeout_sec=0.0):
        time.sleep(min(timeout_sec, 0.01))

    def shutdown(self):
        self.initialized = False


@pytest.fixture
def fake_rclpy(monkeypatch):
    rclpy = _FakeRclpy()
    monkeypatch.setitem(sys.modules, "rclpy", rclpy)
    return rclpy


def test_runtime_starts_and_stops_context(fake_rclpy):
    runtime = RosRuntime()
    assert runtime.start()
    assert runtime.node is fake_rclpy.node
    assert fake_rclpy.initialized

    runtime.stop()

    assert runtime.node is None
    assert fake_rclpy.node.destroyed
    assert not fake_rclpy.initialized


def test_runtime_start_is_idempotent(fake_rclpy):
    runtime = RosRuntime()
    assert runtime.start()
    assert runtime.start()
    runtime.stop()


def test_runtime_disabled_when_rclpy_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "rclpy", None)
    runtime = RosRuntime()
    assert not runtime.start()
    assert runtime.node is None
