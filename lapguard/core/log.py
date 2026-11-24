"""Logging setup: rotating file in the state dir, ISO-8601 timestamps with UTC offset."""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_NAME = "lapguard.log"


class _IsoFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        return datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="milliseconds")


def setup_logging(log_dir: Path, level: str = "INFO", *, console: bool = False) -> Path:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / LOG_NAME
    logger = logging.getLogger("lapguard")
    logger.setLevel(level.upper())
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    fmt = _IsoFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    fh = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    if console:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(fmt)
        logger.addHandler(ch)
    logger.propagate = False
    return path
