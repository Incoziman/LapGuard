"""The interface every platform adapter implements.

The adapter owns all OS-specific threads and system changes. It publishes
typed sensor events onto the bus (only while sensors are active) and records
every system change in the restore journal *before* making it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from ..core.config import Config
from ..core.events import Event
from ..core.journal import Journal, RestoreReport

Publish = Callable[[Event], None]
Diag = Callable[[str], None]


class PlatformAdapter(ABC):
    name = "base"

    def __init__(self, publish: Publish, journal: Journal, cfg: Config, *, dev: bool,
                 on_session_end: Callable[[], None] | None = None, diag: Diag | None = None) -> None:
        self.publish = publish
        self.journal = journal
        self.cfg = cfg
        self.dev = dev
        self.on_session_end = on_session_end
        self.diag = diag

    # lifecycle: start() runs idle background listeners (near-zero CPU)
    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    # protections (ARMING -> DISARMED)
    @abstractmethod
    def set_keep_awake(self, on: bool) -> None: ...

    @abstractmethod
    def apply_protections(self) -> list[str]:
        """Journal + apply system changes (power actions, policies...). Returns warnings."""

    def remove_protections(self) -> RestoreReport:
        return self.journal.restore_all()

    # sensors (ARMED -> DISARMED)
    @abstractmethod
    def grab_keyboard(self, self_release_s: float | None) -> None: ...

    @abstractmethod
    def release_keyboard(self) -> None: ...

    @abstractmethod
    def set_sensors_active(self, active: bool) -> None: ...

    def status(self) -> dict[str, str]:
        """Human-readable state for diagnostics (lid, AC, ...)."""
        return {}
