"""Translate low-level key events (virtual-key codes) into passkey input.

Pure logic, no Windows imports, so it is unit-tested on any OS.

Because the keyboard hook *swallows* every key, Windows never sees Shift or
CapsLock go down, so ``GetKeyboardState`` would be wrong. This class tracks
modifier and toggle state itself and builds the 256-byte key-state array that
``ToUnicodeEx`` needs. The actual layout lookup is injected (``to_unicode``).
"""

from __future__ import annotations

from typing import Callable

from ...core.events import Key

VK_BACK, VK_RETURN, VK_ESCAPE = 0x08, 0x0D, 0x1B
VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_CAPITAL, VK_NUMLOCK = 0x14, 0x90
VK_LSHIFT, VK_RSHIFT = 0xA0, 0xA1
VK_LCONTROL, VK_RCONTROL = 0xA2, 0xA3
VK_LMENU, VK_RMENU = 0xA4, 0xA5
VK_LWIN, VK_RWIN = 0x5B, 0x5C

_HELD = {VK_SHIFT, VK_LSHIFT, VK_RSHIFT, VK_CONTROL, VK_LCONTROL, VK_RCONTROL,
         VK_MENU, VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN}

ToUnicode = Callable[[int, int, bytes], "str | None"]  # (vk, scan, keystate[256]) -> text


class KeyTranslator:
    def __init__(self, to_unicode: ToUnicode, *, capslock_on: bool = False, numlock_on: bool = True) -> None:
        self._to_unicode = to_unicode
        self._down: set[int] = set()
        self.capslock = capslock_on
        self.numlock = numlock_on

    def feed(self, vk: int, scan: int, is_down: bool) -> tuple[Key, str] | None:
        """Returns (Key, char) for key-down events, None for key-up."""
        if not is_down:
            self._down.discard(vk)
            return None
        if vk in _HELD:
            self._down.add(vk)
            return (Key.OTHER, "")
        if vk == VK_CAPITAL:
            self.capslock = not self.capslock
            return (Key.OTHER, "")
        if vk == VK_NUMLOCK:
            self.numlock = not self.numlock
            return (Key.OTHER, "")
        if vk == VK_RETURN:
            return (Key.ENTER, "")
        if vk == VK_BACK:
            return (Key.BACKSPACE, "")
        if vk == VK_ESCAPE:
            return (Key.ESCAPE, "")

        altgr = VK_RMENU in self._down
        ctrl = bool(self._down & {VK_CONTROL, VK_LCONTROL, VK_RCONTROL})
        alt = bool(self._down & {VK_MENU, VK_LMENU})
        win = bool(self._down & {VK_LWIN, VK_RWIN})
        if win or ((ctrl or alt) and not altgr):
            return (Key.OTHER, "")  # shortcut chord, not text

        text = self._to_unicode(vk, scan, self.keystate(altgr=altgr))
        if text and all(32 <= ord(c) != 127 for c in text):
            return (Key.CHAR, text)
        return (Key.OTHER, "")

    def keystate(self, *, altgr: bool = False) -> bytes:
        state = bytearray(256)
        for vk in self._down & {VK_LSHIFT, VK_RSHIFT, VK_SHIFT}:
            state[vk] = 0x80
        if state[VK_LSHIFT] or state[VK_RSHIFT]:
            state[VK_SHIFT] = 0x80
        if altgr:
            for vk in (VK_CONTROL, VK_LCONTROL, VK_MENU, VK_RMENU):
                state[vk] = 0x80
        if self.capslock:
            state[VK_CAPITAL] = 0x01
        if self.numlock:
            state[VK_NUMLOCK] = 0x01
        return bytes(state)
