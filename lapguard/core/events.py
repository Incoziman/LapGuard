"""Typed events carried on the event bus.

Sensors emit ``SensorEvent`` subclasses. The state machine consumes them plus
the control events, and emits ``StateChanged`` / ``PasskeyResult``.
Actions (siren, lock screen, logger, ntfy) subscribe to whatever they need.

Every event knows how to ``describe()`` itself for the log. Events that could
leak passkey material override that (``KeyPressed``) or opt out of logging
entirely (``loggable = False``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import ClassVar


@dataclass(frozen=True, kw_only=True)
class Event:
    ts: float = field(default_factory=time.time)
    loggable: ClassVar[bool] = True

    def describe(self) -> str:
        parts = [f"{f.name}={getattr(self, f.name)!r}" for f in fields(self) if f.name != "ts"]
        return f"{type(self).__name__}({', '.join(parts)})"


# --------------------------------------------------------------------------- sensors

@dataclass(frozen=True, kw_only=True)
class SensorEvent(Event):
    """Something touched the laptop. ``sensor`` matches the [sensors.<name>] config key."""

    sensor: ClassVar[str] = ""
    detail: str = ""

    def describe(self) -> str:
        return f"[{self.sensor}] {super().describe()}"


class Key(str, Enum):
    CHAR = "char"
    ENTER = "enter"
    BACKSPACE = "backspace"
    ESCAPE = "escape"
    OTHER = "other"


@dataclass(frozen=True, kw_only=True)
class KeyPressed(SensorEvent):
    sensor: ClassVar[str] = "keyboard"
    key: Key
    char: str = ""

    def describe(self) -> str:
        # Never log the character itself: it may be part of the passkey.
        return f"[keyboard] KeyPressed(key={self.key.value})"


@dataclass(frozen=True, kw_only=True)
class MouseActivity(SensorEvent):
    sensor: ClassVar[str] = "mouse"
    kind: str = "click"  # "click" | "move"


@dataclass(frozen=True, kw_only=True)
class AcUnplugged(SensorEvent):
    sensor: ClassVar[str] = "power"


@dataclass(frozen=True, kw_only=True)
class ShutdownAttempt(SensorEvent):
    sensor: ClassVar[str] = "shutdown"
    kind: str = "shutdown"  # "shutdown" | "restart" | "logoff" | "sleep" | "power_button"
    blocked: bool = False


@dataclass(frozen=True, kw_only=True)
class LidClosed(SensorEvent):
    sensor: ClassVar[str] = "lid"


@dataclass(frozen=True, kw_only=True)
class CameraTriggered(SensorEvent):
    sensor: ClassVar[str] = "camera"
    reason: str = "motion"  # "motion" | "covered"
    score: float = 0.0


@dataclass(frozen=True, kw_only=True)
class DeviceChanged(SensorEvent):
    sensor: ClassVar[str] = "devices"
    action: str = "added"  # "added" | "removed"
    device_id: str = ""
    name: str = ""
    device_class: str = ""


@dataclass(frozen=True, kw_only=True)
class SessionEvent(SensorEvent):
    sensor: ClassVar[str] = "session"
    kind: str = "locked"  # "locked" | "resumed"


# --------------------------------------------------------------------------- control

@dataclass(frozen=True, kw_only=True)
class ArmRequested(Event):
    source: str = "unknown"  # "hotkey" | "tray" | "cli" | "remote"


@dataclass(frozen=True, kw_only=True)
class CancelArming(Event):
    source: str = "unknown"


@dataclass(frozen=True, kw_only=True)
class ForceDisarm(Event):
    """Disarm without a passkey. Only for dev timeout, emergency file, authenticated remote."""

    reason: str


# --------------------------------------------------------------------------- outputs

@dataclass(frozen=True, kw_only=True)
class StateChanged(Event):
    old: str
    new: str
    reason: str = ""
    sensor: str = ""  # sensor that caused TRIGGERED / ALARM, if any


@dataclass(frozen=True, kw_only=True)
class PasskeyResult(Event):
    ok: bool
    failed_attempts: int


@dataclass(frozen=True, kw_only=True)
class Notice(Event):
    """Something the user should know (protection could not be applied, hook released...)."""

    level: str = "info"  # "info" | "warning" | "error"
    message: str = ""


@dataclass(frozen=True, kw_only=True)
class PasskeyBufferChanged(Event):
    """For the lock screen's dot display. Not logged: length is passkey material."""

    length: int
    loggable: ClassVar[bool] = False
