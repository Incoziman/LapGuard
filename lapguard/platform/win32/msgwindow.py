"""A hidden top-level window that receives the system notifications LapGuard needs.

It must be a real top-level window (not a message-only HWND_MESSAGE window):
WM_QUERYENDSESSION, WM_POWERBROADCAST and ShutdownBlockReasonCreate only work
with top-level windows. It is never shown.

Notifications handled:
  WM_QUERYENDSESSION / WM_ENDSESSION     shutdown, restart, logoff
  WM_POWERBROADCAST                      suspend / resume, plus power-setting changes:
      GUID_LIDSWITCH_STATE_CHANGE        lid open/closed (fires even with lid action "do nothing")
      GUID_ACDC_POWER_SOURCE             AC / battery
  WM_WTSSESSION_CHANGE                   session lock (Win+L)
  WM_DEVICECHANGE                        any device interface arrival / removal

Cross-thread requests (keep-awake, shutdown block reason) must run on this
window's thread, so they are posted as WM_APP messages.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import logging
import threading
from dataclasses import dataclass
from typing import Callable, Protocol

from . import api

log = logging.getLogger(__name__)

_WM_SET_KEEP_AWAKE = api.WM_APP + 1
_WM_SET_SHUTDOWN_BLOCK = api.WM_APP + 2
_CLASS_NAME = "LapGuardHiddenWindow"


@dataclass(frozen=True)
class DeviceNotice:
    action: str        # "added" | "removed"
    kind: str          # "interface" | "volume"
    name: str          # device interface path, e.g. \\?\USB#VID_046D&PID_C52B#...#{guid}
    class_guid: str


class WindowHandler(Protocol):
    def on_query_end_session(self, *, logoff: bool, critical: bool) -> bool: ...  # True = allow
    def on_end_session(self, *, ending: bool) -> None: ...
    def on_lid(self, *, is_open: bool, initial: bool) -> None: ...
    def on_power_source(self, *, on_ac: bool, initial: bool) -> None: ...
    def on_suspend(self) -> None: ...
    def on_resume(self, *, how: str) -> None: ...
    def on_session_change(self, *, event: int) -> None: ...
    def on_device(self, notice: DeviceNotice) -> None: ...


class MessageWindow:
    def __init__(self, handler: WindowHandler) -> None:
        self._handler = handler
        self._proc = api.WNDPROC(self._wndproc)
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: Exception | None = None
        self.hwnd: int = 0
        self._handles: list[int] = []
        self._dev_notify = 0
        self._seen_lid = False
        self._seen_power = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="lapguard-msgwindow", daemon=True)
        self._thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("message window thread did not start")
        if self._error:
            raise self._error

    def stop(self) -> None:
        if self._thread is None:
            return
        if self.hwnd:
            api.user32.PostMessageW(self.hwnd, api.WM_CLOSE, 0, 0)
        self._thread.join(timeout=3)
        self._thread = None

    def set_keep_awake(self, on: bool) -> None:
        if self.hwnd:
            api.user32.PostMessageW(self.hwnd, _WM_SET_KEEP_AWAKE, int(on), 0)

    def set_shutdown_block(self, on: bool) -> None:
        if self.hwnd:
            api.user32.PostMessageW(self.hwnd, _WM_SET_SHUTDOWN_BLOCK, int(on), 0)

    # ------------------------------------------------------------ window thread
    def _run(self) -> None:
        hinst = api.kernel32.GetModuleHandleW(None)
        try:
            wc = api.WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(wc)
            wc.lpfnWndProc = self._proc
            wc.hInstance = hinst
            wc.lpszClassName = _CLASS_NAME
            if not api.user32.RegisterClassExW(ctypes.byref(wc)):
                err = ctypes.get_last_error()
                if err != 1410:  # ERROR_CLASS_ALREADY_EXISTS (e.g. restarted in-process)
                    raise api.win_error(err, "RegisterClassExW")
            self.hwnd = api.user32.CreateWindowExW(api.WS_EX_TOOLWINDOW, _CLASS_NAME, "LapGuard", 0,
                                                   0, 0, 0, 0, None, None, hinst, None)
            if not self.hwnd:
                raise api.win_error(ctypes.get_last_error(), "CreateWindowExW")
            # Ask to be notified of shutdown before most other apps (0x3FF = highest app level).
            api.kernel32.SetProcessShutdownParameters(0x3FF, 0)
            self._register_notifications()
        except Exception as e:
            self._error = e
            self._ready.set()
            return
        self._ready.set()

        msg = w.MSG()
        while api.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            api.user32.TranslateMessage(ctypes.byref(msg))
            api.user32.DispatchMessageW(ctypes.byref(msg))
        self._unregister_notifications()
        api.user32.UnregisterClassW(_CLASS_NAME, hinst)
        self.hwnd = 0

    def _register_notifications(self) -> None:
        for guid in (api.GUID_LIDSWITCH_STATE_CHANGE, api.GUID_ACDC_POWER_SOURCE):
            h = api.user32.RegisterPowerSettingNotification(self.hwnd, ctypes.byref(api.GUID.of(guid)), 0)
            if h:
                self._handles.append(h)
            else:
                log.warning("RegisterPowerSettingNotification(%s) failed: %s", guid, ctypes.get_last_error())
        if not api.wtsapi32.WTSRegisterSessionNotification(self.hwnd, api.NOTIFY_FOR_THIS_SESSION):
            log.warning("WTSRegisterSessionNotification failed: %s", ctypes.get_last_error())
        filt = api.DEV_BROADCAST_DEVICEINTERFACE_W()
        filt.dbcc_size = ctypes.sizeof(filt)
        filt.dbcc_devicetype = api.DBT_DEVTYP_DEVICEINTERFACE
        self._dev_notify = api.user32.RegisterDeviceNotificationW(
            self.hwnd, ctypes.byref(filt),
            api.DEVICE_NOTIFY_WINDOW_HANDLE | api.DEVICE_NOTIFY_ALL_INTERFACE_CLASSES)
        if not self._dev_notify:
            log.warning("RegisterDeviceNotificationW failed: %s", ctypes.get_last_error())

    def _unregister_notifications(self) -> None:
        for h in self._handles:
            api.user32.UnregisterPowerSettingNotification(h)
        self._handles.clear()
        if self._dev_notify:
            api.user32.UnregisterDeviceNotification(self._dev_notify)
            self._dev_notify = 0
        api.wtsapi32.WTSUnRegisterSessionNotification(self.hwnd)

    def _wndproc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        try:
            result = self._dispatch(hwnd, msg, wparam, lparam)
            if result is not None:
                return result
        except Exception:
            log.exception("message window handler failed (msg=0x%04x)", msg)
            if msg == api.WM_QUERYENDSESSION:
                return 1  # never block shutdown because of our own bug
        return api.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _dispatch(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int | None:
        h = self._handler
        if msg == api.WM_QUERYENDSESSION:
            allow = h.on_query_end_session(logoff=bool(lparam & api.ENDSESSION_LOGOFF),
                                           critical=bool(lparam & api.ENDSESSION_CRITICAL))
            return 1 if allow else 0
        if msg == api.WM_ENDSESSION:
            h.on_end_session(ending=bool(wparam))
            return 0
        if msg == api.WM_POWERBROADCAST:
            if wparam == api.PBT_POWERSETTINGCHANGE and lparam:
                self._power_setting(api.POWERBROADCAST_SETTING.from_address(lparam))
            elif wparam == api.PBT_APMSUSPEND:
                h.on_suspend()
            elif wparam == api.PBT_APMRESUMEAUTOMATIC:
                h.on_resume(how="automatic")
            elif wparam == api.PBT_APMRESUMESUSPEND:
                h.on_resume(how="user")
            return 1
        if msg == api.WM_WTSSESSION_CHANGE:
            h.on_session_change(event=wparam)
            return 0
        if msg == api.WM_DEVICECHANGE and wparam in (api.DBT_DEVICEARRIVAL, api.DBT_DEVICEREMOVECOMPLETE) and lparam:
            notice = self._device_notice(wparam, lparam)
            if notice:
                h.on_device(notice)
            return 1
        if msg == _WM_SET_KEEP_AWAKE:
            flags = api.ES_CONTINUOUS | (api.ES_SYSTEM_REQUIRED | api.ES_DISPLAY_REQUIRED if wparam else 0)
            api.kernel32.SetThreadExecutionState(flags)
            log.info("keep-awake %s", "on" if wparam else "off")
            return 0
        if msg == _WM_SET_SHUTDOWN_BLOCK:
            if wparam:
                api.user32.ShutdownBlockReasonCreate(hwnd, "LapGuard alarm is armed")
            else:
                api.user32.ShutdownBlockReasonDestroy(hwnd)
            return 0
        if msg == api.WM_CLOSE:
            api.user32.DestroyWindow(hwnd)
            return 0
        if msg == api.WM_DESTROY:
            api.user32.PostQuitMessage(0)
            return 0
        return None

    def _power_setting(self, s: api.POWERBROADCAST_SETTING) -> None:
        guid = str(s.PowerSetting)
        value = int.from_bytes(bytes(s.Data)[: min(4, s.DataLength)], "little")
        if guid == api.GUID_LIDSWITCH_STATE_CHANGE:
            initial, self._seen_lid = not self._seen_lid, True
            self._handler.on_lid(is_open=value != 0, initial=initial)
        elif guid == api.GUID_ACDC_POWER_SOURCE:
            initial, self._seen_power = not self._seen_power, True
            self._handler.on_power_source(on_ac=value == 0, initial=initial)  # 0=AC 1=DC 2=UPS

    @staticmethod
    def _device_notice(wparam: int, lparam: int) -> DeviceNotice | None:
        action = "added" if wparam == api.DBT_DEVICEARRIVAL else "removed"
        hdr = api.DEV_BROADCAST_HDR.from_address(lparam)
        if hdr.dbch_devicetype == api.DBT_DEVTYP_DEVICEINTERFACE:
            di = api.DEV_BROADCAST_DEVICEINTERFACE_W.from_address(lparam)
            name_offset = api.DEV_BROADCAST_DEVICEINTERFACE_W.dbcc_name.offset
            name = ctypes.wstring_at(lparam + name_offset)
            return DeviceNotice(action, "interface", name, str(di.dbcc_classguid))
        if hdr.dbch_devicetype == api.DBT_DEVTYP_VOLUME:
            vol = api.DEV_BROADCAST_VOLUME.from_address(lparam)
            drives = "".join(chr(65 + i) + ":" for i in range(26) if vol.dbcv_unitmask & (1 << i))
            return DeviceNotice(action, "volume", drives, "")
        return None
