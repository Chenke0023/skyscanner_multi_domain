"""Attempt trace writer: emits JSONL logs for all scan attempts."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from app_paths import LOGS_DIR


class AttemptTraceWriter:
    _instance: AttemptTraceWriter | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._today = datetime.now(timezone.utc).strftime("%Y%m%d")
        self._path = LOGS_DIR / "attempts" / f"{self._today}.jsonl"
        self._buf: list[str] = []
        self._flush_lock = threading.Lock()

    @classmethod
    def get(cls) -> AttemptTraceWriter:
        if cls._instance is None or cls._instance._today != datetime.now(timezone.utc).strftime("%Y%m%d"):
            with cls._lock:
                if cls._instance is None or cls._instance._today != datetime.now(timezone.utc).strftime("%Y%m%d"):
                    cls._instance = cls()
        return cls._instance

    def write(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        self._buf.append(line)
        if len(self._buf) >= 50:
            self.flush()

    def flush(self) -> None:
        if not self._buf:
            return
        with self._flush_lock:
            if not self._buf:
                return
            batch = self._buf
            self._buf = []
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as f:
            f.write("\n".join(batch) + "\n")


def emit_trace(**fields) -> None:
    AttemptTraceWriter.get().write(fields)


def flush() -> None:
    AttemptTraceWriter.get().flush()