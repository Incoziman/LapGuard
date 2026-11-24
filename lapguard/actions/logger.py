"""Logs every loggable bus event with its own timestamp."""

from __future__ import annotations

import logging
from datetime import datetime

from ..core.bus import EventBus
from ..core.events import Event

log = logging.getLogger("lapguard.events")


class EventLogger:
    def __init__(self, bus: EventBus) -> None:
        self._unsubscribe = bus.subscribe(Event, self._on_event)

    def close(self) -> None:
        self._unsubscribe()

    @staticmethod
    def _on_event(ev: Event) -> None:
        if not ev.loggable:
            return
        at = datetime.fromtimestamp(ev.ts).astimezone().isoformat(timespec="milliseconds")
        log.info("%s at=%s", ev.describe(), at)
