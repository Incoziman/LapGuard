"""Windows 11 platform adapter.

Keyboard   WH_KEYBOARD_LL hook, swallows all keys while armed      (win32/keyboard.py)
Power      GUID_ACDC_POWER_SOURCE notifications                     (win32/msgwindow.py)
Lid        GUID_LIDSWITCH_STATE_CHANGE notifications
Shutdown   WM_QUERYENDSESSION veto + ShutdownBlockReasonCreate; suspend/resume
Session    WTS session lock (Win+L)
Devices    WM_DEVICECHANGE (all interface classes) + audio endpoint watcher
Protect    lid / power / sleep button -> "do nothing" (powrprof), optional HKCU
           policies, HKCU RunOnce "--restore" - all journaled first.
Awake      SetThreadExecutionState while arming/armed.
"""

from __future__ import annotations

import logging
import time
import winreg
from datetime import datetime

from ..core import events as ev
from ..core.journal import Journal
from ..sensors.device_logic import DeviceFilter, classify, physical_key
from .base import PlatformAdapter
from .win32 import api, powersettings, registry
from .win32.audio import AudioWatcher
from .win32.keyboard import KeyboardGrabber
from .win32.msgwindow import DeviceNotice, MessageWindow

log = logging.getLogger(__name__)

_WTS_EVENTS = {1: "console connect", 2: "console disconnect", 3: "remote connect", 4: "remote disconnect",
               5: "logon", 6: "logoff", 7: "lock", 8: "unlock", 9: "remote control"}


def register_restorers(journal: Journal) -> None:
    journal.register(powersettings.JOURNAL_KIND, powersettings.restore)
    journal.register(registry.JOURNAL_KIND, registry.restore)


class WindowsAdapter(PlatformAdapter):
    name = "windows"

    def __init__(self, *args, clock=time.monotonic, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._clock = clock
        self._window = MessageWindow(self)
        self._keyboard: KeyboardGrabber | None = None
        self._audio = AudioWatcher(self._on_audio, diag=self._diag if self.diag else None)
        d = self.cfg.sensors.devices
        self._devices = DeviceFilter(on_arrival=d.on_arrival, on_removal=d.on_removal, ignore=tuple(d.ignore))
        self._active = False
        self.lid_open: bool | None = None
        self.on_ac: bool | None = None

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._window.start()
        if self.diag:
            self._audio.start()  # diag shows audio events without arming

    def stop(self) -> None:
        self.set_sensors_active(False)
        self.release_keyboard()
        self._audio.stop()
        self._window.stop()

    def status(self) -> dict[str, str]:
        unknown = "unknown"
        return {
            "lid": unknown if self.lid_open is None else ("open" if self.lid_open else "closed"),
            "power": unknown if self.on_ac is None else ("AC" if self.on_ac else "battery"),
        }

    # ------------------------------------------------------------ protections
    def set_keep_awake(self, on: bool) -> None:
        self._window.set_keep_awake(on)

    def apply_protections(self) -> list[str]:
        warnings: list[str] = []
        if self.cfg.sensors.lid.override_power_actions:
            try:
                warnings += powersettings.override_power_actions(self.journal)
            except OSError as e:
                warnings.append(f"power actions not changed: {e}")
        win = self.cfg.windows
        policies = [("DisableTaskMgr", win.disable_task_manager), ("DisableLockWorkstation", win.disable_lock_workstation)]
        for name, wanted in policies:
            if not wanted:
                continue
            if self.dev:
                warnings.append(f"dev mode: {name} policy not applied (safety)")
                continue
            try:
                registry.journaled_set(self.journal, registry.POLICIES_SYSTEM, name, winreg.REG_DWORD, 1)
            except OSError as e:
                warnings.append(f"{name} policy not applied: {e}")
        if win.runonce_restore:
            try:
                registry.journaled_set(self.journal, registry.RUNONCE, registry.RUNONCE_NAME,
                                       winreg.REG_SZ, registry.restore_command())
            except OSError as e:
                warnings.append(f"RunOnce crash-restore entry not created: {e}")
        return warnings

    # ------------------------------------------------------------ sensors
    def grab_keyboard(self, self_release_s: float | None) -> None:
        if self._keyboard and self._keyboard.active:
            return
        self._keyboard = KeyboardGrabber(self._on_key, self_release_s=self_release_s)
        self._keyboard.start()

    def release_keyboard(self) -> None:
        if self._keyboard:
            self._keyboard.stop()
            self._keyboard = None

    def set_sensors_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        s = self.cfg.sensors
        if active:
            self._devices.arm(self._clock())
            self._audio.start()
            self._window.set_shutdown_block(s.shutdown.enabled and s.shutdown.block)
        else:
            self._devices.disarm()
            if not self.diag:
                self._audio.stop()
            self._window.set_shutdown_block(False)

    # ------------------------------------------------------------ callbacks (other threads)
    def _diag(self, text: str) -> None:
        if self.diag:
            self.diag(f"{datetime.now():%H:%M:%S.%f}"[:-3] + f"  {text}")

    def _on_key(self, key: ev.Key, char: str, injected: bool) -> None:
        self.publish(ev.KeyPressed(key=key, char=char, detail="injected" if injected else ""))

    def on_query_end_session(self, *, logoff: bool, critical: bool) -> bool:
        kind = "logoff" if logoff else "shutdown"
        self._diag(f"end-session query: {kind}{' (critical)' if critical else ''}")
        if not self._active:
            return True
        block = self.cfg.sensors.shutdown.block and not critical
        self.publish(ev.ShutdownAttempt(kind=kind, blocked=block,
                                        detail="critical - cannot be blocked" if critical else ""))
        return not block

    def on_end_session(self, *, ending: bool) -> None:
        self._diag(f"end-session: {'session IS ending' if ending else 'cancelled'}")
        if ending and self.on_session_end:
            self.on_session_end()  # restore journal synchronously before Windows kills us

    def on_lid(self, *, is_open: bool, initial: bool) -> None:
        self.lid_open = is_open
        self._diag(f"lid {'open' if is_open else 'CLOSED'}{' (initial state)' if initial else ''}")
        if self._active and not initial and not is_open:
            self.publish(ev.LidClosed())

    def on_power_source(self, *, on_ac: bool, initial: bool) -> None:
        was_ac, self.on_ac = self.on_ac, on_ac
        self._diag(f"power source: {'AC' if on_ac else 'BATTERY'}{' (initial state)' if initial else ''}")
        if self._active and not initial and was_ac and not on_ac:
            self.publish(ev.AcUnplugged())

    def on_suspend(self) -> None:
        self._diag("system is suspending")
        if self._active:
            self.publish(ev.ShutdownAttempt(kind="sleep", blocked=False, detail="cannot be vetoed"))

    def on_resume(self, *, how: str) -> None:
        self._diag(f"system resumed ({how})")
        if self._active:
            self.publish(ev.SessionEvent(kind="resumed", detail=how))

    def on_session_change(self, *, event: int) -> None:
        self._diag(f"session: {_WTS_EVENTS.get(event, event)}")
        if self._active and event == api.WTS_SESSION_LOCK:
            self.publish(ev.SessionEvent(kind="locked"))

    def on_device(self, notice: DeviceNotice) -> None:
        cls = classify(notice.class_guid) if notice.kind == "interface" else "volume"
        self._diag(f"device {notice.action}: [{cls}] {notice.name}")
        if self._active and self._devices.accept(notice.action, notice.name, self._clock()):
            self.publish(ev.DeviceChanged(action=notice.action, device_id=notice.name,
                                          name=physical_key(notice.name), device_class=cls))

    def _on_audio(self, action: str, device_id: str, detail: str) -> None:
        self._diag(f"{action}: {device_id} ({detail})")
        if self._active and self._devices.accept(action, device_id, self._clock()):
            self.publish(ev.DeviceChanged(action=action, device_id=device_id, name=detail, device_class="audio"))
