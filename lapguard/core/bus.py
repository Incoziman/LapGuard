"""A small thread-safe publish/subscribe event bus.

``publish()`` never blocks and may be called from any thread - this matters
because the Windows low-level keyboard hook callback must return within
milliseconds or Windows silently removes the hook.

Delivery happens either on the bus's own dispatcher thread (``start()``) or
synchronously via ``dispatch_pending()`` (tests, custom loops). Handlers are
matched with ``isinstance``, so subscribing to ``Event`` receives everything.
A failing handler is logged and does not affect other handlers.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

from .events import Event

log = logging.getLogger(__name__)

Handler = Callable[[Event], None]
_STOP = object()


class EventBus:
    def __init__(self) -> None:
        self._subs: list[tuple[type[Event], Handler]] = []
        self._lock = threading.Lock()
        self._queue: queue.SimpleQueue[object] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None

    def subscribe(self, event_type: type[Event], handler: Handler) -> Callable[[], None]:
        entry = (event_type, handler)
        with self._lock:
            self._subs.append(entry)

        def unsubscribe() -> None:
            with self._lock:
                if entry in self._subs:
                    self._subs.remove(entry)

        return unsubscribe

    def publish(self, event: Event) -> None:
        self._queue.put(event)

    def dispatch_pending(self, max_items: int | None = None) -> int:
        """Deliver queued events on the calling thread. Returns the number delivered."""
        n = 0
        while max_items is None or n < max_items:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is _STOP:
                continue
            self._deliver(item)  # type: ignore[arg-type]
            n += 1
        return n

    def start(self, name: str = "lapguard-bus") -> None:
        if self._thread is not None:
            raise RuntimeError("bus already started")
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        if self._thread is None:
            return
        self._queue.put(_STOP)
        self._thread.join(timeout)
        self._thread = None

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            self._deliver(item)  # type: ignore[arg-type]

    def _deliver(self, event: Event) -> None:
        with self._lock:
            subs = list(self._subs)
        for event_type, handler in subs:
            if isinstance(event, event_type):
                try:
                    handler(event)
                except Exception:
                    log.exception("event handler %r failed on %s", handler, type(event).__name__)
