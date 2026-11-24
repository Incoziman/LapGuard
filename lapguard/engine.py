"""The LapGuard engine: wires config, bus, state machine, platform adapter and journal.

What turns on and off with each state:

  ARMING    keep-awake on, protections applied (lid/power buttons -> do nothing,
            policies, RunOnce crash-restore), emergency DISARM-file watcher on
  ARMED     keyboard captured (dev: hook self-releases on its own timer),
            sensors active (lid, AC, shutdown veto, session lock, devices)
  DISARMED  everything above undone; the journal restores every system setting

UI-free so it can run headless (console harness now, tray app in milestone 5).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .actions.logger import EventLogger
from .core import config as cfgmod
from .core.bus import EventBus
from .core.config import Config
from .core.devmode import DisarmFileWatcher
from .core.events import ArmRequested, Event, ForceDisarm, Notice, StateChanged
from .core.journal import Journal, RestoreReport
from .core.machine import MachineSettings, StateMachine
from .core.passkey import make_verifier
from .core.states import State
from .platform import get_adapter, register_restorers
from .platform.base import PlatformAdapter

log = logging.getLogger(__name__)

AdapterFactory = Callable[..., PlatformAdapter]
TICK_S = 0.1


class Engine:
    def __init__(self, cfg: Config, *, dev: bool = False, adapter_factory: AdapterFactory = get_adapter,
                 journal: Journal | None = None) -> None:
        if not cfg.has_passkey:
            raise RuntimeError("no passkey configured - run `lapguard --set-passkey` first")
        self.cfg = cfg
        self.dev = dev
        self.bus = EventBus()
        self.journal = journal or Journal(cfgmod.journal_path())
        register_restorers(self.journal)
        self.machine = StateMachine(make_verifier(cfg.passkey.hash), MachineSettings.from_config(cfg, dev=dev),
                                    publish=self.bus.publish)
        self.adapter = adapter_factory(self.bus.publish, self.journal, cfg, dev=dev,
                                       on_session_end=self._on_session_end)
        self.watcher = DisarmFileWatcher(cfgmod.emergency_file(),
                                         lambda reason: self.bus.publish(ForceDisarm(reason=reason)))
        self.last_restore: RestoreReport | None = None
        self._lock = threading.RLock()
        self._protected = False
        self._sensing = False
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._ticker = threading.Thread(target=self._tick_loop, name="lapguard-ticker", daemon=True)
        self._started = self._stopped = False

        self.bus.subscribe(Event, self.machine.handle)  # machine first: it decides the state
        self.bus.subscribe(StateChanged, self._on_state)
        if cfg.actions.log.enabled:
            EventLogger(self.bus)

    # ------------------------------------------------------------ public
    def start(self) -> None:
        self.bus.start()
        self.adapter.start()
        self._ticker.start()
        self._started = True

    def arm(self, source: str = "unknown") -> None:
        self.bus.publish(ArmRequested(source=source))

    def stop(self) -> None:
        """Idempotent. Always leaves the system restored."""
        if self._stopped:
            return
        self._stopped = True
        if self.machine.state is not State.DISARMED:
            self.machine.force_disarm("application exit")
        self._deactivate()
        self._stopping.set()
        self._wake.set()
        if self._started:
            self.adapter.stop()
            self.bus.stop()
            self._ticker.join(timeout=2)

    # ------------------------------------------------------------ state reactions (bus thread)
    def _on_state(self, ev: StateChanged) -> None:
        new = State(ev.new)
        if new is State.ARMING:
            self._wake.set()
            self._activate_protections()
        elif new is State.ARMED:
            self._activate_sensors()
        elif new is State.DISARMED:
            self._deactivate()

    def _notice(self, level: str, message: str) -> None:
        getattr(log, "error" if level == "error" else "warning" if level == "warning" else "info")(message)
        self.bus.publish(Notice(level=level, message=message))

    def _activate_protections(self) -> None:
        with self._lock:
            if self._protected:
                return
            self._protected = True
            self.adapter.set_keep_awake(True)
            for w in self.adapter.apply_protections():
                self._notice("warning", w)
            if self.dev or self.cfg.general.emergency_file:
                self.watcher.start()

    def _activate_sensors(self) -> None:
        with self._lock:
            if self._sensing or self.machine.state is State.DISARMED:
                return
            self._sensing = True
            self_release = None
            if self.dev:
                remaining = self.machine.dev_seconds_remaining() or 0.0
                self_release = remaining + 2.0  # independent backstop to the machine's auto-disarm
            try:
                self.adapter.grab_keyboard(self_release)
            except Exception as e:
                self._notice("error", f"keyboard capture failed: {e}")
            self.adapter.set_sensors_active(True)

    def _deactivate(self) -> None:
        with self._lock:
            if self._sensing:
                self._sensing = False
                self.adapter.set_sensors_active(False)
                self.adapter.release_keyboard()
            if self._protected:
                self._protected = False
                self.watcher.stop()
                report = self.adapter.remove_protections()
                self.last_restore = report
                if not report.ok:
                    self._notice("error", f"some settings were not restored: {report} - run `lapguard --restore`")
                self.adapter.set_keep_awake(False)

    def _on_session_end(self) -> None:
        log.warning("Windows session is ending while LapGuard is active - restoring settings now")
        self._deactivate()

    # ------------------------------------------------------------ ticker
    def _tick_loop(self) -> None:
        while not self._stopping.is_set():
            if self.machine.state is State.DISARMED:
                self._wake.wait()  # idle: no CPU at all until armed again
                self._wake.clear()
                continue
            self.machine.tick()
            time.sleep(TICK_S)
