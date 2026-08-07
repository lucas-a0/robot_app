"""Tests for the battery status provider.

The provider attaches its subscription to the shared ROS runtime node, so
tests hand it a fake node and drive the subscription callback by hand; the
``rclpy.qos`` / ``sensor_msgs`` imports are satisfied by fake modules
injected into ``sys.modules``. No real ROS2 installation is required.
"""

import sys
import types

import pytest

from xiaozhi_ble.battery import BatteryProvider


class _FakeNode:
    def __init__(self):
        self.subscriptions = []
        self.destroyed_subscriptions = []

    def create_subscription(self, msg_type, topic, callback, qos):
        subscription = (msg_type, topic, callback, qos)
        self.subscriptions.append(subscription)
        return subscription

    def destroy_subscription(self, subscription):
        self.destroyed_subscriptions.append(subscription)


@pytest.fixture
def fake_ros(monkeypatch):
    qos_mod = types.ModuleType("rclpy.qos")
    qos_mod.qos_profile_sensor_data = object()
    rclpy_mod = types.ModuleType("rclpy")
    rclpy_mod.qos = qos_mod

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

    monkeypatch.setitem(sys.modules, "rclpy", rclpy_mod)
    monkeypatch.setitem(sys.modules, "rclpy.qos", qos_mod)
    monkeypatch.setitem(sys.modules, "sensor_msgs", sensor_mod)
    monkeypatch.setitem(sys.modules, "sensor_msgs.msg", msg_mod)

    return BatteryState


def _message(msg_type, percentage, supply_status=0):
    msg = msg_type()
    msg.percentage = percentage
    msg.power_supply_status = supply_status
    return msg


def _subscription_callback(node):
    _, _, callback, _ = node.subscriptions[0]
    return callback


def test_provider_is_disabled_without_topic(fake_ros):
    node = _FakeNode()
    provider = BatteryProvider("", lambda _p, _s: None)
    assert not provider.enabled
    provider.start(node)
    assert node.subscriptions == []


def test_provider_subscribes_and_reports_status(fake_ros):
    updates = []
    node = _FakeNode()
    provider = BatteryProvider("/battery_state", lambda p, s: updates.append((p, s)))
    provider.start(node)

    msg_type = fake_ros
    assert len(node.subscriptions) == 1
    _, topic, callback, _ = node.subscriptions[0]
    assert topic == "/battery_state"

    callback(_message(msg_type, 0.67, msg_type.POWER_SUPPLY_STATUS_CHARGING))
    assert updates == [(0.67, "CHARGING")]

    callback(_message(msg_type, 0.66, msg_type.POWER_SUPPLY_STATUS_DISCHARGING))
    assert updates == [(0.67, "CHARGING"), (0.66, "DISCHARGING")]

    provider.stop()
    assert node.destroyed_subscriptions == node.subscriptions


def test_provider_ignores_nan_and_keeps_last_value(fake_ros):
    updates = []
    node = _FakeNode()
    provider = BatteryProvider("/battery_state", lambda p, s: updates.append((p, s)))
    provider.start(node)
    msg_type = fake_ros
    callback = _subscription_callback(node)
    callback(_message(msg_type, 0.5, msg_type.POWER_SUPPLY_STATUS_FULL))
    callback(_message(msg_type, float("nan"), msg_type.POWER_SUPPLY_STATUS_UNKNOWN))
    callback(_message(msg_type, float("inf"), msg_type.POWER_SUPPLY_STATUS_UNKNOWN))
    assert updates == [(0.5, "FULL")]
    provider.stop()


def test_provider_maps_all_supply_statuses(fake_ros):
    updates = []
    node = _FakeNode()
    provider = BatteryProvider("/battery_state", lambda p, s: updates.append((p, s)))
    provider.start(node)
    msg_type = fake_ros
    callback = _subscription_callback(node)
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
    provider.stop()


def test_provider_disabled_when_sensor_msgs_unavailable(monkeypatch):
    node = _FakeNode()
    monkeypatch.setitem(sys.modules, "sensor_msgs", None)
    provider = BatteryProvider("/battery_state", lambda _p, _s: None)
    provider.start(node)
    assert node.subscriptions == []
