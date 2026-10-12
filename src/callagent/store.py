"""Append-only call records on disk: one JSONL transcript per call, summaries and messages."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any


def _safe(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", name).strip("_")
    return cleaned or "unknown"


class CallStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        (self.root / "calls").mkdir(parents=True, exist_ok=True)
        (self.root / "summaries").mkdir(parents=True, exist_ok=True)

    def _call_path(self, call_sid: str) -> Path:
        return self.root / "calls" / f"{_safe(call_sid)}.jsonl"

    @staticmethod
    def _append(path: Path, record: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def log(self, call_sid: str, kind: str, **data: Any) -> None:
        self._append(self._call_path(call_sid), {"ts": time.time(), "kind": kind, **data})

    def read(self, call_sid: str) -> list[dict[str, Any]]:
        path = self._call_path(call_sid)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def save_summary(self, call_sid: str, summary: dict[str, Any]) -> Path:
        path = self.root / "summaries" / f"{_safe(call_sid)}.json"
        path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def add_message(self, *, caller: str, text: str, call_sid: str) -> None:
        self._append(self.root / "messages.jsonl",
                     {"ts": time.time(), "caller": caller, "text": text, "call_sid": call_sid})

    def messages(self) -> list[dict[str, Any]]:
        path = self.root / "messages.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
