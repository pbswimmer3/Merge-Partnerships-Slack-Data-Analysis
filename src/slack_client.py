"""Slack scraping: live Web API client + offline Slack-export fallback loader."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


def _normalize_message(raw: dict, replies: Optional[List[dict]] = None) -> dict:
    reactions = raw.get("reactions", []) or []
    reaction_count = sum(r.get("count", 0) for r in reactions)
    return {
        "ts": raw.get("ts"),
        "user": raw.get("user") or raw.get("bot_id") or "unknown",
        "text": raw.get("text", "") or "",
        "thread_ts": raw.get("thread_ts"),
        "reply_count": raw.get("reply_count", 0) or 0,
        "reply_users_count": raw.get("reply_users_count", 0) or 0,
        "reactions": reaction_count,
        "replies": replies or [],
        "permalink": raw.get("permalink"),
    }


class SlackScraper:
    def __init__(self, token: str):
        self.client = WebClient(token=token)

    def _call_with_retry(self, fn, max_attempts: int = 5, **kwargs):
        attempt = 0
        while True:
            try:
                return fn(**kwargs)
            except SlackApiError as e:
                error = e.response.get("error") if e.response is not None else str(e)
                if error != "ratelimited":
                    raise
                attempt += 1
                if attempt >= max_attempts:
                    raise
                retry_after = None
                if e.response is not None:
                    header_val = e.response.headers.get("Retry-After")
                    if header_val is not None:
                        retry_after = int(header_val)
                if retry_after is None:
                    retry_after = min(2 ** attempt, 16)
                time.sleep(retry_after)

    def fetch_channel_history(self, channel_id: str, oldest_ts: float) -> List[dict]:
        messages: List[dict] = []
        cursor: Optional[str] = None
        while True:
            resp = self._call_with_retry(
                self.client.conversations_history,
                channel=channel_id,
                oldest=str(oldest_ts),
                limit=200,
                cursor=cursor,
            )
            batch = resp.get("messages", [])
            messages.extend(batch)
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break

        normalized: List[dict] = []
        for raw in messages:
            replies: List[dict] = []
            if (raw.get("reply_count", 0) or 0) > 0 and raw.get("thread_ts"):
                thread_replies = self.fetch_thread_replies(channel_id, raw["thread_ts"])
                # Exclude the root message (first item) from replies list.
                replies = [r for r in thread_replies if r.get("ts") != raw.get("ts")]
            permalink = self.get_permalink(channel_id, raw.get("ts"))
            msg = _normalize_message(raw, replies=replies)
            msg["permalink"] = permalink
            normalized.append(msg)
        return normalized

    def fetch_thread_replies(self, channel_id: str, thread_ts: str) -> List[dict]:
        replies: List[dict] = []
        cursor: Optional[str] = None
        while True:
            resp = self._call_with_retry(
                self.client.conversations_replies,
                channel=channel_id,
                ts=thread_ts,
                limit=200,
                cursor=cursor,
            )
            batch = resp.get("messages", [])
            replies.extend(_normalize_message(r) for r in batch)
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        return replies

    def get_permalink(self, channel_id: str, ts: Optional[str]) -> Optional[str]:
        if not ts:
            return None
        try:
            resp = self.client.chat_getPermalink(channel=channel_id, message_ts=ts)
            return resp.get("permalink")
        except Exception:
            return None

    def post_message(self, channel_id: str, text: str) -> None:
        self.client.chat_postMessage(channel=channel_id, text=text)


def load_export(export_dir: str) -> List[dict]:
    """Load a Slack export directory (per-day JSON files, e.g. 2026-07-01.json)
    into the same normalized message shape produced by SlackScraper.
    """
    directory = Path(export_dir)
    all_raw: List[dict] = []
    for path in sorted(directory.glob("*.json")):
        with open(path, "r", encoding="utf-8") as f:
            day_messages = json.load(f)
        all_raw.extend(day_messages)

    by_ts = {m.get("ts"): m for m in all_raw}
    normalized: List[dict] = []
    for raw in all_raw:
        # Skip messages that are thread replies but not thread roots (they'll be
        # attached as replies to their root below); roots have thread_ts == ts
        # or no thread_ts at all.
        if raw.get("thread_ts") and raw.get("thread_ts") != raw.get("ts"):
            continue
        replies: List[dict] = []
        if (raw.get("reply_count", 0) or 0) > 0:
            thread_ts = raw.get("thread_ts") or raw.get("ts")
            replies = [
                _normalize_message(r)
                for r in all_raw
                if r.get("thread_ts") == thread_ts and r.get("ts") != raw.get("ts")
            ]
        normalized.append(_normalize_message(raw, replies=replies))
    return normalized
