"""Notion output: maps analyzed questions to Notion database rows and upserts them.

Import never requires secrets; the Client is only constructed (and network calls
only made) inside write_analysis / ensure_database / upsert_question.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from notion_client import Client

logger = logging.getLogger(__name__)

TITLE_MAX_LEN = 200

DATABASE_PROPERTIES = {
    "Question": {"title": {}},
    "Date": {"date": {}},
    "Category": {"select": {}},
    "LLM Category": {"select": {}},
    "Subtopic": {"rich_text": {}},
    "Difficulty": {"number": {}},
    "Automatable": {"checkbox": {}},
    "Reply Count": {"number": {}},
    "First Reply Latency (min)": {"number": {}},
    "Slack User": {"rich_text": {}},
    "Message TS": {"rich_text": {}},
    "Permalink": {"url": {}},
}


def _ts_to_date_str(ts) -> Optional[str]:
    try:
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def build_row_properties(question: dict, db_schema_owned: bool = True) -> dict:
    """Pure mapping from one analyzed question dict to a Notion `properties` payload.
    Omits any property whose value is None so we never send nulls.
    """
    text = (question.get("text") or "")[:TITLE_MAX_LEN]
    props: dict = {
        "Question": {"title": [{"type": "text", "text": {"content": text}}]},
        "Category": {"select": {"name": question["category"]}} if question.get("category") else None,
        "Automatable": {"checkbox": bool(question.get("automatable", False))},
        "Reply Count": {"number": question.get("reply_count", 0)},
        "Message TS": {"rich_text": [{"type": "text", "text": {"content": str(question["ts"])}}]},
    }

    date_str = _ts_to_date_str(question.get("ts"))
    if date_str is not None:
        props["Date"] = {"date": {"start": date_str}}

    llm_category = question.get("llm_category")
    if llm_category is not None:
        props["LLM Category"] = {"select": {"name": llm_category}}

    subtopic = question.get("subtopic")
    if subtopic is not None:
        props["Subtopic"] = {"rich_text": [{"type": "text", "text": {"content": str(subtopic)}}]}

    difficulty = question.get("difficulty")
    if difficulty is not None:
        props["Difficulty"] = {"number": difficulty}

    latency_sec = question.get("first_reply_latency_sec")
    if latency_sec is not None:
        props["First Reply Latency (min)"] = {"number": round(latency_sec / 60, 1)}

    user = question.get("user")
    if user is not None:
        props["Slack User"] = {"rich_text": [{"type": "text", "text": {"content": str(user)}}]}

    permalink = question.get("permalink")
    if permalink is not None:
        props["Permalink"] = {"url": permalink}

    return {k: v for k, v in props.items() if v is not None}


def ensure_database(client: Client, parent_page_id: str, database_id: Optional[str], title: str = "Partnerships Questions") -> str:
    if database_id:
        return database_id
    created = client.databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=[{"type": "text", "text": {"content": title}}],
        properties=DATABASE_PROPERTIES,
    )
    return created["id"]


def upsert_question(client: Client, database_id: str, question: dict) -> None:
    properties = build_row_properties(question)
    ts = str(question["ts"])
    results = client.databases.query(
        database_id=database_id,
        filter={"property": "Message TS", "rich_text": {"equals": ts}},
    ).get("results", [])

    if results:
        page_id = results[0]["id"]
        client.pages.update(page_id=page_id, properties=properties)
    else:
        client.pages.create(parent={"database_id": database_id}, properties=properties)


def write_analysis(analysis: dict, notion_token: str, parent_page_id: str, database_id: Optional[str]) -> str:
    try:
        client = Client(auth=notion_token)
        database_id = ensure_database(client, parent_page_id, database_id)
        for question in analysis.get("questions", []):
            upsert_question(client, database_id, question)
        return database_id
    except Exception:
        logger.exception("Failed to write analysis to Notion.")
        raise
