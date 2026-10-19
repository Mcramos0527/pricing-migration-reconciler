
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import Settings

_MARKER = "_pricing_reconciler_handler"


def configure_logging(settings: Settings, *, console: bool = True) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level, logging.INFO))
    for handler in list(root.handlers):
        if getattr(handler, _MARKER, False):
            root.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(
            settings.log_dir / settings.log_file,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
    ]
    if console:
        handlers.append(logging.StreamHandler())
    for handler in handlers:
        handler.setFormatter(formatter)
        setattr(handler, _MARKER, True)
        root.addHandler(handler)
