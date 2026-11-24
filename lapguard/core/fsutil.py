"""Crash-safe file writes."""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
from pathlib import Path


def atomic_write_text(path: Path, text: str, *, private: bool = False) -> None:
    """Write ``text`` to ``path`` so readers see either the old or the new file, never half.

    temp file in the same dir -> flush -> fsync -> os.replace -> fsync dir (POSIX).
    ``private`` makes the file 0600 on POSIX (mkstemp already does; kept explicit).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if private and os.name != "nt":
            os.chmod(tmp, 0o600)
        _replace_with_retry(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    if os.name != "nt":
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


def _replace_with_retry(src: str, dst: Path, attempts: int = 10) -> None:
    # On Windows, antivirus / indexers briefly holding the target cause PermissionError.
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if os.name != "nt" or i == attempts - 1:
                raise
            time.sleep(0.05)
