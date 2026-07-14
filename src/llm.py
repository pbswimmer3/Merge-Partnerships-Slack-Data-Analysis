"""Anthropic-backed classification and summarization. Only invoked when llm_enabled.

All functions degrade gracefully (return input unchanged / None) on any error so the
pipeline never crashes when the API is unavailable, misconfigured, or rate limited.
"""
from __future__ import annotations

import json
import re
import sys
from typing import List

CLASSIFY_CATEGORIES = [
    "api_technical",
    "integration_connector",
    "data_sync",
    "pricing_commercial",
    "access_permissions",
    "bug_issue",
    "auth_scopes",
    "partnership_process",
    "customer_request",
    "feature_request",
    "sales_marketing",
    "internal_ops",
    "other",
]

CLASSIFY_SYSTEM_PROMPT = (
    "You are a support/partnerships analyst. For each question you are given, "
    "return a JSON object with fields: index (int, matching input index), "
    "llm_category (one of the following exact snake_case labels: "
    + ", ".join(CLASSIFY_CATEGORIES) + "), subtopic (short free text, "
    "max 6 words), difficulty (integer 1-5, 1=trivial 5=very hard/ambiguous), "
    "automatable (boolean, true if a doc/FAQ/bot could answer this without a human), "
    "rationale (max 20 words explaining difficulty/automatable). "
    "Always choose the single most specific matching category from the list. "
    "Only use 'other' as a last resort when none of the other categories reasonably apply. "
    "Respond ONLY with a JSON array of these objects, no prose, no markdown fences."
)

SUMMARY_SYSTEM_PROMPT = (
    "You are a partnerships operations analyst. Given aggregate stats about a Slack "
    "channel, write a concise 3-5 sentence narrative summary highlighting trends, "
    "the most common question categories, and any notable automation opportunities. "
    "Plain text only, no markdown headers."
)


def _extract_json_array(text: str):
    text = text.strip()
    # Strip markdown code fences if present.
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        text = match.group(0)
    return json.loads(text)


def classify_questions(questions: List[dict], config) -> List[dict]:
    """Augments each question dict in-place-ish (returns new list) with:
    llm_category, subtopic, difficulty, automatable, rationale.
    On any error or when disabled, returns questions unchanged.
    """
    if not questions:
        return questions
    if not getattr(config, "llm_enabled", False):
        return questions

    try:
        import anthropic
    except ImportError:
        return questions

    api_key = getattr(config, "anthropic_key", None)
    if not api_key:
        return questions

    try:
        client = anthropic.Anthropic(api_key=api_key, base_url=getattr(config, "anthropic_base_url", None))
    except Exception as exc:
        print(f"WARNING: could not construct Anthropic client ({type(exc).__name__}: {exc}); skipping LLM classification.", file=sys.stderr)
        return questions

    model = getattr(config, "llm_model", "claude-opus-4-8")
    batch_size = getattr(config, "llm_batch_size", 25) or 25

    result = [dict(q) for q in questions]

    for start in range(0, len(result), batch_size):
        batch = result[start:start + batch_size]
        payload = [
            {"index": i, "text": q.get("text", ""), "heuristic_category": q.get("category", "other")}
            for i, q in enumerate(batch)
        ]
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=CLASSIFY_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Classify these questions. Input JSON:\n"
                            + json.dumps(payload)
                        ),
                    }
                ],
            )
            content_text = "".join(
                block.text for block in response.content if getattr(block, "type", None) == "text"
            )
            parsed = _extract_json_array(content_text)
            by_index = {item.get("index"): item for item in parsed if isinstance(item, dict)}
            for i, q in enumerate(batch):
                item = by_index.get(i)
                if not item:
                    continue
                q["llm_category"] = item.get("llm_category")
                q["subtopic"] = item.get("subtopic")
                q["difficulty"] = item.get("difficulty")
                q["automatable"] = item.get("automatable")
                q["rationale"] = item.get("rationale")
        except Exception as exc:
            print(f"WARNING: LLM classification batch failed ({type(exc).__name__}: {exc}); leaving it unaugmented.", file=sys.stderr)
            continue

    return result


def summarize_trends(analysis: dict, config) -> str:
    """Short narrative string summarizing trends. Returns "" on any failure."""
    if not getattr(config, "llm_enabled", False):
        return ""

    try:
        import anthropic
    except ImportError:
        return ""

    api_key = getattr(config, "anthropic_key", None)
    if not api_key:
        return ""

    try:
        client = anthropic.Anthropic(api_key=api_key, base_url=getattr(config, "anthropic_base_url", None))
        model = getattr(config, "llm_model", "claude-opus-4-8")
        stats_payload = {
            "totals": analysis.get("totals"),
            "category_distribution": analysis.get("category_distribution"),
            "response": analysis.get("response"),
            "per_day": analysis.get("per_day"),
        }
        response = client.messages.create(
            model=model,
            max_tokens=512,
            system=SUMMARY_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": "Aggregate stats JSON:\n" + json.dumps(stats_payload, default=str),
                }
            ],
        )
        return "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ).strip()
    except Exception as exc:
        print(f"WARNING: LLM summary generation failed ({type(exc).__name__}: {exc}); leaving summary blank.", file=sys.stderr)
        return ""
