"""Emergency-disarm file watcher.

``touch ~/.lapguard/DISARM`` (or ``type nul > %USERPROFILE%\\.lapguard\\DISARM``)
force-disarms LapGuard within ~0.5 s - e.g. from an SSH session or a second
machine if a keyboard-hook bug ever locks you out. The file is deleted after
it is acted on so it cannot instantly disarm the next arming.

Runs only while armed (the app starts/stops it), so idle CPU stays at zero.
The 60 s dev auto-disarm lives in the state machine (``dev_auto_disarm_s``);
the hook/grab threads add their own independent self-release in milestone 2.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)


class DisarmFileWatcher:
    def __init__(self, path: Path, on_disarm: Callable[[str], None], interval_s: float = 0.5) -> None:
        self.path = Path(path)
        self._on_disarm = on_disarm
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="lapguard-disarm-file", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def check_once(self) -> bool:
        if not self.path.exists():
            return False
        log.warning("emergency disarm file found: %s", self.path)
        try:
            self.path.unlink()
        except OSError:
            log.exception("could not delete %s (disarming anyway)", self.path)
        self._on_disarm(f"emergency file {self.path}")
        return True

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.check_once()
            except Exception:
                log.exception("disarm-file watcher error")
