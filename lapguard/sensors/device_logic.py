"""Decide which device plug/unplug notifications count as a touch.

Pure logic (unit-tested). One physical plug produces a burst of notifications
with unrelated-looking IDs (USB device, HID collections, disk, volume, drive
letter...), so after one accepted notification everything else within
``burst_window_s`` is suppressed. The first notification of a burst is
normally the USB device itself (with VID/PID), which is what gets reported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

_VID_PID = re.compile(r"VID_([0-9A-F]{4}).*?PID_([0-9A-F]{4})", re.IGNORECASE)
_IFACE_GUID = re.compile(r"#\{[0-9a-f-]{36}\}$", re.IGNORECASE)

# Interface class GUIDs -> human-readable class (for logs and phone alerts).
INTERFACE_CLASSES = {
    "a5dcbf10-6530-11d2-901f-00c04fb951ed": "usb",
    "4d1e55b2-f16f-11cf-88cb-001111000030": "hid",
    "884b96c3-56ef-11d1-bc8c-00a0c91405dd": "keyboard",
    "378de44c-56ef-11d1-bc8c-00a0c91405dd": "mouse",
    "53f56307-b6bf-11d0-94f2-00a0c91efb8b": "disk",
    "53f5630d-b6bf-11d0-94f2-00a0c91efb8b": "volume",
    "6ac27878-a6fa-4155-ba85-f98f491d4f33": "portable-device",
    "e6f07b5f-ee97-4a90-b076-33f57bf4eaa7": "monitor",
    "1ca05180-a699-450a-9a0c-de4fbe3ddd89": "display-adapter",
    "6994ad04-93ef-11d0-a3cc-00a0c9223196": "audio",
    "e6327cad-dcec-4949-ae8a-991e976a79d2": "audio-render",
    "65e8773d-8f56-11d0-a3b9-00a0c9223196": "audio-capture",
    "e5323777-f976-4f5b-9b55-b94699c46e44": "camera",
    "0850302a-b344-4fda-9be9-90576b8d46f0": "bluetooth",
    "cac88484-7515-4c03-82e6-71a87abac361": "network",
    "86e0d1e0-8089-11d0-9ce4-08003e301f73": "serial-port",
}

ARRIVAL_ACTIONS = {"added", "audio_added", "audio_connected", "default_audio_changed"}


def classify(class_guid: str) -> str:
    return INTERFACE_CLASSES.get(class_guid.lower().strip("{}"), "other")


def physical_key(device_id: str) -> str:
    """Collapse the many interfaces of one physical device onto one key."""
    m = _VID_PID.search(device_id)
    if m:
        return f"usb:{m.group(1).upper()}:{m.group(2).upper()}"
    return _IFACE_GUID.sub("", device_id.removeprefix("\\\\?\\")).upper()


@dataclass
class DeviceFilter:
    on_arrival: bool = True
    on_removal: bool = True
    ignore: Sequence[str] = ()
    burst_window_s: float = 2.0
    settle_s: float = 1.0
    _armed_at: float | None = None
    _last_accepted: float | None = None

    def arm(self, now: float) -> None:
        self._armed_at = now
        self._last_accepted = None

    def disarm(self) -> None:
        self._armed_at = None
        self._last_accepted = None

    def accept(self, action: str, device_id: str, now: float) -> bool:
        if self._armed_at is None or now - self._armed_at < self.settle_s:
            return False
        arrival = action in ARRIVAL_ACTIONS
        if (arrival and not self.on_arrival) or (not arrival and not self.on_removal):
            return False
        lowered = device_id.lower()
        if any(s and s.lower() in lowered for s in self.ignore):
            return False
        if self._last_accepted is not None and now - self._last_accepted < self.burst_window_s:
            return False
        self._last_accepted = now
        return True
