"""Render analysis results into a GitHub-flavored Markdown report."""
from __future__ import annotations

from typing import Optional


def _cfg_get(config, name, default=None):
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(name, default)
    return getattr(config, name, default)


def _fmt_seconds(seconds: Optional[float]) -> str:
    if seconds is None:
        return "n/a"
    seconds = float(seconds)
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.1f}m"
    hours = minutes / 60
    if hours < 24:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def _trend_note(per_day: dict) -> str:
    if not per_day or len(per_day) < 2:
        return "Not enough data to establish a trend."
    dates = sorted(per_day.keys())
    mid = len(dates) // 2
    first_half = dates[:mid] or dates[:1]
    second_half = dates[mid:] or dates[-1:]
    first_avg = sum(per_day[d]["messages"] for d in first_half) / len(first_half)
    second_avg = sum(per_day[d]["messages"] for d in second_half) / len(second_half)
    if second_avg > first_avg * 1.1:
        return f"Message volume is trending up ({first_avg:.1f} -> {second_avg:.1f} msgs/day average)."
    if second_avg < first_avg * 0.9:
        return f"Message volume is trending down ({first_avg:.1f} -> {second_avg:.1f} msgs/day average)."
    return f"Message volume is roughly stable (~{second_avg:.1f} msgs/day average)."


def _category_stats_from_questions(questions: list, key: str) -> dict:
    """Return {category: {count, avg_difficulty, automatable_share}} using `key`
    (either 'category' for heuristic or 'llm_category' for LLM output)."""
    stats: dict = {}
    for q in questions:
        cat = q.get(key)
        if not cat:
            continue
        s = stats.setdefault(cat, {"count": 0, "difficulties": [], "automatable": []})
        s["count"] += 1
        if q.get("difficulty") is not None:
            try:
                s["difficulties"].append(float(q["difficulty"]))
            except (TypeError, ValueError):
                pass
        if q.get("automatable") is not None:
            s["automatable"].append(bool(q["automatable"]))
    return stats


def _heuristic_difficulty_proxy(questions: list) -> dict:
    """Proxy difficulty per category from thread depth (reply_count) and latency,
    used only when LLM difficulty scores are unavailable."""
    stats: dict = {}
    for q in questions:
        cat = q.get("category", "other")
        s = stats.setdefault(cat, {"count": 0, "reply_counts": [], "latencies": []})
        s["count"] += 1
        s["reply_counts"].append(q.get("reply_count", 0) or 0)
        if q.get("first_reply_latency_sec") is not None:
            s["latencies"].append(q["first_reply_latency_sec"])
    return stats


def render_markdown(analysis: dict, llm_summary: Optional[str], config, date_str: str) -> str:
    channel_id = _cfg_get(config, "channel_id", "unknown")
    lookback_days = _cfg_get(config, "lookback_days", 30)
    llm_enabled = _cfg_get(config, "llm_enabled", False)

    totals = analysis.get("totals", {})
    per_day = analysis.get("per_day", {})
    category_distribution = analysis.get("category_distribution", {})
    top_askers = analysis.get("top_askers", [])
    response = analysis.get("response", {})
    questions = analysis.get("questions", [])

    dates = sorted(per_day.keys())
    date_range = f"{dates[0]} to {dates[-1]}" if dates else "n/a"

    lines: list = []
    lines.append(f"# #partnerships Slack Analysis — {date_str}")
    lines.append("")
    lines.append(f"**Channel:** `{channel_id}`  ")
    lines.append(f"**Date range:** {date_range} ({lookback_days} day look-back)")
    lines.append("")

    # Executive summary
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(
        f"- **{totals.get('message_count', 0)}** messages from **{totals.get('unique_users', 0)}** "
        f"unique users, including **{totals.get('question_count', 0)}** questions across "
        f"**{totals.get('thread_count', 0)}** threads."
    )
    unanswered = response.get("unanswered_question_count", 0)
    median_latency = response.get("median_first_reply_latency_sec")
    lines.append(
        f"- **{unanswered}** unanswered questions; median first-reply latency: "
        f"**{_fmt_seconds(median_latency)}**."
    )
    if llm_summary:
        lines.append("")
        lines.append(llm_summary.strip())
    lines.append("")

    # Trends
    lines.append("## Trends")
    lines.append("")
    lines.append("| Date | Messages | Questions |")
    lines.append("|---|---|---|")
    for d in dates:
        row = per_day[d]
        lines.append(f"| {d} | {row.get('messages', 0)} | {row.get('questions', 0)} |")
    lines.append("")
    lines.append(_trend_note(per_day))
    lines.append("")

    # Top question categories
    lines.append("## Top Question Categories")
    lines.append("")
    total_questions = sum(category_distribution.values()) or 1
    sorted_categories = sorted(category_distribution.items(), key=lambda kv: kv[1], reverse=True)
    lines.append("| Category | Count | % |")
    lines.append("|---|---|---|")
    for cat, count in sorted_categories:
        pct = 100.0 * count / total_questions
        lines.append(f"| {cat} | {count} | {pct:.1f}% |")
    if not sorted_categories:
        lines.append("| _(no questions detected)_ | 0 | 0.0% |")
    lines.append("")

    # Question types breakdown
    lines.append("## Question Types Breakdown")
    lines.append("")
    has_llm_categories = any(q.get("llm_category") for q in questions)
    if has_llm_categories:
        lines.append("| Heuristic Category | LLM Category | Count |")
        lines.append("|---|---|---|")
        pair_counts: dict = {}
        for q in questions:
            key = (q.get("category", "other"), q.get("llm_category") or "n/a")
            pair_counts[key] = pair_counts.get(key, 0) + 1
        for (heur, llm_cat), count in sorted(pair_counts.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"| {heur} | {llm_cat} | {count} |")
    else:
        lines.append("| Heuristic Category | Count |")
        lines.append("|---|---|")
        for cat, count in sorted_categories:
            lines.append(f"| {cat} | {count} |")
    lines.append("")

    # Difficulty ranking
    lines.append("## Difficulty Ranking")
    lines.append("")
    llm_stats = _category_stats_from_questions(questions, "llm_category")
    has_difficulty = any(s["difficulties"] for s in llm_stats.values())
    if has_difficulty:
        lines.append("| Category | Avg Difficulty (1-5) | Questions |")
        lines.append("|---|---|---|")
        ranked = sorted(
            ((cat, s) for cat, s in llm_stats.items() if s["difficulties"]),
            key=lambda kv: sum(kv[1]["difficulties"]) / len(kv[1]["difficulties"]),
            reverse=True,
        )
        for cat, s in ranked:
            avg_diff = sum(s["difficulties"]) / len(s["difficulties"])
            lines.append(f"| {cat} | {avg_diff:.1f} | {s['count']} |")
    else:
        lines.append("_Enable LLM analysis (set `ANTHROPIC_API_KEY`) for difficulty scoring._")
        lines.append("")
        lines.append("Heuristic proxy (avg thread depth / response latency per category):")
        lines.append("")
        lines.append("| Category | Avg Replies | Avg First-Reply Latency |")
        lines.append("|---|---|---|")
        proxy_stats = _heuristic_difficulty_proxy(questions)
        ranked_proxy = sorted(
            proxy_stats.items(),
            key=lambda kv: sum(kv[1]["reply_counts"]) / len(kv[1]["reply_counts"]) if kv[1]["reply_counts"] else 0,
            reverse=True,
        )
        for cat, s in ranked_proxy:
            avg_replies = sum(s["reply_counts"]) / len(s["reply_counts"]) if s["reply_counts"] else 0.0
            avg_latency = sum(s["latencies"]) / len(s["latencies"]) if s["latencies"] else None
            lines.append(f"| {cat} | {avg_replies:.1f} | {_fmt_seconds(avg_latency)} |")
    lines.append("")

    # Automation opportunities
    lines.append("## Automation Opportunities")
    lines.append("")
    opportunities = []
    if has_difficulty:
        for cat, s in llm_stats.items():
            if s["count"] < 2:
                continue
            automatable_share = (
                sum(1 for a in s["automatable"] if a) / len(s["automatable"])
                if s["automatable"] else 0.0
            )
            avg_diff = sum(s["difficulties"]) / len(s["difficulties"]) if s["difficulties"] else None
            is_high_volume = s["count"] >= max(2, total_questions * 0.1)
            qualifies = is_high_volume and (automatable_share > 0.5 or (avg_diff is not None and avg_diff <= 2))
            if qualifies:
                opportunities.append((cat, s["count"], automatable_share, avg_diff))
        opportunities.sort(key=lambda t: t[1], reverse=True)
        if opportunities:
            for cat, count, automatable_share, avg_diff in opportunities:
                diff_str = f"{avg_diff:.1f}" if avg_diff is not None else "n/a"
                lines.append(
                    f"- **{cat}** ({count} questions): {automatable_share * 100:.0f}% flagged automatable, "
                    f"avg difficulty {diff_str}. Candidate for FAQ/doc/bot coverage."
                )
        else:
            lines.append("No strong automation candidates identified from LLM signals this period.")
    else:
        # Heuristic fallback: high-volume categories only.
        high_volume = [
            (cat, count) for cat, count in sorted_categories
            if count >= max(2, total_questions * 0.15)
        ]
        if high_volume:
            lines.append("_Heuristic proxy (no LLM automatable signal available):_")
            lines.append("")
            for cat, count in high_volume:
                lines.append(
                    f"- **{cat}** ({count} questions): high volume; review for FAQ/doc coverage."
                )
        else:
            lines.append("No strong automation candidates identified this period.")
    lines.append("")

    # Top askers / response stats
    lines.append("## Top Askers")
    lines.append("")
    if top_askers:
        lines.append("| User | Questions Asked |")
        lines.append("|---|---|")
        for user, count in top_askers:
            lines.append(f"| {user} | {count} |")
    else:
        lines.append("_No questions detected._")
    lines.append("")
    lines.append(f"**Unanswered questions:** {unanswered}  ")
    lines.append(f"**Median response latency:** {_fmt_seconds(median_latency)}")
    lines.append("")

    return "\n".join(lines)
