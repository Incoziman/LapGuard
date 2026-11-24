"""Journaled HKCU registry changes (policy keys, RunOnce restore entry). No admin needed."""

from __future__ import annotations

import logging
import sys
import winreg
from pathlib import Path

from ...core.journal import Journal

log = logging.getLogger(__name__)

JOURNAL_KIND = "registry"
POLICIES_SYSTEM = r"Software\Microsoft\Windows\CurrentVersion\Policies\System"
RUNONCE = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
# "!" prefix: Windows deletes the RunOnce value only after the command succeeds.
RUNONCE_NAME = "!LapGuardRestore"


def snapshot(path: str, name: str) -> dict:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            value, vtype = winreg.QueryValueEx(k, name)
    except FileNotFoundError:
        return {"path": path, "name": name, "exists": False}
    if isinstance(value, bytes):
        value = {"hex": value.hex()}
    return {"path": path, "name": name, "exists": True, "type": vtype, "value": value}


def set_value(path: str, name: str, vtype: int, value: object) -> None:
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, vtype, value)


def delete_value(path: str, name: str) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    except FileNotFoundError:
        pass


def journaled_set(journal: Journal, path: str, name: str, vtype: int, value: object) -> None:
    key = f"HKCU\\{path}\\{name}"
    journal.record(JOURNAL_KIND, key, snapshot(path, name))
    try:
        set_value(path, name, vtype, value)
        log.info("registry: set %s = %r", key, value)
    except OSError:
        journal.discard(JOURNAL_KIND, key)
        raise


def restore(key: str, original: dict) -> None:
    path, name = original["path"], original["name"]
    if not original["exists"]:
        delete_value(path, name)
        return
    value = original["value"]
    if isinstance(value, dict) and "hex" in value:
        value = bytes.fromhex(value["hex"])
    set_value(path, name, int(original["type"]), value)


def restore_command() -> str:
    """Command line that runs ``lapguard --restore`` without a console window when possible."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --restore'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return f'"{pythonw if pythonw.exists() else exe}" -m lapguard --restore'
