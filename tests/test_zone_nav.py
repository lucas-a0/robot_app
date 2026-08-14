"""Tests for the zone-navigation publisher.

The navigator attaches its publisher to the shared ROS runtime node, so
tests hand it a fake node and inspect the published messages; the
``std_msgs`` import is satisfied by a fake module injected into
``sys.modules``. No real ROS2 installation is required.
"""

import sys
import types

import pytest

from xiaozhi_ble.zone_nav import ZONES, ZoneNavigator


class _FakeString:
    def __init__(self):
        self.data = ""


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
def fake_std_msgs(monkeypatch):
    std_mod = types.ModuleType("std_msgs")
    msg_mod = types.ModuleType("std_msgs.msg")
    msg_mod.String = _FakeString
    std_mod.msg = msg_mod
    monkeypatch.setitem(sys.modules, "std_msgs", std_mod)
    monkeypatch.setitem(sys.modules, "std_msgs.msg", msg_mod)


def test_navigator_is_disabled_without_topic(fake_std_msgs):
    node = _FakeNode()
    navigator = ZoneNavigator("")
    assert not navigator.enabled
    navigator.start(node)
    assert node.publishers == []
    assert navigator.execute("charging_zone") == "ERR unavailable"


def test_navigator_publishes_zone_names(fake_std_msgs):
    node = _FakeNode()
    navigator = ZoneNavigator("/xiaozhi_topic")
    navigator.start(node)

    assert len(node.publishers) == 1
    publisher = node.publishers[0]
    assert publisher.msg_type is _FakeString
    assert publisher.topic == "/xiaozhi_topic"

    for zone in ZONES:
        assert navigator.execute(zone) == f"OK {zone}"
    assert [message.data for message in publisher.published] == list(ZONES)

    navigator.stop()
    assert node.destroyed_publishers == node.publishers
    assert navigator.execute("charging_zone") == "ERR unavailable"


def test_navigator_rejects_unknown_zones(fake_std_msgs):
    node = _FakeNode()
    navigator = ZoneNavigator("/xiaozhi_topic")
    navigator.start(node)

    assert navigator.execute("garage_zone") == "ERR command"
    assert navigator.execute("") == "ERR command"
    assert node.publishers[0].published == []
    navigator.stop()


def test_navigator_disabled_when_std_msgs_unavailable(monkeypatch):
    node = _FakeNode()
    monkeypatch.setitem(sys.modules, "std_msgs", None)
    navigator = ZoneNavigator("/xiaozhi_topic")
    navigator.start(node)
    assert node.publishers == []
    assert navigator.execute("charging_zone") == "ERR unavailable"
