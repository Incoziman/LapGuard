"""Engine lifecycle against a fake platform adapter (no OS hooks)."""

import time

import pytest

from lapguard.core import events as ev
from lapguard.core.config import Config, emergency_file
from lapguard.core.journal import Journal
from lapguard.core.passkey import hash_passkey
from lapguard.core.states import State
from lapguard.engine import Engine
from lapguard.platform.base import PlatformAdapter

PASS = "test-pass"


@pytest.fixture(scope="module")
def pass_hash():
    return hash_passkey(PASS)


class FakeAdapter(PlatformAdapter):
    name = "fake"
    instances: list["FakeAdapter"] = []

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.calls: list[str] = []
        self.system = {"lid": 1}
        self.keep_awake = self.keyboard = self.sensors = False
        self.self_release = None
        self.journal.register("fake", lambda key, original: self.system.__setitem__(key, original))
        FakeAdapter.instances.append(self)

    def start(self): self.calls.append("start")
    def stop(self): self.calls.append("stop")
    def set_keep_awake(self, on): self.keep_awake = on

    def apply_protections(self):
        self.journal.record("fake", "lid", self.system["lid"])
        self.system["lid"] = 0
        return ["fake warning"]

    def grab_keyboard(self, self_release_s):
        self.keyboard, self.self_release = True, self_release_s

    def release_keyboard(self): self.keyboard = False
    def set_sensors_active(self, active): self.sensors = active


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def engine_factory(pass_hash, tmp_path):
    engines = []

    def make(dev=True, countdown=0.2, **general):
        cfg = Config()
        cfg.passkey.hash = pass_hash
        cfg.general.countdown_s = countdown
        for k, v in general.items():
            setattr(cfg.general, k, v)
        e = Engine(cfg, dev=dev, adapter_factory=FakeAdapter, journal=Journal(tmp_path / "journal.json"))
        engines.append(e)
        e.start()
        return e, e.adapter

    yield make
    for e in engines:
        e.stop()


def type_keys(engine, text):
    engine.bus.publish(ev.KeyPressed(key=ev.Key.OTHER))
    for ch in text:
        engine.bus.publish(ev.KeyPressed(key=ev.Key.CHAR, char=ch))
    engine.bus.publish(ev.KeyPressed(key=ev.Key.ENTER))


def test_requires_passkey():
    with pytest.raises(RuntimeError, match="set-passkey"):
        Engine(Config(), adapter_factory=FakeAdapter)


def test_full_cycle_applies_and_restores(engine_factory):
    engine, fake = engine_factory()
    notices = []
    engine.bus.subscribe(ev.Notice, notices.append)
    engine.arm("test")
    assert wait_for(lambda: engine.machine.state is State.ARMED)
    assert wait_for(lambda: fake.keyboard and fake.sensors)
    assert fake.keep_awake and fake.system["lid"] == 0
    assert engine.journal.has_pending()
    assert 55 < fake.self_release < 65  # dev backstop ~ remaining dev time + 2 s
    assert wait_for(lambda: any(n.message == "fake warning" for n in notices))

    type_keys(engine, PASS)
    assert wait_for(lambda: engine.machine.state is State.DISARMED)
    assert wait_for(lambda: not fake.keyboard and not fake.sensors and not fake.keep_awake)
    assert fake.system["lid"] == 1
    assert not engine.journal.has_pending()
    assert engine.last_restore.ok


def test_keyboard_not_grabbed_during_countdown(engine_factory):
    engine, fake = engine_factory(countdown=30)
    engine.arm("test")
    assert wait_for(lambda: fake.keep_awake)
    assert not fake.keyboard and not fake.sensors
    engine.bus.publish(ev.CancelArming(source="test"))
    assert wait_for(lambda: not fake.keep_awake)
    assert fake.system["lid"] == 1


def test_stop_while_armed_restores_everything(engine_factory):
    engine, fake = engine_factory()
    engine.arm("test")
    assert wait_for(lambda: fake.keyboard)
    engine.stop()
    assert not fake.keyboard and not fake.sensors and not fake.keep_awake
    assert fake.system["lid"] == 1
    assert "stop" in fake.calls
    engine.stop()  # idempotent


def test_session_end_restores_synchronously(engine_factory):
    engine, fake = engine_factory()
    engine.arm("test")
    assert wait_for(lambda: fake.keyboard)
    fake.on_session_end()
    assert fake.system["lid"] == 1 and not fake.keyboard


def test_emergency_file_disarms(engine_factory):
    engine, fake = engine_factory()
    engine.arm("test")
    assert wait_for(lambda: engine.machine.state is State.ARMED)
    path = emergency_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    assert wait_for(lambda: engine.machine.state is State.DISARMED, timeout=5)
    assert wait_for(lambda: fake.system["lid"] == 1)
    assert not path.exists()


def test_non_dev_has_no_keyboard_self_release(engine_factory):
    engine, fake = engine_factory(dev=False)
    engine.arm("test")
    assert wait_for(lambda: fake.keyboard)
    assert fake.self_release is None
