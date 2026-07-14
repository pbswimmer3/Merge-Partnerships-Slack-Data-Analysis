"""Spend model builder + recommendations engine for the automation.html
spend panel (plan.md §5, Phase C3/C4). Reads the local LLM cost ledger
(`data/spend/ledger/*.json`, written by `cmd_analyze` via
`store.append_spend_ledger`) and produces a pure, JSON-serializable model:

    {
      "ledger": {...} | None,   # None when there are zero ledger entries
      "recommendations": [...]
    }

Ledger entries are dicts shaped like `store.append_spend_ledger`'s writes:
    {"run_at", "command", "model", "calls", "input_tokens", "output_tokens",
     "cache_read_input_tokens", "est_cost_usd", "dates_analyzed",
     "questions_analyzed"?}   # optional: added after the first ledger
                               # entries were written, so older entries lack it

Design decision — `cost_per_question` / `cost_per_candidate`: rather than
approximate with a denominator that doesn't line up with `total_usd` (e.g.
distinct dates_analyzed, which double-counts re-analyzed windows and mixes
"days" with "questions"), these are only computed from entries that
explicitly carry `questions_analyzed` (resp. an optional future
`automatable_count` field). If no entries carry the field, the value is
`None` and the UI renders "—". This keeps the number honest instead of
silently wrong, at the cost of being `None` until enough tagged entries
accumulate. `cost_per_candidate` is currently always `None` in practice
since no writer populates `automatable_count` yet; the field is kept for
forward compatibility with the same honest-or-None pattern.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from src.config import PRICING, estimate_cost_usd
from src import store

REC_THRESHOLD_USD = 0.10


def _entry_cost(entry: dict) -> float:
    v = entry.get("est_cost_usd")
    return float(v) if isinstance(v, (int, float)) else 0.0


def _run_date(entry: dict) -> str:
    return (entry.get("run_at") or "")[:10]


def _month_key(entry: dict) -> str:
    return (entry.get("run_at") or "")[:7]


def _num_months(entries: List[dict]) -> int:
    months = {_month_key(e) for e in entries if e.get("run_at")}
    return max(1, len(months))


def _build_by_day(entries: List[dict]) -> List[dict]:
    totals: Dict[str, float] = {}
    for e in entries:
        d = _run_date(e)
        if not d:
            continue
        totals[d] = totals.get(d, 0.0) + _entry_cost(e)
    return [{"date": d, "usd": round(totals[d], 6)} for d in sorted(totals)]


def _build_ledger_summary(entries: List[dict]) -> dict:
    sorted_entries = sorted(entries, key=lambda e: e.get("run_at") or "")
    since = sorted_entries[0]["run_at"] if sorted_entries else None

    total_usd = sum(_entry_cost(e) for e in entries)
    total_calls = sum(int(e.get("calls") or 0) for e in entries)
    total_input_tokens = sum(int(e.get("input_tokens") or 0) for e in entries)
    total_output_tokens = sum(int(e.get("output_tokens") or 0) for e in entries)

    q_entries = [e for e in entries if e.get("questions_analyzed") is not None]
    total_questions = sum(int(e["questions_analyzed"]) for e in q_entries)
    cost_per_question = (
        total_usd / total_questions if q_entries and total_questions > 0 else None
    )

    c_entries = [e for e in entries if e.get("automatable_count") is not None]
    total_candidates = sum(int(e["automatable_count"]) for e in c_entries)
    cost_per_candidate = (
        total_usd / total_candidates if c_entries and total_candidates > 0 else None
    )

    recent = sorted(entries, key=lambda e: e.get("run_at") or "", reverse=True)[:10]

    return {
        "since": since,
        "total_usd": round(total_usd, 6),
        "total_calls": total_calls,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "by_day": _build_by_day(entries),
        "per_run_recent": recent,
        "cost_per_question": cost_per_question,
        "cost_per_candidate": cost_per_candidate,
    }


def _rec_batch_api(total_usd: float, num_months: int) -> Optional[dict]:
    avg_monthly = total_usd / num_months
    est = avg_monthly * 0.5
    if est < REC_THRESHOLD_USD:
        return None
    return {
        "title": "Move nightly analyze runs to the Batch API",
        "detail": (
            "The nightly analyze cron is latency-insensitive; Anthropic's "
            "Batches API is 50% off standard token pricing for the same model."
        ),
        "est_monthly_savings_usd": round(est, 4),
        "kind": "batch_api",
    }


def _rec_prompt_caching(entries: List[dict], num_months: int) -> Optional[dict]:
    if not entries:
        return None
    if any((e.get("cache_read_input_tokens") or 0) > 0 for e in entries):
        return None
    input_cost_total = 0.0
    for e in entries:
        pricing = PRICING.get(e.get("model"))
        if not pricing:
            continue
        input_cost_total += (int(e.get("input_tokens") or 0) / 1_000_000) * pricing["input"]
    if input_cost_total <= 0:
        return None
    input_cost_monthly = input_cost_total / num_months
    est = 0.9 * 0.6 * input_cost_monthly
    if est < REC_THRESHOLD_USD:
        return None
    return {
        "title": "Enable prompt caching for the classifier's system prompt",
        "detail": (
            "No ledger entries show cache-read tokens yet, meaning the same "
            "classification system prompt is likely resent in full on every "
            "call. Caching the shared prefix (assumed ~60% of input tokens, "
            "~90% cache discount) could recover this."
        ),
        "est_monthly_savings_usd": round(est, 4),
        "kind": "prompt_caching",
        "label": "estimate",
    }


def _rec_model_mix(entries: List[dict], total_usd: float, num_months: int) -> Optional[dict]:
    if not entries:
        return None
    total_input = sum(int(e.get("input_tokens") or 0) for e in entries)
    total_output = sum(int(e.get("output_tokens") or 0) for e in entries)
    table = []
    for model_name in PRICING:
        cost = estimate_cost_usd(model_name, total_input, total_output) or 0.0
        table.append({"model": model_name, "est_cost_usd": round(cost, 4)})
    table.sort(key=lambda row: row["est_cost_usd"])
    cheapest = table[0]

    avg_monthly_actual = total_usd / num_months
    avg_monthly_cheapest = cheapest["est_cost_usd"] / num_months
    savings = avg_monthly_actual - avg_monthly_cheapest
    if savings < REC_THRESHOLD_USD:
        return None
    return {
        "title": f"Model mix: {cheapest['model']} is the cheapest fit for this token volume",
        "detail": (
            "Classification quality must be spot-checked before switching — "
            "this is a user decision, not auto-applied."
        ),
        "est_monthly_savings_usd": round(savings, 4),
        "kind": "model_mix",
        "label": "estimate",
        "table": table,
    }


def _rec_rerun_overlap(entries: List[dict], num_months: int) -> Optional[dict]:
    date_occurrences: Dict[str, List[tuple]] = {}
    for e in entries:
        dates = e.get("dates_analyzed") or []
        if not dates:
            continue
        share = _entry_cost(e) / len(dates)
        run_at = e.get("run_at") or ""
        for d in dates:
            date_occurrences.setdefault(d, []).append((run_at, share))

    wasted_total = 0.0
    duplicated_dates = 0
    for occurrences in date_occurrences.values():
        if len(occurrences) <= 1:
            continue
        duplicated_dates += 1
        occurrences_sorted = sorted(occurrences, key=lambda pair: pair[0])
        # First occurrence (chronologically) was the "needed" analysis;
        # every later re-run of that same date is approximate waste.
        wasted_total += sum(share for _, share in occurrences_sorted[1:])

    if duplicated_dates == 0:
        return None
    est = wasted_total / num_months
    if est < REC_THRESHOLD_USD:
        return None
    return {
        "title": f"{duplicated_dates} date(s) analyzed more than once",
        "detail": (
            "Re-running analyze over an already-analyzed date re-spends the "
            "classification cost for that date (the class of incident seen "
            "with the July 13 `--days 1` overwrite). Estimate is "
            "approximate: each run's cost is split proportionally across "
            "the dates it covers."
        ),
        "est_monthly_savings_usd": round(est, 4),
        "kind": "rerun_overlap",
        "label": "estimate",
    }


def _build_recommendations(entries: List[dict]) -> List[dict]:
    if not entries:
        return []
    total_usd = sum(_entry_cost(e) for e in entries)
    num_months = _num_months(entries)
    recs = [
        _rec_batch_api(total_usd, num_months),
        _rec_prompt_caching(entries, num_months),
        _rec_model_mix(entries, total_usd, num_months),
        _rec_rerun_overlap(entries, num_months),
    ]
    return [r for r in recs if r is not None]


def build_spend_model(entries: Optional[List[dict]] = None) -> dict:
    """Pure builder: given a flat list of ledger entries (as read from
    data/spend/ledger/*.json, one dict per run), return the spend model
    consumed by the automation.html panel:

        {"ledger": {...} | None, "recommendations": [...]}

    `ledger` is `None` when `entries` is empty (no LLM runs recorded yet) so
    the panel can render its explainer state instead of zeroed-out KPIs.

    Pass `entries=None` (the default) to load from disk via
    `store.read_all_spend_ledger()` -- the normal `cmd_dashboard` path. Pass
    an explicit list for pure/offline testing.
    """
    if entries is None:
        entries = store.read_all_spend_ledger()

    if not entries:
        return {"ledger": None, "recommendations": []}

    return {
        "ledger": _build_ledger_summary(entries),
        "recommendations": _build_recommendations(entries),
    }
