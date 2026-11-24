"""Lid / power-button / sleep-button actions via the Power API (powrprof.dll).

Uses PowerRead/WriteAC/DCValueIndex directly rather than parsing ``powercfg``
output, which is localised. Every original value is journaled before it is
written; the restorer writes it back and re-applies the scheme if it is still
the active one (it never switches your active power plan).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import logging
from dataclasses import dataclass

from ...core.journal import Journal
from . import api

log = logging.getLogger(__name__)

JOURNAL_KIND = "powersetting"
SUB_BUTTONS = "4f971e89-eebd-4455-a8de-9e59040e7347"
DO_NOTHING = 0


@dataclass(frozen=True)
class Setting:
    name: str
    guid: str
    subgroup: str = SUB_BUTTONS


LIDACTION = Setting("LIDACTION", "5ca83367-6e45-459f-a27b-476b1d01c936")
PBUTTONACTION = Setting("PBUTTONACTION", "7648efa3-dd9c-4e3e-b566-50f929386280")
SBUTTONACTION = Setting("SBUTTONACTION", "96996bc0-ad50-47ec-923b-6f41874dd9eb")
OVERRIDDEN = (LIDACTION, PBUTTONACTION, SBUTTONACTION)


def active_scheme() -> str:
    ptr = ctypes.POINTER(api.GUID)()
    rc = api.powrprof.PowerGetActiveScheme(None, ctypes.byref(ptr))
    if rc:
        raise api.win_error(rc, "PowerGetActiveScheme")
    try:
        return str(ptr.contents)
    finally:
        api.kernel32.LocalFree(ptr)


def read_value(scheme: str, subgroup: str, setting: str, *, ac: bool) -> int:
    fn = api.powrprof.PowerReadACValueIndex if ac else api.powrprof.PowerReadDCValueIndex
    out = w.DWORD()
    rc = fn(None, ctypes.byref(api.GUID.of(scheme)), ctypes.byref(api.GUID.of(subgroup)),
            ctypes.byref(api.GUID.of(setting)), ctypes.byref(out))
    if rc:
        raise api.win_error(rc, f"PowerRead{'AC' if ac else 'DC'}ValueIndex({setting})")
    return out.value


def write_value(scheme: str, subgroup: str, setting: str, value: int, *, ac: bool) -> None:
    fn = api.powrprof.PowerWriteACValueIndex if ac else api.powrprof.PowerWriteDCValueIndex
    rc = fn(None, ctypes.byref(api.GUID.of(scheme)), ctypes.byref(api.GUID.of(subgroup)),
            ctypes.byref(api.GUID.of(setting)), value)
    if rc:
        raise api.win_error(rc, f"PowerWrite{'AC' if ac else 'DC'}ValueIndex({setting})")


def reapply_if_active(scheme: str) -> None:
    """Changes to the active scheme only take effect after PowerSetActiveScheme."""
    if active_scheme() == scheme:
        rc = api.powrprof.PowerSetActiveScheme(None, ctypes.byref(api.GUID.of(scheme)))
        if rc:
            raise api.win_error(rc, "PowerSetActiveScheme")


def override_power_actions(journal: Journal) -> list[str]:
    """Set lid / power button / sleep button to "Do nothing" (AC and DC). Returns warnings."""
    warnings: list[str] = []
    scheme = active_scheme()
    changed = False
    for s in OVERRIDDEN:
        for ac in (True, False):
            mode = "ac" if ac else "dc"
            try:
                original = read_value(scheme, s.subgroup, s.guid, ac=ac)
            except OSError as e:
                if s is not SBUTTONACTION:  # many laptops have no sleep button setting
                    warnings.append(f"cannot read {s.name} ({mode}): {e}")
                continue
            if original == DO_NOTHING:
                continue
            key = f"{scheme}|{s.name}|{mode}"
            journal.record(JOURNAL_KIND, key, {"scheme": scheme, "subgroup": s.subgroup,
                                               "setting": s.guid, "name": s.name, "ac": ac,
                                               "value": original})
            try:
                write_value(scheme, s.subgroup, s.guid, DO_NOTHING, ac=ac)
                changed = True
                log.info("power: %s (%s) %d -> do nothing", s.name, mode, original)
            except OSError as e:
                journal.discard(JOURNAL_KIND, key)
                warnings.append(f"cannot set {s.name} ({mode}) to 'do nothing': {e}")
    if changed:
        try:
            reapply_if_active(scheme)
        except OSError as e:
            warnings.append(f"power settings written but not applied: {e}")
    return warnings


def restore(key: str, original: dict) -> None:
    write_value(original["scheme"], original["subgroup"], original["setting"], int(original["value"]),
                ac=bool(original["ac"]))
    reapply_if_active(original["scheme"])
