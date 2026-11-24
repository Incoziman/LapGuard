"""KeyTranslator is pure: a fake US layout stands in for ToUnicodeEx."""

from lapguard.core.events import Key
from lapguard.platform.win32.keytranslate import (
    VK_BACK, VK_CAPITAL, VK_ESCAPE, VK_LCONTROL, VK_LMENU, VK_LSHIFT, VK_LWIN, VK_NUMLOCK,
    VK_RETURN, VK_RMENU, VK_RSHIFT, VK_SHIFT, KeyTranslator,
)

VK_A, VK_1, VK_NUMPAD7, VK_F5, VK_SPACE = 0x41, 0x31, 0x67, 0x74, 0x20
VK_Q = 0x51


def fake_us_layout(vk: int, scan: int, state: bytes) -> str | None:
    shift = bool(state[VK_SHIFT] & 0x80)
    caps = bool(state[VK_CAPITAL] & 0x01)
    altgr = bool(state[VK_RMENU] & 0x80)
    if altgr:
        return "@" if vk == VK_Q else None  # like a German layout's AltGr+Q
    if 0x41 <= vk <= 0x5A:
        ch = chr(vk).lower()
        return ch.upper() if shift != caps else ch
    if vk == VK_1:
        return "!" if shift else "1"
    if vk == VK_SPACE:
        return " "
    if vk == VK_NUMPAD7:
        return "7" if state[VK_NUMLOCK] & 0x01 else None
    return None


def press(t, vk):
    down = t.feed(vk, 0, True)
    t.feed(vk, 0, False)
    return down


def test_plain_chars_and_specials():
    t = KeyTranslator(fake_us_layout)
    assert press(t, VK_A) == (Key.CHAR, "a")
    assert press(t, VK_1) == (Key.CHAR, "1")
    assert press(t, VK_SPACE) == (Key.CHAR, " ")
    assert press(t, VK_RETURN) == (Key.ENTER, "")
    assert press(t, VK_BACK) == (Key.BACKSPACE, "")
    assert press(t, VK_ESCAPE) == (Key.ESCAPE, "")
    assert press(t, VK_F5) == (Key.OTHER, "")


def test_key_up_returns_none():
    t = KeyTranslator(fake_us_layout)
    assert t.feed(VK_A, 0, False) is None


def test_shift_is_tracked_even_though_keys_are_swallowed():
    t = KeyTranslator(fake_us_layout)
    assert t.feed(VK_LSHIFT, 0, True) == (Key.OTHER, "")
    assert press(t, VK_A) == (Key.CHAR, "A")
    assert press(t, VK_1) == (Key.CHAR, "!")
    t.feed(VK_LSHIFT, 0, False)
    assert press(t, VK_A) == (Key.CHAR, "a")


def test_right_shift_and_shift_release_order():
    t = KeyTranslator(fake_us_layout)
    t.feed(VK_LSHIFT, 0, True)
    t.feed(VK_RSHIFT, 0, True)
    t.feed(VK_LSHIFT, 0, False)
    assert press(t, VK_A) == (Key.CHAR, "A")  # right shift still held


def test_capslock_toggles_from_initial_state():
    t = KeyTranslator(fake_us_layout, capslock_on=True)
    assert press(t, VK_A) == (Key.CHAR, "A")
    assert press(t, VK_CAPITAL) == (Key.OTHER, "")
    assert press(t, VK_A) == (Key.CHAR, "a")
    t.feed(VK_LSHIFT, 0, True)
    press(t, VK_CAPITAL)
    assert press(t, VK_A) == (Key.CHAR, "a")  # caps + shift cancel out


def test_numlock_numpad():
    t = KeyTranslator(fake_us_layout, numlock_on=True)
    assert press(t, VK_NUMPAD7) == (Key.CHAR, "7")
    press(t, VK_NUMLOCK)
    assert press(t, VK_NUMPAD7) == (Key.OTHER, "")


def test_ctrl_alt_win_chords_are_not_text():
    for mod in (VK_LCONTROL, VK_LMENU, VK_LWIN):
        t = KeyTranslator(fake_us_layout)
        t.feed(mod, 0, True)
        assert press(t, VK_A) == (Key.OTHER, "")


def test_altgr_produces_text():
    t = KeyTranslator(fake_us_layout)
    t.feed(VK_LCONTROL, 0, True)  # Windows sends a fake LCtrl with AltGr
    t.feed(VK_RMENU, 0, True)
    assert press(t, VK_Q) == (Key.CHAR, "@")


def test_control_characters_never_become_text():
    t = KeyTranslator(lambda vk, scan, st: "\x7f")
    assert press(t, VK_A) == (Key.OTHER, "")
    t = KeyTranslator(lambda vk, scan, st: "\t")
    assert press(t, VK_A) == (Key.OTHER, "")
