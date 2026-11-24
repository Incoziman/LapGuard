"""Runtime selection of the platform adapter."""

from __future__ import annotations

import sys
from typing import Callable

from ..core.config import Config
from ..core.journal import Journal
from .base import Diag, PlatformAdapter, Publish


def get_adapter(publish: Publish, journal: Journal, cfg: Config, *, dev: bool,
                on_session_end: Callable[[], None] | None = None, diag: Diag | None = None) -> PlatformAdapter:
    if sys.platform == "win32":
        from .windows import WindowsAdapter

        return WindowsAdapter(publish, journal, cfg, dev=dev, on_session_end=on_session_end, diag=diag)
    if sys.platform.startswith("linux"):
        raise NotImplementedError("The Linux adapter is not implemented yet (it follows the Windows milestones).")
    raise NotImplementedError(f"unsupported platform: {sys.platform}")


def register_restorers(journal: Journal) -> None:
    """Make journaled changes restorable (used at startup and by --restore)."""
    if sys.platform == "win32":
        from .windows import register_restorers as _register

        _register(journal)
