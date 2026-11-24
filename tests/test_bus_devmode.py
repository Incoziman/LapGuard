import logging
import threading
import time

from lapguard.actions.logger import EventLogger
from lapguard.core import events as ev
from lapguard.core.bus import EventBus
from lapguard.core.config import emergency_file
from lapguard.core.devmode import DisarmFileWatcher
from lapguard.core.machine import MachineSettings, StateMachine
from lapguard.core.states import State


def test_isinstance_matching_and_unsubscribe():
    bus = EventBus()
    every, sensors = [], []
    bus.subscribe(ev.Event, every.append)
    unsub = bus.subscribe(ev.SensorEvent, sensors.append)
    bus.publish(ev.LidClosed())
    bus.publish(ev.ArmRequested())
    assert bus.dispatch_pending() == 2
    assert len(every) == 2 and len(sensors) == 1
    unsub()
    bus.publish(ev.LidClosed())
    bus.dispatch_pending()
    assert len(sensors) == 1


def test_failing_handler_does_not_break_others(caplog):
    bus = EventBus()
    got = []

    def bad(_):
        raise ValueError("boom")

    bus.subscribe(ev.Event, bad)
    bus.subscribe(ev.Event, got.append)
    with caplog.at_level(logging.ERROR):
        bus.publish(ev.LidClosed())
        bus.dispatch_pending()
    assert len(got) == 1
    assert "boom" in caplog.text


def test_threaded_dispatch_from_many_publishers():
    bus = EventBus()
    got, done = [], threading.Event()

    def handler(e):
        got.append(e)
        if len(got) == 400:
            done.set()

    bus.subscribe(ev.Event, handler)
    bus.start()
    threads = [threading.Thread(target=lambda: [bus.publish(ev.MouseActivity()) for _ in range(100)])
               for _ in range(4)]
    for t in threads:
        t.start()
    assert done.wait(5)
    bus.stop()


def test_machine_on_bus_end_to_end(caplog):
    bus = EventBus()
    m = StateMachine(lambda s: s == "1234", MachineSettings(countdown_s=0), publish=bus.publish)
    bus.subscribe(ev.Event, m.handle)
    EventLogger(bus)
    with caplog.at_level(logging.INFO, logger="lapguard"):
        bus.publish(ev.ArmRequested(source="test"))
        bus.dispatch_pending()
        assert m.state is State.ARMED
        bus.publish(ev.KeyPressed(key=ev.Key.OTHER))
        for ch in "1234":
            bus.publish(ev.KeyPressed(key=ev.Key.CHAR, char=ch))
        bus.publish(ev.KeyPressed(key=ev.Key.ENTER))
        bus.dispatch_pending()
        bus.dispatch_pending()  # events the machine published while handling
    assert m.state is State.DISARMED
    assert "StateChanged" in caplog.text and "at=" in caplog.text
    assert "'1'" not in caplog.text and "1234" not in caplog.text  # passkey never logged


def test_disarm_file_watcher_fires_once_and_consumes_file():
    calls = []
    path = emergency_file()
    w = DisarmFileWatcher(path, calls.append, interval_s=0.05)
    w.start()
    try:
        path.touch()
        deadline = time.monotonic() + 3
        while not calls and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(0.2)
    finally:
        w.stop()
    assert len(calls) == 1 and "emergency file" in calls[0]
    assert not path.exists()


def test_disarm_file_check_once_without_file():
    w = DisarmFileWatcher(emergency_file(), lambda r: None)
    assert w.check_once() is False
