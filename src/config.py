"""Configuration loading: config.yaml + .env, resolved into a Config dataclass."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from dotenv import load_dotenv

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
NOTION_STATE_PATH = Path(__file__).resolve().parent.parent / "notion_state.json"


def read_notion_state(path: Path = NOTION_STATE_PATH) -> Optional[str]:
    """Return the database_id from the committed state file pointer, or None."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        return data.get("database_id") or None
    except (OSError, ValueError):
        return None


def write_notion_state(database_id: str, path: Path = NOTION_STATE_PATH) -> None:
    """Persist the created database_id so subsequent runs reuse it."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"database_id": database_id}, f, indent=2)
        f.write("\n")


@dataclass
class Config:
    channel_id: str
    lookback_days: int
    llm_enabled: bool
    llm_model: str
    llm_batch_size: int
    categories: Dict[str, List[str]]
    slack_token: Optional[str]
    anthropic_key: Optional[str]
    post_channel_id: Optional[str]
    notion_token: Optional[str]
    notion_parent_page_id: Optional[str]
    notion_database_id: Optional[str]

    @classmethod
    def resolve(cls, config_path: Optional[str] = None, days_override: Optional[int] = None) -> "Config":
        # Load .env into process env (no-op if file absent).
        load_dotenv()

        path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        raw: dict = {}
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}

        env_channel = os.environ.get("SLACK_CHANNEL_ID")
        channel_id = env_channel or raw.get("channel_id") or ""

        lookback_days = days_override if days_override is not None else int(raw.get("lookback_days", 30))

        llm_cfg = raw.get("llm", {}) or {}
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or None
        enabled_raw = llm_cfg.get("enabled", "auto")
        if isinstance(enabled_raw, str) and enabled_raw.strip().lower() == "auto":
            llm_enabled = bool(anthropic_key)
        elif isinstance(enabled_raw, bool):
            llm_enabled = enabled_raw
        else:
            llm_enabled = str(enabled_raw).strip().lower() in ("true", "1", "yes")

        llm_model = llm_cfg.get("model", "claude-opus-4-8")
        llm_batch_size = int(llm_cfg.get("batch_size", 25))

        categories = raw.get("categories", {}) or {}

        notion_cfg = raw.get("notion", {}) or {}
        notion_parent_page_id = (
            os.environ.get("NOTION_PARENT_PAGE_ID") or notion_cfg.get("parent_page_id") or None
        )
        notion_database_id = (
            os.environ.get("NOTION_DATABASE_ID")
            or read_notion_state()
            or notion_cfg.get("database_id")
            or None
        )

        return cls(
            channel_id=channel_id,
            lookback_days=lookback_days,
            llm_enabled=llm_enabled,
            llm_model=llm_model,
            llm_batch_size=llm_batch_size,
            categories=categories,
            slack_token=os.environ.get("SLACK_BOT_TOKEN") or None,
            anthropic_key=anthropic_key,
            post_channel_id=os.environ.get("SLACK_POST_CHANNEL_ID") or None,
            notion_token=os.environ.get("NOTION_API_KEY") or None,
            notion_parent_page_id=notion_parent_page_id,
            notion_database_id=notion_database_id,
        )
