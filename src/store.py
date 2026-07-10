"""JSONL/JSON cache helpers under data/raw and data/analysis. Idempotent by date."""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "raw"
ANALYSIS_DIR = BASE_DIR / "data" / "analysis"


def _ensure_dirs() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)


def raw_path(date_str: str) -> Path:
    _ensure_dirs()
    return RAW_DIR / f"{date_str}.jsonl"


def analysis_path(date_str: str) -> Path:
    _ensure_dirs()
    return ANALYSIS_DIR / f"{date_str}.json"


def write_raw(messages: List[dict], date_str: str) -> Path:
    _ensure_dirs()
    path = raw_path(date_str)
    with open(path, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")
    return path


def read_raw(date_str: str) -> List[dict]:
    path = raw_path(date_str)
    if not path.exists():
        return []
    messages = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                messages.append(json.loads(line))
    return messages


def write_json(obj: dict, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=str)
    return path


def read_json(path: Path) -> Optional[dict]:
    path = Path(path)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
