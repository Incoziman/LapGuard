"""Alarm states and the set of legal transitions between them."""

from __future__ import annotations

from enum import Enum


class State(str, Enum):
    DISARMED = "disarmed"
    ARMING = "arming"        # countdown running, sensors not yet active
    ARMED = "armed"          # sensors active, waiting for a touch
    TRIGGERED = "triggered"  # grace window open, passkey prompt shown
    ALARM = "alarm"          # siren + owner lock screen until correct passkey


ALLOWED_TRANSITIONS: dict[State, frozenset[State]] = {
    State.DISARMED: frozenset({State.ARMING}),
    State.ARMING: frozenset({State.ARMED, State.DISARMED}),
    # ARMED -> DISARMED only via force_disarm (dev timeout / emergency file / remote).
    State.ARMED: frozenset({State.TRIGGERED, State.ALARM, State.DISARMED}),
    State.TRIGGERED: frozenset({State.ALARM, State.DISARMED}),
    State.ALARM: frozenset({State.DISARMED}),
}


def is_allowed(old: State, new: State) -> bool:
    return new in ALLOWED_TRANSITIONS[old]
