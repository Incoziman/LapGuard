import pytest

from lapguard.core import events as ev
from lapguard.core.config import Config
from lapguard.core.machine import DEV_AUTO_DISARM_S, MachineSettings, StateMachine
from lapguard.core.states import ALLOWED_TRANSITIONS, State

PASS = "s3cret"


def make(clock, **overrides):
    published: list[ev.Event] = []
    settings = MachineSettings(**{"countdown_s": 10, "grace_s": 8, "max_attempts": 2, **overrides})
    m = StateMachine(lambda s: s == PASS, settings, publish=published.append, clock=clock)
    return m, published


def armed(clock, **overrides):
    m, pub = make(clock, **overrides)
    assert m.arm("test")
    clock.advance(m.settings.countdown_s)
    m.tick()
    assert m.state is State.ARMED
    return m, pub


def type_text(m, text, enter=True):
    for ch in text:
        m.handle(ev.KeyPressed(key=ev.Key.CHAR, char=ch))
    if enter:
        m.handle(ev.KeyPressed(key=ev.Key.ENTER))


def wake(m):
    m.handle(ev.KeyPressed(key=ev.Key.OTHER))


def transitions(pub):
    return [(e.old, e.new) for e in pub if isinstance(e, ev.StateChanged)]


# ------------------------------------------------------------------ arming

def test_arm_countdown_then_armed(clock):
    m, pub = make(clock)
    assert m.arm()
    assert m.state is State.ARMING
    clock.advance(9.9)
    m.tick()
    assert m.state is State.ARMING
    assert m.seconds_remaining() == pytest.approx(0.1)
    clock.advance(0.1)
    m.tick()
    assert m.state is State.ARMED
    assert transitions(pub) == [("disarmed", "arming"), ("arming", "armed")]


def test_zero_countdown_arms_immediately(clock):
    m, _ = make(clock, countdown_s=0)
    m.arm()
    assert m.state is State.ARMED


def test_arm_only_from_disarmed(clock):
    m, _ = armed(clock)
    assert not m.arm()
    assert m.state is State.ARMED


def test_cancel_arming(clock):
    m, _ = make(clock)
    m.arm()
    assert m.cancel_arming("tray")
    assert m.state is State.DISARMED
    assert not m.cancel_arming()


def test_sensors_ignored_while_arming_and_disarmed(clock):
    m, _ = make(clock)
    m.handle(ev.LidClosed())
    wake(m)
    assert m.state is State.DISARMED
    m.arm()
    m.handle(ev.LidClosed())
    wake(m)
    assert m.state is State.ARMING


# ------------------------------------------------------------------ grace window

def test_typing_passkey_straight_away_works(clock):
    """The first char opens the grace window AND counts (wake char + rest)."""
    m, pub = armed(clock)
    type_text(m, PASS)
    assert m.state is State.DISARMED
    assert [e for e in pub if isinstance(e, ev.PasskeyResult)][-1].failed_attempts == 0
    assert transitions(pub)[-2:] == [("armed", "triggered"), ("triggered", "disarmed")]


@pytest.mark.parametrize("wake_event", [
    ev.KeyPressed(key=ev.Key.CHAR, char=" "),   # Space to wake
    ev.KeyPressed(key=ev.Key.CHAR, char="x"),   # any printable key
    ev.KeyPressed(key=ev.Key.OTHER),            # Shift, arrows, ...
    ev.KeyPressed(key=ev.Key.ENTER),            # Enter to wake
])
def test_wake_key_then_passkey_works(clock, wake_event):
    m, _ = armed(clock, max_attempts=1)
    m.handle(wake_event)
    assert m.state is State.TRIGGERED
    assert m.trigger_sensor == "keyboard"
    type_text(m, PASS)
    assert m.state is State.DISARMED


def test_wake_char_only_applies_to_first_submission(clock):
    m, _ = armed(clock, max_attempts=3)
    m.handle(ev.KeyPressed(key=ev.Key.CHAR, char=PASS[0]))
    type_text(m, "zzz")                 # wrong either way
    assert m.failed_attempts == 1
    type_text(m, PASS[1:])              # wake char is gone now
    assert m.failed_attempts == 2
    assert m.state is State.TRIGGERED


def test_escape_discards_wake_char(clock):
    m, _ = armed(clock)
    m.handle(ev.KeyPressed(key=ev.Key.CHAR, char=PASS[0]))
    m.handle(ev.KeyPressed(key=ev.Key.ESCAPE))
    type_text(m, PASS[1:])
    assert m.failed_attempts == 1


def test_backspace_on_empty_buffer_removes_wake_char(clock):
    m, pub = armed(clock)
    m.handle(ev.KeyPressed(key=ev.Key.CHAR, char="x"))
    m.handle(ev.KeyPressed(key=ev.Key.BACKSPACE))
    lengths = [e.length for e in pub if isinstance(e, ev.PasskeyBufferChanged)]
    assert lengths == [1, 0]
    m.handle(ev.KeyPressed(key=ev.Key.ENTER))   # nothing typed: free
    assert m.failed_attempts == 0
    type_text(m, PASS)
    assert m.state is State.DISARMED


def test_wake_char_alone_can_be_submitted(clock):
    m, _ = armed(clock, max_attempts=3)
    m.handle(ev.KeyPressed(key=ev.Key.CHAR, char="q"))
    m.handle(ev.KeyPressed(key=ev.Key.ENTER))   # candidate "q" -> counts as an attempt
    assert m.failed_attempts == 1


def test_verifier_called_at_most_twice_per_enter(clock):
    calls = []

    def verifier(s):
        calls.append(s)
        return s == PASS

    m = StateMachine(verifier, MachineSettings(countdown_s=0, max_attempts=5), clock=clock)
    m.arm()
    type_text(m, "wrong")               # wake 'w' + "rong"
    assert sorted(calls) == ["rong", "wrong"]
    calls.clear()
    type_text(m, "again")               # no wake char any more
    assert calls == ["again"]


def test_wake_char_kept_when_keyboard_is_immediate(clock):
    m, _ = armed(clock, immediate_sensors=frozenset({"keyboard"}))
    type_text(m, PASS)
    assert m.state is State.DISARMED


def test_no_wake_char_when_keyboard_sensor_disabled(clock):
    m, _ = armed(clock, disabled_sensors=frozenset({"keyboard"}))
    m.handle(ev.KeyPressed(key=ev.Key.CHAR, char=PASS[0]))
    assert m.state is State.ARMED
    m.handle(ev.MouseActivity())
    type_text(m, PASS[1:])
    assert m.state is State.TRIGGERED and m.failed_attempts == 1


def test_correct_passkey_within_grace_disarms(clock):
    m, pub = armed(clock)
    wake(m)
    clock.advance(7.9)
    m.tick()
    type_text(m, PASS)
    assert m.state is State.DISARMED
    assert any(isinstance(e, ev.PasskeyResult) and e.ok for e in pub)


def test_grace_expiry_triggers_alarm(clock):
    m, pub = armed(clock)
    wake(m)
    clock.advance(8)
    m.tick()
    assert m.state is State.ALARM
    last = [e for e in pub if isinstance(e, ev.StateChanged)][-1]
    assert last.reason == "grace window expired"
    assert last.sensor == "keyboard"


def test_wrong_attempts_up_to_max_trigger_alarm(clock):
    m, _ = armed(clock, max_attempts=2)
    wake(m)
    type_text(m, "nope")
    assert m.state is State.TRIGGERED
    assert m.failed_attempts == 1
    type_text(m, "still-no")
    assert m.state is State.ALARM


def test_single_attempt_mode(clock):
    m, _ = armed(clock, max_attempts=1)
    wake(m)
    type_text(m, "nope")
    assert m.state is State.ALARM


def test_more_touches_do_not_extend_grace(clock):
    m, _ = armed(clock)
    wake(m)
    clock.advance(5)
    wake(m)
    m.handle(ev.AcUnplugged())
    clock.advance(3)
    m.tick()
    assert m.state is State.ALARM


def test_empty_enter_costs_no_attempt(clock):
    m, _ = armed(clock, max_attempts=1)
    wake(m)
    m.handle(ev.KeyPressed(key=ev.Key.ENTER))
    assert m.state is State.TRIGGERED
    assert m.failed_attempts == 0


def test_backspace_and_escape_edit_buffer(clock):
    m, pub = armed(clock)
    wake(m)
    type_text(m, PASS + "zz", enter=False)
    m.handle(ev.KeyPressed(key=ev.Key.BACKSPACE))
    m.handle(ev.KeyPressed(key=ev.Key.BACKSPACE))
    m.handle(ev.KeyPressed(key=ev.Key.ENTER))
    assert m.state is State.DISARMED

    m, _ = armed(clock)
    wake(m)
    type_text(m, "garbage", enter=False)
    m.handle(ev.KeyPressed(key=ev.Key.ESCAPE))
    type_text(m, PASS)
    assert m.state is State.DISARMED


def test_buffer_length_published_and_capped(clock):
    m, pub = armed(clock, max_buffer=5)
    wake(m)
    type_text(m, "abcdefgh", enter=False)
    lengths = [e.length for e in pub if isinstance(e, ev.PasskeyBufferChanged)]
    assert lengths[-1] == 5


def test_other_keys_ignored_in_buffer(clock):
    m, _ = armed(clock)
    wake(m)
    m.handle(ev.KeyPressed(key=ev.Key.CHAR, char=PASS[:2]))
    m.handle(ev.KeyPressed(key=ev.Key.OTHER))  # e.g. Shift, arrows
    type_text(m, PASS[2:])
    assert m.state is State.DISARMED


# ------------------------------------------------------------------ immediate sensors

@pytest.mark.parametrize("event", [ev.LidClosed(), ev.ShutdownAttempt(), ev.DeviceChanged(), ev.SessionEvent()])
def test_immediate_sensors_go_straight_to_alarm(clock, event):
    m, pub = armed(clock)
    m.handle(event)
    assert m.state is State.ALARM
    assert transitions(pub)[-1] == ("armed", "alarm")
    assert m.trigger_sensor == event.sensor


def test_immediate_sensor_during_grace_escalates(clock):
    m, _ = armed(clock)
    wake(m)
    m.handle(ev.LidClosed())
    assert m.state is State.ALARM
    assert m.trigger_sensor == "lid"


@pytest.mark.parametrize("event", [ev.AcUnplugged(), ev.CameraTriggered(), ev.MouseActivity()])
def test_non_immediate_sensors_open_grace(clock, event):
    m, _ = armed(clock)
    m.handle(event)
    assert m.state is State.TRIGGERED
    assert m.seconds_remaining() == pytest.approx(8)


def test_immediate_set_is_configurable(clock):
    m, _ = armed(clock, immediate_sensors=frozenset({"power"}))
    m.handle(ev.AcUnplugged())
    assert m.state is State.ALARM


def test_disabled_sensor_never_triggers_but_keys_still_reach_buffer(clock):
    m, _ = armed(clock, disabled_sensors=frozenset({"keyboard", "lid"}))
    wake(m)
    m.handle(ev.LidClosed())
    assert m.state is State.ARMED
    m.handle(ev.MouseActivity())
    assert m.state is State.TRIGGERED
    type_text(m, PASS)
    assert m.state is State.DISARMED


# ------------------------------------------------------------------ alarm

def test_correct_passkey_in_alarm_disarms(clock):
    m, _ = armed(clock)
    m.handle(ev.LidClosed())
    type_text(m, "wrong")
    type_text(m, "wrong again")
    type_text(m, "wrong thrice")
    assert m.state is State.ALARM
    type_text(m, PASS)
    assert m.state is State.DISARMED


def test_alarm_does_not_time_out(clock):
    m, _ = armed(clock)
    m.handle(ev.LidClosed())
    clock.advance(10_000)
    m.tick()
    assert m.state is State.ALARM


def test_rearm_resets_attempts(clock):
    m, _ = armed(clock, max_attempts=2)
    wake(m)
    type_text(m, "wrong")
    type_text(m, PASS)
    assert m.state is State.DISARMED
    m.arm()
    clock.advance(10)
    m.tick()
    wake(m)
    type_text(m, "wrong")
    assert m.state is State.TRIGGERED  # first wrong of the new session


def test_verifier_exception_counts_as_wrong(clock):
    def boom(_):
        raise RuntimeError("bad hash")

    m = StateMachine(boom, MachineSettings(countdown_s=0, max_attempts=1), clock=clock)
    m.arm()
    wake(m)
    type_text(m, "anything")
    assert m.state is State.ALARM


# ------------------------------------------------------------------ forced / dev

def test_force_disarm_from_any_active_state(clock):
    for setup in ("arming", "armed", "triggered", "alarm"):
        m, _ = make(clock)
        m.arm()
        if setup != "arming":
            clock.advance(10)
            m.tick()
        if setup == "triggered":
            wake(m)
        if setup == "alarm":
            m.handle(ev.LidClosed())
        assert m.state.value == setup
        assert m.force_disarm("test")
        assert m.state is State.DISARMED
    assert not m.force_disarm("already disarmed")


def test_force_disarm_event(clock):
    m, _ = armed(clock)
    m.handle(ev.ForceDisarm(reason="emergency file"))
    assert m.state is State.DISARMED


def test_dev_auto_disarm_from_alarm(clock):
    m, pub = armed(clock, dev_auto_disarm_s=DEV_AUTO_DISARM_S)
    m.handle(ev.LidClosed())
    clock.advance(DEV_AUTO_DISARM_S - 10 - 0.1)  # 10 s already spent in countdown
    m.tick()
    assert m.state is State.ALARM
    clock.advance(0.1)
    m.tick()
    assert m.state is State.DISARMED
    assert "dev mode auto-disarm" in [e for e in pub if isinstance(e, ev.StateChanged)][-1].reason


def test_no_auto_disarm_without_dev(clock):
    m, _ = armed(clock)
    m.handle(ev.LidClosed())
    clock.advance(3600)
    m.tick()
    assert m.state is State.ALARM


def test_from_config_maps_sensor_flags():
    cfg = Config()
    cfg.sensors.power.immediate = True
    cfg.sensors.camera.enabled = False
    s = MachineSettings.from_config(cfg, dev=True)
    assert "power" in s.immediate_sensors and "lid" in s.immediate_sensors
    assert "keyboard" not in s.immediate_sensors
    assert s.disabled_sensors == frozenset({"camera"})
    assert s.dev_auto_disarm_s == DEV_AUTO_DISARM_S
    assert MachineSettings.from_config(cfg).dev_auto_disarm_s is None


def test_every_published_transition_is_legal(clock):
    m, pub = armed(clock)
    wake(m)
    type_text(m, "x1")
    type_text(m, "x2")
    type_text(m, PASS)
    for old, new in transitions(pub):
        assert State(new) in ALLOWED_TRANSITIONS[State(old)]


# ------------------------------------------------------------------ event hygiene

def test_keypress_description_never_contains_the_character():
    text = ev.KeyPressed(key=ev.Key.CHAR, char="Q").describe()
    assert "Q" not in text and "char=" not in text


def test_buffer_length_events_are_not_logged():
    assert ev.PasskeyBufferChanged.loggable is False
    assert ev.StateChanged.loggable is True
