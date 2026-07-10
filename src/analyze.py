"""Pure analysis functions over normalized message dicts. No Slack/LLM calls here."""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Union

INTERROGATIVES = (
    "how", "what", "why", "when", "where", "which", "who",
    "can", "could", "does", "do", "is there", "are there",
)
ASK_PATTERNS = ("need help", "how do i", "any way to")


def is_question(text: str) -> bool:
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.endswith("?"):
        return True
    lowered = stripped.lower()
    for pattern in ASK_PATTERNS:
        if pattern in lowered:
            return True
    first_word = lowered.split()[0].strip(",.!") if lowered.split() else ""
    for word in INTERROGATIVES:
        if " " in word:
            if lowered.startswith(word):
                return True
        elif first_word == word:
            return True
    return False


def categorize_heuristic(text: str, categories: Dict[str, List[str]]) -> str:
    if not text:
        return "other"
    lowered = text.lower()
    for category, keywords in (categories or {}).items():
        for kw in keywords:
            if str(kw).lower() in lowered:
                return category
    return "other"


def _ts_to_date(ts: Optional[str]) -> str:
    try:
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return "unknown"


def _first_reply_latency(msg: dict) -> Optional[float]:
    replies = msg.get("replies") or []
    if not replies:
        return None
    try:
        root_ts = float(msg["ts"])
        reply_tss = [float(r["ts"]) for r in replies if r.get("ts")]
        if not reply_tss:
            return None
        first_reply_ts = min(reply_tss)
        return max(0.0, first_reply_ts - root_ts)
    except (TypeError, ValueError, KeyError):
        return None


def _flatten_messages(messages: List[dict]) -> List[dict]:
    """Flatten thread replies into top-level entries so reply-authored
    questions are counted alongside root messages. Replies have no nested
    replies of their own (reply_count 0 / latency None)."""
    flattened: List[dict] = []
    for msg in messages:
        flattened.append(msg)
        for reply in msg.get("replies") or []:
            flattened.append({
                "ts": reply.get("ts"),
                "user": reply.get("user", "unknown"),
                "text": reply.get("text", "") or "",
                "reply_count": 0,
                "replies": [],
                "permalink": reply.get("permalink"),
            })
    return flattened


def _get_categories(config: Union[object, dict, None]) -> Dict[str, List[str]]:
    if config is None:
        return {}
    if isinstance(config, dict):
        return config.get("categories", {}) or {}
    return getattr(config, "categories", {}) or {}


def analyze(messages: List[dict], config: Union[object, dict, None] = None) -> dict:
    categories_cfg = _get_categories(config)

    thread_count = sum(1 for m in messages if (m.get("reply_count", 0) or 0) > 0)
    flattened_messages = _flatten_messages(messages)
    message_count = len(flattened_messages)
    users = set()
    per_day: Dict[str, Dict[str, int]] = defaultdict(lambda: {"messages": 0, "questions": 0})
    category_distribution: Counter = Counter()
    asker_counts: Counter = Counter()
    latencies: List[float] = []
    unanswered_question_count = 0
    questions_out: List[dict] = []

    for msg in flattened_messages:
        user = msg.get("user", "unknown")
        users.add(user)
        date_str = _ts_to_date(msg.get("ts"))
        per_day[date_str]["messages"] += 1

        text = msg.get("text", "") or ""
        question = is_question(text)
        if question:
            per_day[date_str]["questions"] += 1
            category = categorize_heuristic(text, categories_cfg)
            category_distribution[category] += 1
            asker_counts[user] += 1

            reply_count = msg.get("reply_count", 0) or 0
            latency = _first_reply_latency(msg)
            if reply_count == 0:
                unanswered_question_count += 1
            if latency is not None:
                latencies.append(latency)

            questions_out.append({
                "ts": msg.get("ts"),
                "user": user,
                "text": text,
                "category": category,
                "is_question": True,
                "reply_count": reply_count,
                "first_reply_latency_sec": latency,
                "permalink": msg.get("permalink"),
            })

    question_count = len(questions_out)
    median_latency = statistics.median(latencies) if latencies else None
    top_askers = asker_counts.most_common(10)

    return {
        "totals": {
            "message_count": message_count,
            "question_count": question_count,
            "thread_count": thread_count,
            "unique_users": len(users),
        },
        "per_day": dict(sorted(per_day.items())),
        "category_distribution": dict(category_distribution),
        "top_askers": top_askers,
        "response": {
            "median_first_reply_latency_sec": median_latency,
            "unanswered_question_count": unanswered_question_count,
        },
        "questions": questions_out,
    }
