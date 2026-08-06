"""Tests for the battery status provider.

The provider imports rclpy lazily, so tests inject fake ``rclpy`` /
``sensor_msgs`` modules into ``sys.modules`` and drive the subscription
callback by hand; no real ROS2 installation is required.
"""

import sys
import time
import types

import pytest

from xiaozhi_ble.battery import BatteryProvider


class _FakeNode:
    def __init__(self):
        self.subscriptions = []
        self.destroyed = False

    def create_subscription(self, msg_type, topic, callback, qos):
        self.subscriptions.append((msg_type, topic, callback, qos))

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
def fake_ros(monkeypatch):
    rclpy = _FakeRclpy()

    qos_mod = types.ModuleType("rclpy.qos")
    qos_mod.qos_profile_sensor_data = object()
    rclpy.qos = qos_mod

    class BatteryState:
        POWER_SUPPLY_STATUS_UNKNOWN = 0
        POWER_SUPPLY_STATUS_CHARGING = 1
        POWER_SUPPLY_STATUS_DISCHARGING = 2
        POWER_SUPPLY_STATUS_NOT_CHARGING = 3
        POWER_SUPPLY_STATUS_FULL = 4

    sensor_mod = types.ModuleType("sensor_msgs")
    msg_mod = types.ModuleType("sensor_msgs.msg")
    msg_mod.BatteryState = BatteryState
    sensor_mod.msg = msg_mod

    monkeypatch.setitem(sys.modules, "rclpy", rclpy)
    monkeypatch.setitem(sys.modules, "rclpy.qos", qos_mod)
    monkeypatch.setitem(sys.modules, "sensor_msgs", sensor_mod)
    monkeypatch.setitem(sys.modules, "sensor_msgs.msg", msg_mod)

    return rclpy, BatteryState


def _message(msg_type, percentage, supply_status=0):
    msg = msg_type()
    msg.percentage = percentage
    msg.power_supply_status = supply_status
    return msg


def _subscription_callback(provider, fake_ros):
    rclpy, _ = fake_ros
    _, _, callback, _ = rclpy.node.subscriptions[0]
    return callback


def test_provider_is_disabled_without_topic(fake_ros):
    provider = BatteryProvider("", lambda _p, _s: None)
    assert not provider.enabled
    provider.start()
    assert provider._thread is None
    rclpy, _ = fake_ros
    assert not rclpy.initialized


def test_provider_subscribes_and_reports_status(fake_ros):
    updates = []
    provider = BatteryProvider("/battery_state", lambda p, s: updates.append((p, s)))
    provider.start()
    try:
        rclpy, msg_type = fake_ros
        assert rclpy.initialized
        assert len(rclpy.node.subscriptions) == 1
        _, topic, callback, _ = rclpy.node.subscriptions[0]
        assert topic == "/battery_state"

        callback(_message(msg_type, 0.67, msg_type.POWER_SUPPLY_STATUS_CHARGING))
        assert updates == [(0.67, "CHARGING")]

        callback(_message(msg_type, 0.66, msg_type.POWER_SUPPLY_STATUS_DISCHARGING))
        assert updates == [(0.67, "CHARGING"), (0.66, "DISCHARGING")]
    finally:
        provider.stop()

    assert rclpy.node.destroyed
    assert not rclpy.initialized


def test_provider_ignores_nan_and_keeps_last_value(fake_ros):
    updates = []
    provider = BatteryProvider("/battery_state", lambda p, s: updates.append((p, s)))
    provider.start()
    try:
        _, msg_type = fake_ros
        callback = _subscription_callback(provider, fake_ros)
        callback(_message(msg_type, 0.5, msg_type.POWER_SUPPLY_STATUS_FULL))
        callback(_message(msg_type, float("nan"), msg_type.POWER_SUPPLY_STATUS_UNKNOWN))
        callback(_message(msg_type, float("inf"), msg_type.POWER_SUPPLY_STATUS_UNKNOWN))
        assert updates == [(0.5, "FULL")]
    finally:
        provider.stop()


def test_provider_maps_all_supply_statuses(fake_ros):
    updates = []
    provider = BatteryProvider("/battery_state", lambda p, s: updates.append((p, s)))
    provider.start()
    try:
        _, msg_type = fake_ros
        callback = _subscription_callback(provider, fake_ros)
        for constant, name in [
            (msg_type.POWER_SUPPLY_STATUS_CHARGING, "CHARGING"),
            (msg_type.POWER_SUPPLY_STATUS_DISCHARGING, "DISCHARGING"),
            (msg_type.POWER_SUPPLY_STATUS_NOT_CHARGING, "NOT_CHARGING"),
            (msg_type.POWER_SUPPLY_STATUS_FULL, "FULL"),
        ]:
            callback(_message(msg_type, 0.5, constant))
        assert [s for _, s in updates] == [
            "CHARGING",
            "DISCHARGING",
            "NOT_CHARGING",
            "FULL",
        ]
    finally:
        provider.stop()


def test_provider_disabled_when_ros_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "rclpy", None)
    provider = BatteryProvider("/battery_state", lambda _p, _s: None)
    provider.start()
    assert provider._thread is None
