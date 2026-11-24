"""Restore journal: the safety net for every system setting LapGuard changes.

Protocol for any change (powercfg, registry policy, volume, RunOnce, ...):

    journal.record(kind, key, original_value)   # 1. persist the ORIGINAL first
    apply_the_change()                           # 2. then change the system
    ...
    journal.restore_all()                        # on disarm / clean exit / next startup

The journal file exists only while there is something to restore, so a journal
present at startup means the previous run crashed (or was killed) while armed.

* Recording the same (kind, key) twice keeps the FIRST original - otherwise a
  crash followed by re-arming would "remember" LapGuard's own modified value.
* Entries are restored newest-first and removed one at a time, so a partially
  failed restore can be retried and never re-applies already-restored values.
* A corrupt journal is moved aside (never silently deleted) and reported.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .fsutil import atomic_write_text

log = logging.getLogger(__name__)

Restorer = Callable[[str, Any], None]  # (key, original_value) -> None, raises on failure

FORMAT_VERSION = 1


@dataclass
class JournalEntry:
    kind: str
    key: str
    original: Any
    recorded_at: str


@dataclass
class RestoreReport:
    restored: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (entry, error)
    skipped: list[str] = field(default_factory=list)             # no restorer registered
    load_error: str | None = None

    @property
    def ok(self) -> bool:
        return not self.failed and not self.skipped and self.load_error is None


class Journal:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._restorers: dict[str, Restorer] = {}
        self.load_error: str | None = None
        self._entries: list[JournalEntry] = self._load()

    # ---------------------------------------------------------------- public
    def register(self, kind: str, restorer: Restorer) -> None:
        self._restorers[kind] = restorer

    def record(self, kind: str, key: str, original: Any) -> bool:
        """Persist the original value BEFORE changing it. Returns False if already recorded."""
        json.dumps(original)  # fail now, before the system is touched, if not serialisable
        with self._lock:
            if self._find(kind, key) is not None:
                return False
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            self._entries.append(JournalEntry(kind, key, original, stamp))
            self._save()
            log.info("journal: recorded %s:%s original=%r", kind, key, original)
            return True

    def discard(self, kind: str, key: str) -> None:
        """Drop an entry without restoring (e.g. the change itself failed to apply)."""
        with self._lock:
            entry = self._find(kind, key)
            if entry is not None:
                self._entries.remove(entry)
                self._save()

    def entries(self) -> list[JournalEntry]:
        with self._lock:
            return list(self._entries)

    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._entries)

    def __len__(self) -> int:
        return len(self.entries())

    def restore_all(self) -> RestoreReport:
        report = RestoreReport(load_error=self.load_error)
        with self._lock:
            for entry in reversed(list(self._entries)):
                label = f"{entry.kind}:{entry.key}"
                restorer = self._restorers.get(entry.kind)
                if restorer is None:
                    report.skipped.append(label)
                    log.warning("journal: no restorer for %s, kept for later", label)
                    continue
                try:
                    restorer(entry.key, entry.original)
                except Exception as e:  # keep the entry; try the rest
                    report.failed.append((label, f"{type(e).__name__}: {e}"))
                    log.exception("journal: restoring %s failed", label)
                    continue
                self._entries.remove(entry)
                self._save()
                report.restored.append(label)
                log.info("journal: restored %s -> %r", label, entry.original)
        return report

    # ---------------------------------------------------------------- internals
    def _find(self, kind: str, key: str) -> JournalEntry | None:
        return next((e for e in self._entries if e.kind == kind and e.key == key), None)

    def _save(self) -> None:
        if not self._entries:
            self.path.unlink(missing_ok=True)
            return
        doc = {"version": FORMAT_VERSION, "entries": [asdict(e) for e in self._entries]}
        atomic_write_text(self.path, json.dumps(doc, indent=2), private=True)

    def _load(self) -> list[JournalEntry]:
        if not self.path.exists():
            return []
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
            if doc.get("version") != FORMAT_VERSION or not isinstance(doc.get("entries"), list):
                raise ValueError(f"unsupported journal format: version={doc.get('version')!r}")
            return [JournalEntry(**e) for e in doc["entries"]]
        except (OSError, ValueError, TypeError, AttributeError) as e:
            aside = self.path.with_name(f"journal.corrupt-{time.strftime('%Y%m%d-%H%M%S')}.json")
            try:
                self.path.replace(aside)
            except OSError:
                aside = self.path
            self.load_error = f"journal unreadable ({e}); moved to {aside} - restore those settings manually"
            log.error("journal: %s", self.load_error)
            return []
