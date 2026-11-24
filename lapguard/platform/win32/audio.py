"""Audio endpoint watcher (IMMNotificationClient via pycaw/comtypes).

Catches headphones / Bluetooth audio appearing and the default output device
changing - things that never show up as a USB event. Callbacks arrive on COM
worker threads; ``on_change`` must be thread-safe (bus.publish is).
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger(__name__)

E_RENDER = 0
STATE_ACTIVE = 1

OnChange = Callable[[str, str, str], None]  # (action, device_id, detail)


def _make_client(on_change: OnChange, diag: Callable[[str], None] | None):
    from pycaw.callbacks import MMNotificationClient

    class Client(MMNotificationClient):
        def on_default_device_changed(self, flow, flow_id, role, role_id, default_device_id):
            if flow_id == E_RENDER and role_id == 0:  # eConsole; eMultimedia duplicates it
                on_change("default_audio_changed", default_device_id or "", "default output changed")

        def on_device_added(self, added_device_id):
            on_change("audio_added", added_device_id, "audio endpoint added")

        def on_device_removed(self, removed_device_id):
            on_change("audio_removed", removed_device_id, "audio endpoint removed")

        def on_device_state_changed(self, device_id, new_state, new_state_id):
            action = "audio_connected" if new_state_id == STATE_ACTIVE else "audio_disconnected"
            on_change(action, device_id, f"state={new_state}")

        def on_property_value_changed(self, device_id, property_struct, fmtid, pid):
            # Very chatty; only shown in --diag so we can learn what a jack plug looks like.
            if diag:
                diag(f"audio property changed: {device_id} {fmtid} pid={pid}")

    return Client()


class AudioWatcher:
    def __init__(self, on_change: OnChange, diag: Callable[[str], None] | None = None) -> None:
        self._on_change = on_change
        self._diag = diag
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="lapguard-audio", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=3)
        self._thread = None

    def _run(self) -> None:
        try:
            import comtypes
            from pycaw.utils import AudioUtilities

            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except Exception as e:  # pycaw missing or COM unavailable: degrade, never crash
            self.error = f"audio watcher unavailable: {e}"
            log.warning(self.error)
            self._ready.set()
            return
        try:
            enumerator = AudioUtilities.GetDeviceEnumerator()
            client = _make_client(self._on_change, self._diag)
            enumerator.RegisterEndpointNotificationCallback(client)
            self._ready.set()
            self._stop.wait()
            enumerator.UnregisterEndpointNotificationCallback(client)
        except Exception as e:
            self.error = f"audio watcher failed: {e}"
            log.exception("audio watcher failed")
            self._ready.set()
        finally:
            comtypes.CoUninitialize()
