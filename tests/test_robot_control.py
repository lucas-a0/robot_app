"""Tests for the robot-control Trigger actuator.

The actuator attaches its service clients to the shared ROS runtime node,
so tests hand it a fake node/client/future; the ``std_srvs`` import is
satisfied by a fake module injected into ``sys.modules``. No real ROS2
installation is required.
"""

import sys
import time
import types

import pytest

from xiaozhi_ble.robot_control import TOPIC_COMMANDS, RobotControl

COMMANDS = {
    "stand_up": "/base_bridge/stand_up",
    "lie_down": "/base_bridge/lie_down",
}


class _FakeResponse:
    def __init__(self, success=True, message=""):
        self.success = success
        self.message = message


class _FakeFuture:
    def __init__(self):
        self.callbacks = []
        self.cancelled = False
        self._response = None
        self._exc = None

    def add_done_callback(self, callback):
        self.callbacks.append(callback)

    def result(self):
        if self._exc is not None:
            raise self._exc
        return self._response

    def cancel(self):
        self.cancelled = True

    def complete(self, response=None, exc=None):
        """Test helper: finish the call and fire the done callbacks."""
        self._response = response
        self._exc = exc
        for callback in self.callbacks:
            callback(self)


class _FakeClient:
    def __init__(self, service_available=True):
        self.service_available = service_available
        self.futures = []

    def wait_for_service(self, timeout_sec=0.0):
        return self.service_available

    def call_async(self, request):
        future = _FakeFuture()
        self.futures.append(future)
        return future


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
    def __init__(self, service_available=True):
        self.service_available = service_available
        self.clients = {}
        self.publishers = []
        self.destroyed_publishers = []

    def create_client(self, srv_type, service):
        client = _FakeClient(self.service_available)
        self.clients[service] = client
        return client

    def create_publisher(self, msg_type, topic, qos):
        publisher = _FakePublisher(msg_type, topic, qos)
        self.publishers.append(publisher)
        return publisher

    def destroy_publisher(self, publisher):
        self.destroyed_publishers.append(publisher)


@pytest.fixture
def fake_trigger(monkeypatch):
    class Trigger:
        class Request:
            pass

    std_srvs_mod = types.ModuleType("std_srvs")
    srv_mod = types.ModuleType("std_srvs.srv")
    srv_mod.Trigger = Trigger
    std_srvs_mod.srv = srv_mod
    monkeypatch.setitem(sys.modules, "std_srvs", std_srvs_mod)
    monkeypatch.setitem(sys.modules, "std_srvs.srv", srv_mod)
    return Trigger


@pytest.fixture
def fake_std_msgs(monkeypatch):
    std_mod = types.ModuleType("std_msgs")
    msg_mod = types.ModuleType("std_msgs.msg")
    msg_mod.String = _FakeString
    std_mod.msg = msg_mod
    monkeypatch.setitem(sys.modules, "std_msgs", std_mod)
    monkeypatch.setitem(sys.modules, "std_msgs.msg", msg_mod)


def test_disabled_without_commands_or_topic(fake_trigger):
    errors = []
    control = RobotControl({}, on_error=errors.append, topic="")
    assert not control.enabled
    control.start(_FakeNode())
    assert control.execute("stand_up") == "ERR unavailable"


def test_disabled_when_std_srvs_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "std_srvs", None)
    control = RobotControl(COMMANDS, topic="")
    control.start(_FakeNode())
    assert control.execute("stand_up") == "ERR unavailable"


def test_unknown_command_is_rejected(fake_trigger):
    control = RobotControl(COMMANDS, topic="")
    control.start(_FakeNode())
    assert control.execute("fly") == "ERR command"


def test_unavailable_service_is_rejected(fake_trigger):
    control = RobotControl(COMMANDS, topic="")
    control.start(_FakeNode(service_available=False))
    assert control.execute("stand_up") == "ERR unavailable stand_up"


def test_successful_call_stays_silent(fake_trigger):
    errors = []
    node = _FakeNode()
    control = RobotControl(COMMANDS, on_error=errors.append, topic="")
    control.start(node)

    assert control.execute("stand_up") is None

    future = node.clients["/base_bridge/stand_up"].futures[0]
    future.complete(_FakeResponse(success=True))
    assert errors == []
    control.stop()


def test_failed_trigger_is_reported(fake_trigger):
    errors = []
    node = _FakeNode()
    control = RobotControl(COMMANDS, on_error=errors.append, topic="")
    control.start(node)

    control.execute("lie_down")
    future = node.clients["/base_bridge/lie_down"].futures[0]
    future.complete(_FakeResponse(success=False, message="low battery"))

    assert errors == ["ERR failed lie_down low battery"]
    control.stop()


def test_call_exception_is_reported(fake_trigger):
    errors = []
    node = _FakeNode()
    control = RobotControl(COMMANDS, on_error=errors.append, topic="")
    control.start(node)

    control.execute("stand_up")
    future = node.clients["/base_bridge/stand_up"].futures[0]
    future.complete(exc=RuntimeError("service blew up"))

    assert errors == ["ERR failed stand_up service blew up"]
    control.stop()


def test_call_timeout_is_reported_and_late_result_ignored(fake_trigger):
    errors = []
    node = _FakeNode()
    control = RobotControl(
        COMMANDS, call_timeout_secs=0.1, on_error=errors.append, topic=""
    )
    control.start(node)

    control.execute("stand_up")
    future = node.clients["/base_bridge/stand_up"].futures[0]

    deadline = time.monotonic() + 5.0
    while not errors and time.monotonic() < deadline:
        time.sleep(0.01)

    assert errors == ["ERR timeout stand_up"]
    assert future.cancelled

    # A late completion must not produce a second report.
    future.complete(_FakeResponse(success=False, message="late"))
    assert errors == ["ERR timeout stand_up"]
    control.stop()


def test_stop_cancels_pending_calls(fake_trigger):
    errors = []
    node = _FakeNode()
    control = RobotControl(
        COMMANDS, call_timeout_secs=0.1, on_error=errors.append, topic=""
    )
    control.start(node)

    control.execute("stand_up")
    control.stop()

    time.sleep(0.3)
    assert errors == []
    # After stop the actuator is detached again.
    assert control.execute("stand_up") == "ERR unavailable"


def test_topic_commands_are_published(fake_trigger, fake_std_msgs):
    node = _FakeNode()
    control = RobotControl({}, topic="/xiaozhi_topic")
    assert control.enabled
    control.start(node)

    assert len(node.publishers) == 1
    publisher = node.publishers[0]
    assert publisher.topic == "/xiaozhi_topic"

    for name in (
        "go_forward",
        "go_back",
        "turn_left",
        "turn_right",
        "dance",
        "nod",
        "squat",
    ):
        assert control.execute(name) is None
    assert [message.data for message in publisher.published] == [
        "go_forward",
        "go_back",
        "turn_left",
        "turn_right",
        "dance",
        "nod",
        "squat",
    ]

    control.stop()
    assert node.destroyed_publishers == node.publishers
    assert control.execute("go_forward") == "ERR unavailable"


def test_topic_command_unavailable_when_std_msgs_missing(
    fake_trigger, monkeypatch
):
    monkeypatch.setitem(sys.modules, "std_msgs", None)
    monkeypatch.setitem(sys.modules, "std_msgs.msg", None)
    control = RobotControl({}, topic="/xiaozhi_topic")
    control.start(_FakeNode())
    assert control.execute("go_forward") == "ERR unavailable"


def test_trigger_mapping_takes_priority_over_topic(
    fake_trigger, fake_std_msgs
):
    node = _FakeNode()
    control = RobotControl({"squat": "/base_bridge/lie_down"}, topic="/xiaozhi_topic")
    control.start(node)

    assert control.execute("squat") is None
    assert "/base_bridge/lie_down" in node.clients
    assert node.publishers[0].published == []
    control.stop()


def test_topic_commands_set_is_the_protocol_list():
    assert TOPIC_COMMANDS == {
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
