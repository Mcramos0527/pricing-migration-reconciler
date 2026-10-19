from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path


class LockBusyError(RuntimeError):
    pass


class RunLock:
    # context manager around an exclusively-created lock file

    def __init__(self, path: Path, stale_hours: float = 6.0) -> None:
        self.path = Path(path)
        self.stale_seconds = stale_hours * 3600

    def _create(self) -> bool:
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(
                f"pid={os.getpid()} started={datetime.now().isoformat(timespec='seconds')}\n"
            )
        return True

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._create():
            return self
        age = time.time() - self.path.stat().st_mtime
        if age > self.stale_seconds:
            self.path.unlink(missing_ok=True)
            if self._create():
                return self
        raise LockBusyError(
            f"Another run is in progress (lock file {self.path}, age {age/60:.0f} min)."
        )

    def __exit__(self, *exc_info: object) -> None:
        self.path.unlink(missing_ok=True)
