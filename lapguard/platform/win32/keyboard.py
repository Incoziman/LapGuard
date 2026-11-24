"""Global keyboard capture via a low-level hook (WH_KEYBOARD_LL).

While installed, EVERY key-down/up is swallowed (returns 1), so nothing reaches
other apps - including Win, Alt+Tab and media/volume keys. Ctrl+Alt+Del cannot
be intercepted by any user-mode program.

Safety properties:
* The hook callback only translates the key and calls ``on_key`` (which just
  enqueues onto the bus). If the callback ever takes longer than Windows'
  LowLevelHooksTimeout, Windows passes the key through instead - so even a
  hung LapGuard cannot freeze your keyboard.
* ``self_release_s`` (dev mode): the hook thread removes itself on its own
  timer, independent of the rest of the app.
* The hook is re-installed every ``rehook_s`` seconds, because Windows silently
  removes LL hooks that time out repeatedly.
* Killing the process removes the hook immediately.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import logging
import threading
from typing import Callable

from ...core.events import Key
from . import api
from .keytranslate import VK_CAPITAL, VK_NUMLOCK, KeyTranslator

log = logging.getLogger(__name__)

LLKHF_INJECTED = 0x10


def _to_unicode(vk: int, scan: int, keystate: bytes) -> str | None:
    fg = api.user32.GetForegroundWindow()
    layout = api.user32.GetKeyboardLayout(api.user32.GetWindowThreadProcessId(fg, None) if fg else 0)
    buf = ctypes.create_unicode_buffer(8)
    # flag 0x4: do not change kernel keyboard state (keeps dead keys intact), Win10 1607+
    n = api.user32.ToUnicodeEx(vk, scan, keystate, buf, len(buf), 0x4, layout)
    return buf.value[:n] if n > 0 else None


class KeyboardGrabber:
    def __init__(self, on_key: Callable[[Key, str, bool], None], *,
                 self_release_s: float | None = None, rehook_s: float = 30.0) -> None:
        self._on_key = on_key  # (key, char, injected)
        self._self_release_s = self_release_s
        self._rehook_ms = int(rehook_s * 1000)
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._ready = threading.Event()
        self._error: Exception | None = None
        self._proc = api.HOOKPROC(self._callback)  # keep a reference for the hook's lifetime
        self._translator: KeyTranslator | None = None
        self.released_by_timer = False

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.active:
            return
        self._ready.clear()
        self._error = None
        self.released_by_timer = False
        self._thread = threading.Thread(target=self._run, name="lapguard-kbd-hook", daemon=True)
        self._thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("keyboard hook thread did not start")
        if self._error:
            raise self._error

    def stop(self) -> None:
        t = self._thread
        if t is None:
            return
        if t.is_alive() and self._tid:
            api.user32.PostThreadMessageW(self._tid, api.WM_QUIT, 0, 0)
        t.join(timeout=3)
        self._thread = None

    # ------------------------------------------------------------ hook thread
    def _install(self) -> w.HHOOK:
        hook = api.user32.SetWindowsHookExW(api.WH_KEYBOARD_LL, self._proc,
                                            api.kernel32.GetModuleHandleW(None), 0)
        if not hook:
            raise api.win_error(ctypes.get_last_error(), "SetWindowsHookExW")
        return hook

    def _run(self) -> None:
        self._tid = api.kernel32.GetCurrentThreadId()
        self._translator = KeyTranslator(
            _to_unicode,
            capslock_on=bool(api.user32.GetKeyState(VK_CAPITAL) & 1),
            numlock_on=bool(api.user32.GetKeyState(VK_NUMLOCK) & 1),
        )
        hook = None
        release_timer = rehook_timer = 0
        try:
            hook = self._install()
            rehook_timer = api.user32.SetTimer(None, 0, self._rehook_ms, None)
            if self._self_release_s is not None:
                release_timer = api.user32.SetTimer(None, 0, max(1, int(self._self_release_s * 1000)), None)
            log.info("keyboard captured (self-release: %s)",
                     f"{self._self_release_s:.0f}s" if self._self_release_s is not None else "off")
        except Exception as e:
            self._error = e
            self._ready.set()
            return
        self._ready.set()

        msg = w.MSG()
        try:
            while api.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == api.WM_TIMER and release_timer and msg.wParam == release_timer:
                    self.released_by_timer = True
                    log.warning("keyboard hook self-released (dev safety timer)")
                    break
                if msg.message == api.WM_TIMER and msg.wParam == rehook_timer:
                    try:
                        new = self._install()  # install the new one first: no gap
                        api.user32.UnhookWindowsHookEx(hook)
                        hook = new
                    except OSError:
                        log.exception("keyboard re-hook failed; keeping the existing hook")
        finally:
            if hook:
                api.user32.UnhookWindowsHookEx(hook)
            for t in (release_timer, rehook_timer):
                if t:
                    api.user32.KillTimer(None, t)
            log.info("keyboard released")

    def _callback(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code < 0:
            return api.user32.CallNextHookEx(None, n_code, w_param, l_param)
        try:
            kb = api.KBDLLHOOKSTRUCT.from_address(l_param)
            is_down = w_param in (api.WM_KEYDOWN, api.WM_SYSKEYDOWN)
            result = self._translator.feed(kb.vkCode, kb.scanCode, is_down) if self._translator else None
            if result is not None:
                self._on_key(result[0], result[1], bool(kb.flags & LLKHF_INJECTED))
        except Exception:
            pass  # never raise inside a hook; the key is still swallowed
        return 1  # swallow
