"""JSONL/JSON cache helpers under data/raw and data/analysis. Idempotent by date."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "raw"
ANALYSIS_DIR = BASE_DIR / "data" / "analysis"
DAILY_ANALYSIS_DIR = BASE_DIR / "data" / "analysis_by_day"
SPEND_LEDGER_DIR = BASE_DIR / "data" / "spend" / "ledger"


def _ensure_dirs() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)


def raw_path(date_str: str) -> Path:
    _ensure_dirs()
    return RAW_DIR / f"{date_str}.jsonl"


def analysis_path(date_str: str) -> Path:
    _ensure_dirs()
    return ANALYSIS_DIR / f"{date_str}.json"


def daily_analysis_path(date_str: str) -> Path:
    _ensure_dirs()
    return DAILY_ANALYSIS_DIR / f"{date_str}.json"


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


def read_all_analysis() -> List[dict]:
    """Load every cached analysis JSON file under data/analysis/, sorted by filename."""
    _ensure_dirs()
    results = []
    for path in sorted(ANALYSIS_DIR.glob("*.json")):
        data = read_json(path)
        if data is not None:
            results.append(data)
    return results


def read_all_daily_analysis() -> List[dict]:
    """Load every cached analysis JSON file under data/analysis_by_day/, sorted by filename."""
    _ensure_dirs()
    results = []
    for path in sorted(DAILY_ANALYSIS_DIR.glob("*.json")):
        data = read_json(path)
        if data is not None:
            results.append(data)
    return results


def append_spend_ledger(
    run_at: str,
    command: str,
    model: str,
    calls: int,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int,
    est_cost_usd: Optional[float],
    dates_analyzed: List[str],
) -> Path:
    """Append an entry to the monthly spend ledger (data/spend/ledger/YYYY-MM.json).

    Handles corrupt/unreadable ledger files by backing them up as .bak and starting fresh,
    with a warning to stderr.
    """
    # Extract YYYY-MM from run_at (ISO8601 format)
    month_key = run_at[:7]  # "2026-07"

    SPEND_LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    ledger_path = SPEND_LEDGER_DIR / f"{month_key}.json"

    # Try to read existing ledger
    ledger = []
    if ledger_path.exists():
        try:
            with open(ledger_path, "r", encoding="utf-8") as f:
                ledger = json.load(f)
            if not isinstance(ledger, list):
                ledger = []
        except (OSError, ValueError) as exc:
            # Backup corrupt file and start fresh
            backup_path = ledger_path.parent / f"{ledger_path.name}.bak"
            try:
                ledger_path.rename(backup_path)
                print(f"WARNING: Corrupt ledger file backed up to {backup_path}; starting fresh.", file=sys.stderr)
            except OSError as e:
                print(f"WARNING: Could not back up corrupt ledger: {e}; starting fresh.", file=sys.stderr)
            ledger = []

    # Append new entry
    entry = {
        "run_at": run_at,
        "command": command,
        "model": model,
        "calls": calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "est_cost_usd": est_cost_usd,
        "dates_analyzed": dates_analyzed,
    }
    ledger.append(entry)

    # Write back
    with open(ledger_path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2)
        f.write("\n")

    return ledger_path
