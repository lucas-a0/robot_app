"""Tests for the cmd_vel Twist publisher.

The publisher attaches its publisher to the shared ROS runtime node, so
tests hand it a fake node and inspect the published messages; the
``geometry_msgs`` import is satisfied by a fake module injected into
``sys.modules``. No real ROS2 installation is required.
"""

import sys
import types

import pytest

from xiaozhi_ble.cmd_vel import CmdVelPublisher


class _FakeVector3:
    def __init__(self):
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0


class _FakeTwist:
    def __init__(self):
        self.linear = _FakeVector3()
        self.angular = _FakeVector3()


class _FakePublisher:
    def __init__(self, msg_type, topic, qos):
        self.msg_type = msg_type
        self.topic = topic
        self.qos = qos
        self.published = []

    def publish(self, message):
        self.published.append(message)


class _FakeNode:
    def __init__(self):
        self.publishers = []
        self.destroyed_publishers = []

    def create_publisher(self, msg_type, topic, qos):
        publisher = _FakePublisher(msg_type, topic, qos)
        self.publishers.append(publisher)
        return publisher

    def destroy_publisher(self, publisher):
        self.destroyed_publishers.append(publisher)


@pytest.fixture
def fake_geometry_msgs(monkeypatch):
    geometry_mod = types.ModuleType("geometry_msgs")
    msg_mod = types.ModuleType("geometry_msgs.msg")
    msg_mod.Twist = _FakeTwist
    geometry_mod.msg = msg_mod
    monkeypatch.setitem(sys.modules, "geometry_msgs", geometry_mod)
    monkeypatch.setitem(sys.modules, "geometry_msgs.msg", msg_mod)


def test_publisher_is_disabled_without_topic(fake_geometry_msgs):
    node = _FakeNode()
    publisher = CmdVelPublisher("")
    assert not publisher.enabled
    publisher.start(node)
    assert node.publishers == []
    assert publisher.execute("-0.30 0.0") == "ERR unavailable"


def test_publisher_publishes_twist_messages(fake_geometry_msgs):
    node = _FakeNode()
    publisher = CmdVelPublisher("/cmd_vel")
    publisher.start(node)

    assert len(node.publishers) == 1
    ros_publisher = node.publishers[0]
    assert ros_publisher.msg_type is _FakeTwist
    assert ros_publisher.topic == "/cmd_vel"

    assert publisher.execute("-0.30 0.0") == "OK -0.3 0.0"
    message = ros_publisher.published[-1]
    assert message.linear.x == -0.3
    assert message.angular.z == 0.0

    assert publisher.execute("0.0 1.5") == "OK 0.0 1.5"
    message = ros_publisher.published[-1]
    assert message.linear.x == 0.0
    assert message.angular.z == 1.5

    publisher.stop()
    assert node.destroyed_publishers == node.publishers
    assert publisher.execute("-0.30 0.0") == "ERR unavailable"


def test_publisher_republishes_identical_values(fake_geometry_msgs):
    node = _FakeNode()
    publisher = CmdVelPublisher("/cmd_vel")
    publisher.start(node)

    for _ in range(3):
        assert publisher.execute("0.2 0.0") == "OK 0.2 0.0"
    assert len(node.publishers[0].published) == 3
    publisher.stop()


def test_publisher_rejects_malformed_writes(fake_geometry_msgs):
    node = _FakeNode()
    publisher = CmdVelPublisher("/cmd_vel")
    publisher.start(node)

    assert publisher.execute("") == "ERR command"
    assert publisher.execute("0.3") == "ERR command"
    assert publisher.execute("0.3 0.0 0.0") == "ERR command"
    assert publisher.execute("fast 0.0") == "ERR command"
    assert publisher.execute("nan 0.0") == "ERR command"
    assert publisher.execute("inf 0.0") == "ERR command"
    assert node.publishers[0].published == []
    publisher.stop()


def test_publisher_disabled_when_geometry_msgs_unavailable(monkeypatch):
    node = _FakeNode()
    monkeypatch.setitem(sys.modules, "geometry_msgs", None)
    publisher = CmdVelPublisher("/cmd_vel")
    publisher.start(node)
    assert node.publishers == []
    assert publisher.execute("-0.30 0.0") == "ERR unavailable"
