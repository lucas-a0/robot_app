"""Tests for the initial-pose publisher.

The publisher attaches its publisher to the shared ROS runtime node, so
tests hand it a fake node and inspect the published messages; the
``geometry_msgs`` import is satisfied by a fake module injected into
``sys.modules``. No real ROS2 installation is required.
"""

import sys
import types

import pytest

from xiaozhi_ble.initial_pose import (
    COVARIANCE,
    FRAME_ID,
    ORIENTATION_W,
    ORIENTATION_Z,
    POSITION_X,
    POSITION_Y,
)
from xiaozhi_ble.initial_pose import InitialPosePublisher


class _FakePoint:
    def __init__(self):
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0


class _FakeQuaternion:
    def __init__(self):
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.w = 0.0


class _FakePose:
    def __init__(self):
        self.position = _FakePoint()
        self.orientation = _FakeQuaternion()


class _FakePoseWithCovariance:
    def __init__(self):
        self.pose = _FakePose()
        self.covariance = [0.0] * 36


class _FakeHeader:
    def __init__(self):
        self.stamp = None
        self.frame_id = ""


class _FakeMessage:
    def __init__(self):
        self.header = _FakeHeader()
        self.pose = _FakePoseWithCovariance()


class _FakeStamp:
    def __init__(self, sec, nanosec):
        self.sec = sec
        self.nanosec = nanosec


class _FakeClock:
    def now(self):
        return _FakeTime()


class _FakeTime:
    def to_msg(self):
        return _FakeStamp(1234, 5678)


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

    def get_clock(self):
        return _FakeClock()


@pytest.fixture
def fake_geometry_msgs(monkeypatch):
    geometry_mod = types.ModuleType("geometry_msgs")
    msg_mod = types.ModuleType("geometry_msgs.msg")
    msg_mod.PoseWithCovarianceStamped = _FakeMessage
    geometry_mod.msg = msg_mod
    monkeypatch.setitem(sys.modules, "geometry_msgs", geometry_mod)
    monkeypatch.setitem(sys.modules, "geometry_msgs.msg", msg_mod)


def test_publisher_is_disabled_without_topic(fake_geometry_msgs):
    node = _FakeNode()
    publisher = InitialPosePublisher("")
    assert not publisher.enabled
    publisher.start(node)
    assert node.publishers == []
    assert publisher.execute("1") == "ERR unavailable"


def test_publisher_publishes_fixed_pose(fake_geometry_msgs):
    node = _FakeNode()
    publisher = InitialPosePublisher("/initialpose")
    publisher.start(node)

    assert len(node.publishers) == 1
    ros_publisher = node.publishers[0]
    assert ros_publisher.msg_type is _FakeMessage
    assert ros_publisher.topic == "/initialpose"

    assert publisher.execute("1") == "OK"
    message = ros_publisher.published[-1]
    assert message.header.stamp.sec == 1234
    assert message.header.stamp.nanosec == 5678
    assert message.header.frame_id == FRAME_ID == "map"
    assert message.pose.pose.position.x == POSITION_X
    assert message.pose.pose.position.y == POSITION_Y
    assert message.pose.pose.position.z == 0.0
    assert message.pose.pose.orientation.x == 0.0
    assert message.pose.pose.orientation.y == 0.0
    assert message.pose.pose.orientation.z == ORIENTATION_Z
    assert message.pose.pose.orientation.w == ORIENTATION_W
    assert message.pose.covariance == list(COVARIANCE)
    assert message.pose.covariance[0] == 0.25
    assert message.pose.covariance[7] == 0.25
    assert message.pose.covariance[35] == 0.06853891945200942

    publisher.stop()
    assert node.destroyed_publishers == node.publishers
    assert publisher.execute("1") == "ERR unavailable"


def test_publisher_ignores_payload_and_republishes(fake_geometry_msgs):
    node = _FakeNode()
    publisher = InitialPosePublisher("/initialpose")
    publisher.start(node)

    # Any non-empty payload means "reset now"; identical writes republish.
    for text in ("1", "reset", "  0.5 -0.5 0.0 1.0  "):
        assert publisher.execute(text) == "OK"
    assert len(node.publishers[0].published) == 3
    publisher.stop()


def test_publisher_rejects_empty_writes(fake_geometry_msgs):
    node = _FakeNode()
    publisher = InitialPosePublisher("/initialpose")
    publisher.start(node)

    assert publisher.execute("") == "ERR command"
    assert publisher.execute("   \n") == "ERR command"
    assert node.publishers[0].published == []
    publisher.stop()


def test_publisher_disabled_when_geometry_msgs_unavailable(monkeypatch):
    node = _FakeNode()
    monkeypatch.setitem(sys.modules, "geometry_msgs", None)
    publisher = InitialPosePublisher("/initialpose")
    publisher.start(node)
    assert node.publishers == []
    assert publisher.execute("1") == "ERR unavailable"
