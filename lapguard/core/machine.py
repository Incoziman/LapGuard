"""The alarm state machine.

    DISARMED -> ARMING (countdown) -> ARMED -> TRIGGERED (grace window) -> ALARM
         ^__________________ correct passkey / force_disarm ______________|

Pure logic: no threads, no I/O, no timers. Time comes from an injected clock
and deadlines are checked in ``tick()`` (the app calls it ~10x/s while not
disarmed), which makes every timing rule unit-testable with a fake clock.

Passkey entry
-------------
While ARMED, the first keypress opens the grace window. If it was a printable
character it is remembered as a "wake char", and the first submission accepts
EITHER the buffer as typed OR wake char + buffer. So both habits work:
typing the passkey straight away, or pressing a key (e.g. Space) to wake the
prompt and then typing it - neither wastes one of the limited attempts.
The wake char applies to that first submission only; Esc clears it, and
Backspace on an empty buffer removes it. In TRIGGERED / ALARM, characters go
to the buffer, Backspace edits, Esc clears, Enter submits. An empty Enter
(no buffer, no wake char) is ignored and costs no attempt.

* wrong passkey in TRIGGERED: counts an attempt; reaching ``max_attempts`` -> ALARM
* grace window expires                    -> ALARM
* correct passkey in TRIGGERED or ALARM   -> DISARMED
* sensors flagged ``immediate``           -> ALARM directly (from ARMED or TRIGGERED)
* other sensors                           -> TRIGGERED (deadline is not extended by more touches)
* sensors in ``disabled_sensors`` never trigger; keys still reach the passkey buffer.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .config import Config
from .events import (
    ArmRequested,
    CancelArming,
    Event,
    ForceDisarm,
    Key,
    KeyPressed,
    PasskeyBufferChanged,
    PasskeyResult,
    SensorEvent,
    StateChanged,
)
from .states import State, is_allowed

log = logging.getLogger(__name__)

Verifier = Callable[[str], bool]
Publisher = Callable[[Event], None]
Clock = Callable[[], float]

DEV_AUTO_DISARM_S = 60.0


@dataclass(frozen=True)
class MachineSettings:
    countdown_s: float = 10.0
    grace_s: float = 8.0
    max_attempts: int = 2
    immediate_sensors: frozenset[str] = frozenset({"shutdown", "lid", "devices", "session"})
    disabled_sensors: frozenset[str] = frozenset()
    dev_auto_disarm_s: float | None = None
    max_buffer: int = 128

    @classmethod
    def from_config(cls, cfg: Config, *, dev: bool = False) -> "MachineSettings":
        return cls(
            countdown_s=cfg.general.countdown_s,
            grace_s=cfg.general.grace_s,
            max_attempts=cfg.general.max_attempts,
            immediate_sensors=cfg.immediate_sensors(),
            disabled_sensors=cfg.disabled_sensors(),
            dev_auto_disarm_s=DEV_AUTO_DISARM_S if dev else None,
        )


class StateMachine:
    def __init__(
        self,
        verifier: Verifier,
        settings: MachineSettings | None = None,
        *,
        publish: Publisher | None = None,
        clock: Clock = time.monotonic,
    ) -> None:
        self._verifier = verifier
        self.settings = settings or MachineSettings()
        self._publish = publish or (lambda _e: None)
        self._clock = clock
        self._lock = threading.RLock()
        self._state = State.DISARMED
        self._deadline: float | None = None      # end of countdown or grace window
        self._dev_deadline: float | None = None
        self._attempts = 0
        self._buffer: list[str] = []
        self._wake_char = ""
        self._trigger_sensor = ""

    # ------------------------------------------------------------ inspection
    @property
    def state(self) -> State:
        return self._state

    @property
    def failed_attempts(self) -> int:
        return self._attempts

    @property
    def trigger_sensor(self) -> str:
        return self._trigger_sensor

    def seconds_remaining(self) -> float | None:
        """Countdown (ARMING) or grace window (TRIGGERED) time left."""
        with self._lock:
            if self._deadline is None:
                return None
            return max(0.0, self._deadline - self._clock())

    def dev_seconds_remaining(self) -> float | None:
        with self._lock:
            if self._dev_deadline is None:
                return None
            return max(0.0, self._dev_deadline - self._clock())

    def set_verifier(self, verifier: Verifier) -> None:
        with self._lock:
            self._verifier = verifier

    # ------------------------------------------------------------ commands
    def arm(self, source: str = "unknown") -> bool:
        with self._lock:
            if self._state is not State.DISARMED:
                return False
            now = self._clock()
            self._reset()
            self._deadline = now + self.settings.countdown_s
            if self.settings.dev_auto_disarm_s is not None:
                self._dev_deadline = now + self.settings.dev_auto_disarm_s
            self._set(State.ARMING, f"arm requested ({source})")
            if self.settings.countdown_s <= 0:
                self.tick()
            return True

    def cancel_arming(self, source: str = "unknown") -> bool:
        with self._lock:
            if self._state is not State.ARMING:
                return False
            self._disarm(f"arming cancelled ({source})")
            return True

    def force_disarm(self, reason: str) -> bool:
        with self._lock:
            if self._state is State.DISARMED:
                return False
            self._disarm(f"forced: {reason}")
            return True

    def tick(self) -> None:
        with self._lock:
            if self._state is State.DISARMED:
                return
            now = self._clock()
            if self._dev_deadline is not None and now >= self._dev_deadline:
                self._disarm("forced: dev mode auto-disarm")
                return
            if self._deadline is None or now < self._deadline:
                return
            if self._state is State.ARMING:
                self._deadline = None
                self._set(State.ARMED, "countdown finished")
            elif self._state is State.TRIGGERED:
                self._alarm("grace window expired")

    def handle(self, event: Event) -> None:
        """Entry point for bus events."""
        with self._lock:
            match event:
                case ArmRequested(source=source):
                    self.arm(source)
                case CancelArming(source=source):
                    self.cancel_arming(source)
                case ForceDisarm(reason=reason):
                    self.force_disarm(reason)
                case KeyPressed():
                    self._on_key(event)
                case SensorEvent():
                    self._on_sensor(event)

    # ------------------------------------------------------------ internals
    def _on_key(self, ev: KeyPressed) -> None:
        if self._state is State.ARMED:
            self._on_sensor(ev)  # wake key: opens the grace window (or alarm if immediate)
            if self._state is not State.ARMED and ev.key is Key.CHAR and ev.char:
                self._wake_char = ev.char[: self.settings.max_buffer]
                self._publish(PasskeyBufferChanged(length=self._typed_length()))
        elif self._state in (State.TRIGGERED, State.ALARM):
            self._passkey_input(ev)

    def _on_sensor(self, ev: SensorEvent) -> None:
        if ev.sensor in self.settings.disabled_sensors:
            return
        immediate = ev.sensor in self.settings.immediate_sensors
        if self._state is State.ARMED:
            self._trigger_sensor = ev.sensor
            if immediate:
                self._alarm(f"{ev.sensor} (immediate)")
            else:
                self._deadline = self._clock() + self.settings.grace_s
                self._set(State.TRIGGERED, f"{ev.sensor} touched")
        elif self._state is State.TRIGGERED and immediate:
            self._trigger_sensor = ev.sensor
            self._alarm(f"{ev.sensor} during grace window")

    def _passkey_input(self, ev: KeyPressed) -> None:
        if ev.key is Key.CHAR:
            room = self.settings.max_buffer - len(self._buffer)
            if ev.char and room > 0:
                self._buffer.extend(ev.char[:room])
                self._publish(PasskeyBufferChanged(length=self._typed_length()))
        elif ev.key is Key.BACKSPACE:
            if self._buffer:
                self._buffer.pop()
            elif self._wake_char:
                self._wake_char = ""
            else:
                return
            self._publish(PasskeyBufferChanged(length=self._typed_length()))
        elif ev.key is Key.ESCAPE:
            self._clear_buffer()
        elif ev.key is Key.ENTER and (self._buffer or self._wake_char):
            typed = "".join(self._buffer)
            candidates = [c for c in (typed, self._wake_char + typed) if c]
            if len(candidates) == 2 and candidates[0] == candidates[1]:
                candidates.pop()
            self._clear_buffer()
            ok = any(self._verify(c) for c in candidates)
            del typed, candidates
            if ok:
                self._publish(PasskeyResult(ok=True, failed_attempts=self._attempts))
                self._disarm("correct passkey")
                return
            self._attempts += 1
            self._publish(PasskeyResult(ok=False, failed_attempts=self._attempts))
            if self._state is State.TRIGGERED and self._attempts >= self.settings.max_attempts:
                self._alarm(f"{self._attempts} wrong passkey attempt(s)")

    def _verify(self, candidate: str) -> bool:
        try:
            return bool(self._verifier(candidate))
        except Exception:
            log.exception("passkey verifier failed; treating as wrong passkey")
            return False

    def _typed_length(self) -> int:
        return len(self._wake_char) + len(self._buffer)

    def _clear_buffer(self) -> None:
        had = bool(self._buffer or self._wake_char)
        self._buffer = []
        self._wake_char = ""
        if had:
            self._publish(PasskeyBufferChanged(length=0))

    def _alarm(self, reason: str) -> None:
        self._deadline = None
        self._set(State.ALARM, reason)

    def _disarm(self, reason: str) -> None:
        self._set(State.DISARMED, reason)
        self._reset()

    def _reset(self) -> None:
        self._deadline = None
        self._dev_deadline = None
        self._attempts = 0
        self._buffer = []
        self._wake_char = ""
        self._trigger_sensor = ""

    def _set(self, new: State, reason: str) -> None:
        old = self._state
        if not is_allowed(old, new):
            raise RuntimeError(f"illegal transition {old.value} -> {new.value} ({reason})")
        self._state = new
        sensor = self._trigger_sensor if new in (State.TRIGGERED, State.ALARM) else ""
        log.info("state %s -> %s: %s", old.value, new.value, reason)
        self._publish(StateChanged(old=old.value, new=new.value, reason=reason, sensor=sensor))
