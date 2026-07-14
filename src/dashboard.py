"""Pure aggregation + self-contained HTML rendering for the static dashboard.

`aggregate()` takes a list of loaded analysis JSON dicts (each the output of
`src.analyze.analyze()`, possibly round-tripped through JSON) and produces a
plain-dict "dashboard model". `render_html()` turns that model into a
complete, dependency-free HTML document (inline CSS + inline SVG/JS charts,
no CDNs/network calls).
"""
from __future__ import annotations

import datetime
import html
import json
import math
import statistics
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# aggregate()
# ---------------------------------------------------------------------------

def _max_date(per_day: dict) -> str:
    return max(per_day.keys()) if per_day else ""


def _most_recent(analysis_files: List[dict]) -> dict:
    """Pick the file whose per_day covers the latest date. Ties favor the
    later entry in the input list. Empty input -> {}."""
    if not analysis_files:
        return {}
    best = analysis_files[0]
    best_date = _max_date(best.get("per_day") or {})
    for f in analysis_files[1:]:
        d = _max_date(f.get("per_day") or {})
        if d >= best_date:
            best = f
            best_date = d
    return best


def _merge_timeseries(analysis_files: List[dict]) -> List[dict]:
    merged: Dict[str, Dict[str, int]] = {}
    for f in analysis_files:
        for date_str, counts in (f.get("per_day") or {}).items():
            row = merged.setdefault(date_str, {"messages": 0, "questions": 0})
            row["messages"] += int((counts or {}).get("messages", 0) or 0)
            row["questions"] += int((counts or {}).get("questions", 0) or 0)
    return [
        {"date": d, "messages": merged[d]["messages"], "questions": merged[d]["questions"]}
        for d in sorted(merged.keys())
    ]


def _normalize_top_askers(raw) -> List[dict]:
    out = []
    for item in raw or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            out.append({"user": item[0], "count": item[1]})
        elif isinstance(item, dict):
            out.append({"user": item.get("user"), "count": item.get("count", 0)})
    return out


def _difficulty_by_category(questions: List[dict]) -> List[dict]:
    stats: Dict[str, dict] = {}
    for q in questions or []:
        cat = q.get("category") or "other"
        s = stats.setdefault(cat, {"count": 0, "difficulties": [], "automatable_flags": []})
        s["count"] += 1
        diff = q.get("difficulty")
        if diff is not None:
            try:
                s["difficulties"].append(float(diff))
            except (TypeError, ValueError):
                pass
        automatable = q.get("automatable")
        if automatable is not None:
            s["automatable_flags"].append(bool(automatable))

    out = []
    for cat, s in stats.items():
        avg_difficulty = (
            sum(s["difficulties"]) / len(s["difficulties"]) if s["difficulties"] else None
        )
        automatable_pct = (
            100.0 * sum(1 for a in s["automatable_flags"] if a) / len(s["automatable_flags"])
            if s["automatable_flags"] else None
        )
        out.append({
            "category": cat,
            "count": s["count"],
            "avg_difficulty": avg_difficulty,
            "automatable_pct": automatable_pct,
        })
    out.sort(key=lambda r: r["count"], reverse=True)
    return out


def _automation_opportunities(difficulty_by_category: List[dict]) -> (List[dict], int):
    scored = []
    for row in difficulty_by_category:
        count = row["count"]
        avg_difficulty = row["avg_difficulty"]
        automatable_pct = row["automatable_pct"]

        if automatable_pct is not None:
            factor = automatable_pct / 100.0
            rationale = (
                f"{automatable_pct:.0f}% of {count} questions flagged automatable."
            )
        elif avg_difficulty is not None:
            normalized_difficulty = max(0.0, min(1.0, (avg_difficulty - 1) / 4))
            factor = 1 - normalized_difficulty
            rationale = (
                f"{count} questions, low avg difficulty ({avg_difficulty:.1f}/5)."
            )
        else:
            factor = 1.0
            rationale = f"{count} questions; ranked by volume (no LLM signal available)."

        score = count * factor
        scored.append({
            "category": row["category"],
            "count": count,
            "avg_difficulty": avg_difficulty,
            "automatable_pct": automatable_pct,
            "rationale": rationale,
            "_score": score,
        })

    scored.sort(key=lambda r: r["_score"], reverse=True)

    n = len(scored)
    top_third_cutoff = math.ceil(n / 3) if n else 0
    candidate_names = set()
    for i, row in enumerate(scored):
        is_top_third = i < top_third_cutoff
        is_high_automatable = (row["automatable_pct"] or 0) >= 50
        if is_top_third or is_high_automatable:
            candidate_names.add(row["category"])
    automation_candidate_count = len(candidate_names)

    opportunities = []
    for row in scored:
        item = {k: v for k, v in row.items() if k != "_score"}
        item["is_candidate"] = item["category"] in candidate_names
        opportunities.append(item)

    return opportunities, automation_candidate_count



# ---------------------------------------------------------------------------
# category -> color-slot assignment (§2.2, binding across dashboard + automation
# page). Fixed categories always map to the same slot regardless of rank;
# unrecognized categories (excluding "other") get slot 7 then 8, in
# alphabetical order among themselves; anything beyond that, and "other"
# itself, always falls back to the muted gray sentinel.
# ---------------------------------------------------------------------------

_FIXED_CATEGORY_SLOTS = {
    "api_technical": "series-1",
    "integration_connector": "series-2",
    "pricing_commercial": "series-3",
    "data_sync": "series-4",
    "access_permissions": "series-5",
    "bug_issue": "series-6",
}
_EXTRA_CATEGORY_SLOTS = ["series-7", "series-8"]
_MUTED_SLOT = "muted"


def _category_color_map(category_names: List[str]) -> Dict[str, str]:
    """Assign a stable color slot to each category name. Order of
    `category_names` does not matter for fixed categories; unknown categories
    are assigned slot 7/8 in alphabetical order, further extras -> muted."""
    seen: List[str] = []
    seen_set = set()
    for cat in category_names or []:
        if cat and cat not in seen_set:
            seen_set.add(cat)
            seen.append(cat)

    mapping: Dict[str, str] = {}
    unknown = []
    for cat in seen:
        if cat == "other":
            mapping[cat] = _MUTED_SLOT
        elif cat in _FIXED_CATEGORY_SLOTS:
            mapping[cat] = _FIXED_CATEGORY_SLOTS[cat]
        else:
            unknown.append(cat)

    for i, cat in enumerate(sorted(unknown)):
        mapping[cat] = _EXTRA_CATEGORY_SLOTS[i] if i < len(_EXTRA_CATEGORY_SLOTS) else _MUTED_SLOT

    return mapping


def _sparkline(timeseries: List[dict], key: str, window: int = 30) -> List[int]:
    w = min(window, len(timeseries))
    if w <= 0:
        return []
    return [row[key] for row in timeseries[-w:]]


def _window_delta(timeseries: List[dict], key: str, window: int = 30) -> dict:
    # Calendar-day windows, not row counts: rows only exist for days with
    # activity, so slicing by row would silently compare "active days".
    empty = {"pct": None, "current": 0, "previous": 0, "window": window}
    if not timeseries:
        return empty
    try:
        end = datetime.date.fromisoformat(timeseries[-1]["date"])
        first = datetime.date.fromisoformat(timeseries[0]["date"])
    except (KeyError, ValueError):
        return empty
    cur_start = end - datetime.timedelta(days=window - 1)
    prev_start = cur_start - datetime.timedelta(days=window)
    if first > prev_start:
        return empty  # data does not cover the full previous window
    current = previous = 0
    for row in timeseries:
        try:
            d = datetime.date.fromisoformat(row["date"])
        except (KeyError, ValueError):
            continue
        if d >= cur_start:
            current += row[key]
        elif d >= prev_start:
            previous += row[key]
    pct = 100.0 * (current - previous) / previous if previous else None
    return {"pct": pct, "current": current, "previous": previous, "window": window}


def aggregate(analysis_files: List[dict], user_directory: Optional[dict] = None) -> dict:
    analysis_files = analysis_files or []

    timeseries = _merge_timeseries(analysis_files)
    total_messages = sum(row["messages"] for row in timeseries)
    total_questions = sum(row["questions"] for row in timeseries)

    unanswered_count = sum(
        int((f.get("response") or {}).get("unanswered_question_count", 0) or 0)
        for f in analysis_files
    )
    unanswered_pct = (100.0 * unanswered_count / total_questions) if total_questions else 0.0

    all_latencies: List[float] = []
    for f in analysis_files:
        for q in (f.get("questions") or []):
            lat = q.get("first_reply_latency_sec")
            if lat is not None:
                try:
                    all_latencies.append(float(lat))
                except (TypeError, ValueError):
                    pass
    median_first_reply_min = (
        statistics.median(all_latencies) / 60.0 if all_latencies else None
    )

    most_recent = _most_recent(analysis_files)
    category_distribution = most_recent.get("category_distribution") or {}
    total_recent_categorized = sum(category_distribution.values()) or 1
    categories = [
        {
            "name": cat,
            "count": count,
            "pct": 100.0 * count / total_recent_categorized,
        }
        for cat, count in sorted(category_distribution.items(), key=lambda kv: kv[1], reverse=True)
    ]

    difficulty_by_category = _difficulty_by_category(most_recent.get("questions") or [])
    automation_opportunities, automation_candidate_count = _automation_opportunities(
        difficulty_by_category
    )

    top_askers = _normalize_top_askers(most_recent.get("top_askers"))
    for item in top_askers:
        item["display_name"] = (user_directory or {}).get(item["user"], item["user"])

    summary = most_recent.get("llm_summary") or most_recent.get("summary") or ""

    date_range = {
        "start": timeseries[0]["date"] if timeseries else None,
        "end": timeseries[-1]["date"] if timeseries else None,
    }

    kpi_sparklines = {
        "messages": _sparkline(timeseries, "messages"),
        "questions": _sparkline(timeseries, "questions"),
    }
    kpi_deltas = {
        "messages": _window_delta(timeseries, "messages"),
        "questions": _window_delta(timeseries, "questions"),
    }

    category_name_order: List[str] = []
    for c in categories:
        category_name_order.append(c["name"])
    for r in difficulty_by_category:
        category_name_order.append(r["category"])
    category_colors = _category_color_map(category_name_order)

    return {
        "kpis": {
            "total_messages": total_messages,
            "total_questions": total_questions,
            "unanswered_count": unanswered_count,
            "unanswered_pct": unanswered_pct,
            "median_first_reply_min": median_first_reply_min,
            "automation_candidate_count": automation_candidate_count,
        },
        "kpi_sparklines": kpi_sparklines,
        "kpi_deltas": kpi_deltas,
        "timeseries": timeseries,
        "categories": categories,
        "category_colors": category_colors,
        "difficulty_by_category": difficulty_by_category,
        "automation_opportunities": automation_opportunities,
        "top_askers": top_askers,
        "summary": summary,
        "date_range": date_range,
        "generated_note": None,
    }


# ---------------------------------------------------------------------------
# render_html()
# ---------------------------------------------------------------------------

_STYLE = """
:root { color-scheme: light dark; }
.viz-root {
  --surface-1: #fcfcfb;
  --page: #f9f9f7;
  --text-primary: #0b0b0b;
  --text-secondary: #52514e;
  --muted: #898781;
  --grid: #e1e0d9;
  --baseline: #c3c2b7;
  --border: rgba(11,11,11,.10);
  --series-1: #2a78d6;
  --series-2: #1baf7a;
  --series-3: #eda100;
  --series-4: #008300;
  --series-5: #4a3aa7;
  --series-6: #e34948;
  --series-7: #e87ba4;
  --series-8: #eb6834;
  --good: #0ca30c;
  background: var(--page);
  color: var(--text-primary);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  min-height: 100vh;
  margin: 0;
  padding: 0 0 3rem 0;
}
@media (prefers-color-scheme: dark) {
  .viz-root {
    --surface-1: #1a1a19;
    --page: #0d0d0d;
    --text-primary: #ffffff;
    --text-secondary: #c3c2b7;
    --grid: #2c2c2a;
    --baseline: #383835;
    --border: rgba(255,255,255,.10);
    --series-1: #3987e5;
    --series-2: #199e70;
    --series-3: #c98500;
    --series-4: #008300;
    --series-5: #9085e9;
    --series-6: #e66767;
    --series-7: #d55181;
    --series-8: #d95926;
  }
}
.viz-root[data-theme="dark"] {
  --surface-1: #1a1a19;
  --page: #0d0d0d;
  --text-primary: #ffffff;
  --text-secondary: #c3c2b7;
  --grid: #2c2c2a;
  --baseline: #383835;
  --border: rgba(255,255,255,.10);
  --series-1: #3987e5;
  --series-2: #199e70;
  --series-3: #c98500;
  --series-4: #008300;
  --series-5: #9085e9;
  --series-6: #e66767;
  --series-7: #d55181;
  --series-8: #d95926;
}
.viz-root[data-theme="light"] {
  --surface-1: #fcfcfb;
  --page: #f9f9f7;
  --text-primary: #0b0b0b;
  --text-secondary: #52514e;
  --grid: #e1e0d9;
  --baseline: #c3c2b7;
  --border: rgba(11,11,11,.10);
  --series-1: #2a78d6;
  --series-2: #1baf7a;
  --series-3: #eda100;
  --series-4: #008300;
  --series-5: #4a3aa7;
  --series-6: #e34948;
  --series-7: #e87ba4;
  --series-8: #eb6834;
}
* { box-sizing: border-box; }
.viz-container { max-width: 1080px; margin: 0 auto; padding: 1.5rem; }
.site-header {
  position: sticky; top: 0; z-index: 30; background: var(--surface-1);
  border-bottom: 1px solid var(--border);
}
.site-header-inner {
  max-width: 1080px; margin: 0 auto; padding: .85rem 1.5rem;
  display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap;
}
.site-header-title h1 { font-size: 1.2rem; margin: 0 0 .2rem 0; }
.site-header-title .meta { color: var(--text-secondary); font-size: .8rem; }
.site-nav { display: flex; gap: 1rem; font-size: .85rem; }
.site-nav a { color: var(--text-secondary); text-decoration: none; }
.site-nav a.active { color: var(--text-primary); font-weight: 600; }
.site-header-actions { display: flex; gap: .5rem; align-items: center; }
.theme-toggle {
  background: var(--surface-1); color: var(--text-primary); border: 1px solid var(--border);
  border-radius: 6px; padding: .4rem .75rem; font-size: .85rem; cursor: pointer;
}
.kpi-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: .75rem; margin-bottom: 1.75rem; }
.kpi-tile { background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px; padding: .9rem 1rem; }
.kpi-tile .value { font-size: 1.6rem; font-weight: 600; color: var(--text-primary); line-height: 1.15; }
.kpi-tile .label { font-size: .78rem; color: var(--muted); margin-top: .2rem; }
.kpi-tile .kpi-delta { font-size: .74rem; color: var(--text-secondary); margin-top: .3rem; }
.kpi-tile .kpi-subline { font-size: .74rem; color: var(--text-secondary); margin-top: .3rem; }
.kpi-spark { width: 100%; height: 28px; margin-top: .4rem; }
.viz-section { background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px; padding: 1.1rem 1.2rem; margin-bottom: 1.25rem; }
.viz-section h2 { font-size: 1rem; margin: 0 0 .85rem 0; color: var(--text-primary); }
.empty-note { color: var(--muted); font-size: .85rem; font-style: italic; }
.legend { display: flex; gap: 1.25rem; font-size: .8rem; color: var(--text-secondary); margin-bottom: .5rem; }
.legend .swatch { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: .4rem; vertical-align: middle; }
.viz-tooltip {
  position: absolute; pointer-events: none; background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 6px; padding: .4rem .6rem; font-size: .75rem; color: var(--text-primary); box-shadow: 0 2px 8px rgba(0,0,0,.15);
  white-space: nowrap; opacity: 0; transition: opacity .08s ease; z-index: 10;
}
.chart-wrap { position: relative; }
table.viz-table { width: 100%; border-collapse: collapse; font-size: .85rem; }
table.viz-table th, table.viz-table td { text-align: left; padding: .45rem .5rem; border-bottom: 1px solid var(--border); color: var(--text-primary); }
table.viz-table th { color: var(--text-secondary); font-weight: 600; font-size: .78rem; text-transform: uppercase; letter-spacing: .02em; }
.candidate-tag { color: var(--good); font-size: .72rem; font-weight: 600; margin-left: .35rem; }
.viz-footer { color: var(--muted); font-size: .78rem; margin-top: 2rem; text-align: center; }
.summary-text { color: var(--text-primary); font-size: .92rem; line-height: 1.5; }
.cat-chip-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: .4rem; vertical-align: middle; }
.diff-dots { display: inline-flex; align-items: center; gap: 2px; }
.diff-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--grid); display: inline-block; }
.diff-dot.filled { background: var(--text-secondary); }
.auto-meter { display: inline-flex; align-items: center; gap: .4rem; white-space: nowrap; }
.auto-meter-track { display: inline-block; width: 60px; height: 6px; border-radius: 3px; background: var(--grid); overflow: hidden; }
.auto-meter-fill { display: block; height: 100%; border-radius: 3px; background: var(--text-secondary); }
"""

_SCRIPT = r"""
(function () {
  var model = JSON.parse(document.getElementById('model').textContent);
  var root = document.querySelector('.viz-root');

  function css(name) {
    return getComputedStyle(root).getPropertyValue(name).trim();
  }

  function categoryColor(name) {
    var slot = (model.category_colors && model.category_colors[name]) || 'muted';
    return css(slot === 'muted' ? '--muted' : '--' + slot);
  }

  // Round-number axis ticks: 1/2/5 x 10^k steps. Returns an ascending array
  // of tick values from 0 to a "nice" max >= maxVal.
  function niceTicks(maxVal, n) {
    if (!maxVal || maxVal <= 0) return [0];
    n = n || 4;
    var rawStep = maxVal / n;
    var mag = Math.pow(10, Math.floor(Math.log(rawStep) / Math.LN10));
    var residual = rawStep / mag;
    var step;
    if (residual > 5) step = 10 * mag;
    else if (residual > 2) step = 5 * mag;
    else if (residual > 1) step = 2 * mag;
    else step = mag;
    var top = Math.ceil(maxVal / step) * step;
    var ticks = [];
    for (var v = 0; v <= top + step / 1e6; v += step) {
      ticks.push(Math.round(v * 1e6) / 1e6);
    }
    return ticks;
  }

  function el(tag, attrs, parent) {
    var e = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }

  function makeTooltip(container) {
    var tip = document.createElement('div');
    tip.className = 'viz-tooltip';
    container.appendChild(tip);
    return tip;
  }

  function showTip(tip, container, x, y, htmlContent) {
    tip.innerHTML = htmlContent;
    tip.style.opacity = '1';
    var rect = container.getBoundingClientRect();
    var left = x + 12;
    if (left + 160 > rect.width) left = x - 160;
    tip.style.left = left + 'px';
    tip.style.top = Math.max(0, y - 10) + 'px';
  }

  function hideTip(tip) { tip.style.opacity = '0'; }

  // Centered rolling mean; window shrinks near the edges of the series.
  function rollingMean(values, window) {
    var n = values.length, half = Math.floor(window / 2), out = [];
    for (var i = 0; i < n; i++) {
      var lo = Math.max(0, i - half), hi = Math.min(n - 1, i + half);
      var sum = 0, cnt = 0;
      for (var j = lo; j <= hi; j++) { sum += values[j]; cnt++; }
      out.push(sum / cnt);
    }
    return out;
  }

  // ---- Line chart: volume over time (7d rolling mean primary, raw faint) ----
  function renderLineChart(containerId, series) {
    var container = document.getElementById(containerId);
    if (!series || !series.length) {
      container.innerHTML = '<p class="empty-note">No time series data available.</p>';
      return;
    }
    var w = container.clientWidth || 900, h = 280;
    var padL = 44, padR = 16, padT = 12, padB = 28;
    var plotW = w - padL - padR, plotH = h - padT - padB;
    var n = series.length;

    var messagesRaw = series.map(function (d) { return d.messages; });
    var questionsRaw = series.map(function (d) { return d.questions; });
    var messagesAvg = rollingMean(messagesRaw, 7);
    var questionsAvg = rollingMean(questionsRaw, 7);
    var maxRaw = Math.max.apply(null, messagesRaw.concat(questionsRaw).concat(messagesAvg).concat(questionsAvg, [1]));
    var ticks = niceTicks(maxRaw, 4);
    var niceMax = ticks[ticks.length - 1] || 1;

    function x(i) { return padL + (n <= 1 ? 0 : (i / (n - 1)) * plotW); }
    function y(v) { return padT + plotH - (v / niceMax) * plotH; }

    var svg = el('svg', { width: w, height: h, viewBox: '0 0 ' + w + ' ' + h, style: 'display:block' });

    var gridColor = css('--grid'), baseline = css('--baseline'), textColor = css('--text-secondary');
    ticks.forEach(function (t) {
      var gy = y(t);
      el('line', { x1: padL, x2: w - padR, y1: gy, y2: gy, stroke: gridColor, 'stroke-width': 1 }, svg);
      el('text', {
        x: padL - 8, y: gy + 4, 'text-anchor': 'end', fill: textColor, 'font-size': 11,
        style: 'font-variant-numeric:tabular-nums'
      }, svg).textContent = t.toLocaleString();
    });
    el('line', { x1: padL, x2: w - padR, y1: padT + plotH, y2: padT + plotH, stroke: baseline, 'stroke-width': 1 }, svg);

    var xTickIdxs = [];
    for (var ti = 0; ti <= 5; ti++) {
      var idx = Math.round(ti * (n - 1) / 5);
      if (xTickIdxs.indexOf(idx) === -1) xTickIdxs.push(idx);
    }
    xTickIdxs.forEach(function (idx) {
      el('text', {
        x: x(idx), y: padT + plotH + 18, 'text-anchor': 'middle', fill: textColor, 'font-size': 11
      }, svg).textContent = series[idx].date;
    });

    function pathFor(values) {
      return values.map(function (v, i) { return (i === 0 ? 'M' : 'L') + x(i) + ',' + y(v); }).join(' ');
    }

    // soft area fill under the messages average
    var areaPath = pathFor(messagesAvg) + ' L' + x(n - 1) + ',' + y(0) + ' L' + x(0) + ',' + y(0) + ' Z';
    el('path', { d: areaPath, fill: css('--series-1'), opacity: 0.08, stroke: 'none' }, svg);

    // raw daily, thin + faint, same hue as its average
    el('path', { d: pathFor(messagesRaw), fill: 'none', stroke: css('--series-1'), 'stroke-width': 1, opacity: 0.35 }, svg);
    el('path', { d: pathFor(questionsRaw), fill: 'none', stroke: css('--series-2'), 'stroke-width': 1, opacity: 0.35 }, svg);

    // rolling average, the primary encoding
    el('path', { d: pathFor(messagesAvg), fill: 'none', stroke: css('--series-1'), 'stroke-width': 2 }, svg);
    el('path', { d: pathFor(questionsAvg), fill: 'none', stroke: css('--series-2'), 'stroke-width': 2 }, svg);

    var crosshair = el('line', { x1: 0, x2: 0, y1: padT, y2: padT + plotH, stroke: baseline, 'stroke-width': 1, opacity: 0 }, svg);
    var hitArea = el('rect', { x: padL, y: padT, width: plotW, height: plotH, fill: 'transparent' }, svg);

    container.innerHTML = '';
    container.appendChild(svg);
    var tip = makeTooltip(container);

    hitArea.addEventListener('mousemove', function (evt) {
      var rect = svg.getBoundingClientRect();
      var mx = evt.clientX - rect.left;
      var idx = Math.round(((mx - padL) / (plotW || 1)) * (n - 1));
      idx = Math.max(0, Math.min(n - 1, idx));
      var d = series[idx];
      crosshair.setAttribute('x1', x(idx));
      crosshair.setAttribute('x2', x(idx));
      crosshair.setAttribute('opacity', 1);
      showTip(tip, container, x(idx), y(Math.max(d.messages, d.questions)),
        '<strong>' + d.date + '</strong><br>Messages: ' + d.messages + ' (avg ' + messagesAvg[idx].toFixed(1) + ')' +
        '<br>Questions: ' + d.questions + ' (avg ' + questionsAvg[idx].toFixed(1) + ')');
    });
    hitArea.addEventListener('mouseleave', function () { crosshair.setAttribute('opacity', 0); hideTip(tip); });
  }

  // ---- Horizontal bar chart: generic (categories, top askers) ----
  // items: [{name, count, ...}]; opts: {colorFn(item), labelFn(item),
  // tooltipFn(item), emptyMessage}
  function renderBarChart(containerId, items, opts) {
    opts = opts || {};
    var container = document.getElementById(containerId);
    if (!items || !items.length) {
      container.innerHTML = '<p class="empty-note">' + (opts.emptyMessage || 'No data available.') + '</p>';
      return;
    }
    var rowH = 28, gap = 2, axisH = 20;
    var w = container.clientWidth || 900;
    var barsH = items.length * (rowH + gap);
    var h = barsH + axisH;
    var padL = 140, padR = 60;
    var plotW = w - padL - padR;
    var maxVal = Math.max.apply(null, items.map(function (c) { return c.count; })) || 1;
    var ticks = niceTicks(maxVal, 3);
    var niceMax = ticks[ticks.length - 1] || 1;

    var svg = el('svg', { width: w, height: h, viewBox: '0 0 ' + w + ' ' + h, style: 'display:block' });
    container.innerHTML = '';
    container.appendChild(svg);
    var tip = makeTooltip(container);

    var gridColor = css('--grid'), textColor = css('--text-secondary');
    ticks.forEach(function (t) {
      var tx = padL + (t / niceMax) * plotW;
      el('line', { x1: tx, x2: tx, y1: 0, y2: barsH, stroke: gridColor, 'stroke-width': 1 }, svg);
      el('text', { x: tx, y: barsH + 14, 'text-anchor': 'middle', fill: textColor, 'font-size': 11 }, svg).textContent = t.toLocaleString();
    });

    var colorFn = opts.colorFn || function () { return css('--series-1'); };
    var labelFn = opts.labelFn || function (c) { return c.count.toLocaleString(); };
    var tooltipFn = opts.tooltipFn || function (c) { return '<strong>' + c.name + '</strong><br>Count: ' + c.count; };

    items.forEach(function (c, i) {
      var barY = i * (rowH + gap);
      var barW = Math.max(2, (c.count / niceMax) * plotW);
      el('text', { x: padL - 10, y: barY + rowH / 2 + 4, 'text-anchor': 'end', fill: css('--text-secondary'), 'font-size': 12 }, svg).textContent = c.name;
      el('rect', { x: padL, y: barY + 3, width: barW, height: rowH - 6, rx: 4, fill: colorFn(c) }, svg);
      el('text', { x: padL + barW + 8, y: barY + rowH / 2 + 4, fill: css('--text-primary'), 'font-size': 12 }, svg).textContent = labelFn(c);
      var hit = el('rect', { x: padL, y: barY, width: Math.max(barW, 30), height: rowH, fill: 'transparent' }, svg);
      hit.addEventListener('mousemove', function (evt) {
        var rect = svg.getBoundingClientRect();
        showTip(tip, container, evt.clientX - rect.left, barY + rowH / 2, tooltipFn(c));
      });
      hit.addEventListener('mouseleave', function () { hideTip(tip); });
    });
  }

  // ---- Bubble quadrant chart: automatable % vs difficulty ----
  function renderQuadrantChart(containerId, opportunities) {
    var container = document.getElementById(containerId);
    if (!opportunities || !opportunities.length) {
      container.innerHTML = '<p class="empty-note">No automation opportunity data available.</p>';
      return;
    }
    var w = container.clientWidth || 900, h = 340;
    var padL = 46, padR = 20, padT = 26, padB = 40;
    var plotW = w - padL - padR, plotH = h - padT - padB;
    var maxCount = Math.max.apply(null, opportunities.map(function (o) { return o.count; })) || 1;
    var rmin = 6, rmax = 22;

    var svg = el('svg', { width: w, height: h, viewBox: '0 0 ' + w + ' ' + h, style: 'display:block' });
    var gridColor = css('--grid'), baseline = css('--baseline'), textColor = css('--text-secondary'), mutedColor = css('--muted');

    function xScale(pct) { return padL + (pct / 100) * plotW; }
    function yScale(diff) { return padT + ((diff - 1) / 4) * plotH; } // 1 (easy) top .. 5 (hard) bottom

    niceTicks(100, 5).forEach(function (t) {
      if (t > 100) return;
      var tx = xScale(t);
      el('line', { x1: tx, x2: tx, y1: padT, y2: padT + plotH, stroke: gridColor, 'stroke-width': 1 }, svg);
      el('text', { x: tx, y: padT + plotH + 16, 'text-anchor': 'middle', fill: textColor, 'font-size': 11 }, svg).textContent = t + '%';
    });
    for (var dv = 1; dv <= 5; dv++) {
      el('text', { x: padL - 8, y: yScale(dv) + 4, 'text-anchor': 'end', fill: textColor, 'font-size': 11 }, svg).textContent = dv;
    }
    el('text', { x: 4, y: padT - 10, fill: textColor, 'font-size': 11 }, svg).textContent = '▲ easier to automate';
    el('text', { x: padL + plotW / 2, y: h - 4, 'text-anchor': 'middle', fill: textColor, 'font-size': 11 }, svg).textContent = 'Automatable %';

    // quadrant guides
    el('line', { x1: xScale(50), x2: xScale(50), y1: padT, y2: padT + plotH, stroke: baseline, 'stroke-width': 1, 'stroke-dasharray': '3,3' }, svg);
    el('line', { x1: padL, x2: padL + plotW, y1: yScale(3), y2: yScale(3), stroke: baseline, 'stroke-width': 1, 'stroke-dasharray': '3,3' }, svg);
    el('text', { x: padL + plotW - 4, y: padT + 12, 'text-anchor': 'end', fill: mutedColor, 'font-size': 10, 'font-style': 'italic' }, svg).textContent = 'automate first';

    container.innerHTML = '';
    container.appendChild(svg);
    var tip = makeTooltip(container);

    var placed = [];
    opportunities.forEach(function (o) {
      var cx = xScale(o.automatable_pct != null ? o.automatable_pct : 0);
      var cy = yScale(o.avg_difficulty != null ? o.avg_difficulty : 3);
      var r = Math.max(rmin, rmax * Math.sqrt(o.count / maxCount));
      var color = categoryColor(o.category);
      el('circle', { cx: cx, cy: cy, r: r, fill: color, opacity: 0.85 }, svg);

      var naturalX = cx + r + 5, naturalY = cy + 4, anchor = 'start';
      if (naturalX + o.category.length * 6 > padL + plotW) { naturalX = cx - r - 5; anchor = 'end'; }
      var label = el('text', { x: naturalX, y: naturalY, 'text-anchor': anchor, fill: textColor, 'font-size': 11 }, svg);
      label.textContent = o.category;
      placed.push({ el: label, cx: cx, cy: cy, naturalX: naturalX, naturalY: naturalY, anchor: anchor, r: r });

      var hit = el('circle', { cx: cx, cy: cy, r: r + 6, fill: 'transparent' }, svg);
      hit.addEventListener('mousemove', function (evt) {
        var rect = svg.getBoundingClientRect();
        var diffStr = o.avg_difficulty != null ? o.avg_difficulty.toFixed(1) : 'n/a';
        var autoStr = o.automatable_pct != null ? o.automatable_pct.toFixed(0) + '%' : 'n/a';
        showTip(tip, container, evt.clientX - rect.left, evt.clientY - rect.top,
          '<strong>' + o.category + '</strong><br>Count: ' + o.count + '<br>Avg difficulty: ' + diffStr +
          '<br>Automatable: ' + autoStr + (o.is_candidate ? '<br><em>Automation candidate</em>' : ''));
      });
      hit.addEventListener('mouseleave', function () { hideTip(tip); });
    });

    // collision avoidance: measure, sort by natural y, push apart, clamp, leader lines
    var labelH = 13;
    var boxes = placed.map(function (p) {
      var bboxHeight = labelH;
      try { bboxHeight = Math.max(p.el.getBBox().height, labelH); } catch (e) { /* detached/unsupported */ }
      return { p: p, y: p.naturalY, height: bboxHeight };
    });
    boxes.sort(function (a, b) { return a.y - b.y; });
    for (var i = 1; i < boxes.length; i++) {
      var minY = boxes[i - 1].y + boxes[i - 1].height + 2;
      if (boxes[i].y < minY) boxes[i].y = minY;
    }
    boxes.forEach(function (b) {
      var clampedY = Math.max(padT + 8, Math.min(padT + plotH - 2, b.y));
      b.p.el.setAttribute('y', clampedY);
      if (Math.abs(clampedY - b.p.naturalY) > 8) {
        el('line', {
          x1: b.p.cx + (b.p.anchor === 'end' ? -b.p.r : b.p.r), y1: b.p.cy,
          x2: b.p.naturalX + (b.p.anchor === 'end' ? 2 : -2), y2: clampedY - 3,
          stroke: mutedColor, 'stroke-width': 1
        }, svg);
      }
    });
  }

  // ---- Sparkline: small trend line inside a KPI tile ----
  function renderSparkline(containerId, values, colorVar) {
    var container = document.getElementById(containerId);
    if (!container) return;
    if (!values || values.length < 2) { container.innerHTML = ''; return; }
    var w = container.clientWidth || 90, h = 28, pad = 2;
    var maxVal = Math.max.apply(null, values);
    var minVal = Math.min.apply(null, values);
    var range = (maxVal - minVal) || 1;
    var n = values.length;
    function x(i) { return pad + (n <= 1 ? 0 : (i / (n - 1)) * (w - pad * 2)); }
    function y(v) { return h - pad - ((v - minVal) / range) * (h - pad * 2); }

    var svg = el('svg', { width: w, height: h, viewBox: '0 0 ' + w + ' ' + h, style: 'display:block' });
    var d = values.map(function (v, i) { return (i === 0 ? 'M' : 'L') + x(i) + ',' + y(v); }).join(' ');
    el('path', { d: d, fill: 'none', stroke: css(colorVar), 'stroke-width': 1.5 }, svg);
    el('circle', { cx: x(n - 1), cy: y(values[n - 1]), r: 2.2, fill: css(colorVar) }, svg);
    var hit = el('rect', { x: 0, y: 0, width: w, height: h, fill: 'transparent' }, svg);

    container.innerHTML = '';
    container.appendChild(svg);
    var tip = makeTooltip(container);

    hit.addEventListener('mousemove', function (evt) {
      var rect = svg.getBoundingClientRect();
      var mx = evt.clientX - rect.left;
      var idx = Math.max(0, Math.min(n - 1, Math.round(((mx - pad) / ((w - pad * 2) || 1)) * (n - 1))));
      showTip(tip, container, x(idx), y(values[idx]), (n - idx) + ' day(s) ago: ' + values[idx].toLocaleString());
    });
    hit.addEventListener('mouseleave', function () { hideTip(tip); });
  }

  function init() {
    renderLineChart('chart-volume', model.timeseries);

    var categoryItems = (model.categories || []).map(function (c) {
      return { name: c.name, count: c.count, pct: c.pct };
    });
    renderBarChart('chart-categories', categoryItems, {
      colorFn: function (c) { return categoryColor(c.name); },
      labelFn: function (c) { return c.count.toLocaleString() + ' (' + c.pct.toFixed(1) + '%)'; },
      tooltipFn: function (c) { return '<strong>' + c.name + '</strong><br>Count: ' + c.count.toLocaleString() + '<br>' + c.pct.toFixed(1) + '%'; },
      emptyMessage: 'No categorized questions available.'
    });

    var hasDifficulty = (model.difficulty_by_category || []).some(function (r) { return r.avg_difficulty != null; });
    if (hasDifficulty) {
      renderQuadrantChart('chart-automation', model.automation_opportunities);
    }

    var askerItems = (model.top_askers || []).map(function (a) {
      return { name: a.display_name || a.user, count: a.count };
    });
    renderBarChart('chart-top-askers', askerItems, {
      colorFn: function () { return css('--series-1'); },
      labelFn: function (a) { return a.count.toLocaleString(); },
      tooltipFn: function (a) { return '<strong>' + a.name + '</strong><br>Count: ' + a.count.toLocaleString(); },
      emptyMessage: 'No question askers recorded.'
    });

    renderSparkline('spark-messages', model.kpi_sparklines && model.kpi_sparklines.messages, '--series-1');
    renderSparkline('spark-questions', model.kpi_sparklines && model.kpi_sparklines.questions, '--series-2');
  }

  document.addEventListener('DOMContentLoaded', init);
  if (document.readyState !== 'loading') init();

  var toggle = document.getElementById('theme-toggle');
  function effectiveTheme() {
    var explicit = root.getAttribute('data-theme');
    if (explicit) return explicit;
    return (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
  }
  if (toggle) {
    toggle.textContent = effectiveTheme() === 'dark' ? 'Light mode' : 'Dark mode';
    toggle.addEventListener('click', function () {
      var next = effectiveTheme() === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      toggle.textContent = next === 'dark' ? 'Light mode' : 'Dark mode';
      setTimeout(init, 0);
    });
  }
})();
"""


def _kpi_tile(
    value: str,
    label: str,
    delta_label: str = "",
    spark_id: Optional[str] = None,
    subline: str = "",
) -> str:
    extra = ""
    if delta_label:
        extra += f'<div class="kpi-delta">{html.escape(delta_label)}</div>'
    if subline:
        extra += f'<div class="kpi-subline">{html.escape(subline)}</div>'
    if spark_id:
        extra += f'<div id="{spark_id}" class="kpi-spark chart-wrap"></div>'
    return (
        f'<div class="kpi-tile"><div class="value">{html.escape(value)}</div>'
        f'<div class="label">{html.escape(label)}</div>{extra}</div>'
    )


def _delta_label(delta: Optional[dict]) -> str:
    if not delta or delta.get("pct") is None:
        return ""
    pct = delta["pct"]
    arrow = "▲" if pct >= 0 else "▼"
    window = delta.get("window", 30)
    return f"{arrow} {abs(pct):.1f}% vs prior {window}d"


def _difficulty_dots_html(avg_difficulty: Optional[float]) -> str:
    if avg_difficulty is None:
        return '<span class="empty-note">n/a</span>'
    filled = max(0, min(5, round(avg_difficulty)))
    dots = "".join(
        f'<span class="diff-dot{" filled" if i < filled else ""}"></span>' for i in range(5)
    )
    return f'<span class="diff-dots" title="{avg_difficulty:.1f}/5">{dots}</span>'


def _automatable_meter_html(pct: Optional[float]) -> str:
    if pct is None:
        return '<span class="empty-note">n/a</span>'
    pct_clamped = max(0.0, min(100.0, pct))
    fill_w = 60.0 * pct_clamped / 100.0
    return (
        '<span class="auto-meter">'
        f'<span class="auto-meter-track"><span class="auto-meter-fill" '
        f'style="width:{fill_w:.1f}px"></span></span>'
        f'<span>{pct:.1f}%</span></span>'
    )


def _category_chip_html(category: str, category_colors: dict) -> str:
    slot = category_colors.get(category, "muted")
    color_var = "--muted" if slot == "muted" else f"--{slot}"
    dot = f'<span class="cat-chip-dot" style="background:var({color_var})"></span>'
    return f'{dot}{html.escape(str(category))}'


def render_html(model: dict, generated_at: str) -> str:
    kpis = model.get("kpis") or {}
    date_range = model.get("date_range") or {}
    date_range_str = (
        f"{date_range.get('start')} to {date_range.get('end')}"
        if date_range.get("start") and date_range.get("end")
        else "n/a"
    )

    unanswered_pct = kpis.get("unanswered_pct")
    unanswered_count = kpis.get("unanswered_count", 0)
    total_questions = kpis.get("total_questions", 0)
    unanswered_label = (
        f"{unanswered_count} ({unanswered_pct:.1f}%)"
        if unanswered_pct is not None
        else str(unanswered_count)
    )
    median_min = kpis.get("median_first_reply_min")
    median_label = f"{median_min:.1f} min" if median_min is not None else "n/a"

    kpi_deltas = model.get("kpi_deltas") or {}

    kpi_html = "".join([
        _kpi_tile(
            str(kpis.get("total_messages", 0)), "Messages",
            delta_label=_delta_label(kpi_deltas.get("messages")),
            spark_id="spark-messages",
        ),
        _kpi_tile(
            str(kpis.get("total_questions", 0)), "Questions",
            delta_label=_delta_label(kpi_deltas.get("questions")),
            spark_id="spark-questions",
        ),
        _kpi_tile(
            unanswered_label, "Unanswered",
            subline=f"{unanswered_count} of {total_questions} questions",
        ),
        _kpi_tile(median_label, "Median first reply"),
        _kpi_tile(str(kpis.get("automation_candidate_count", 0)), "Automation candidates"),
    ])

    difficulty_by_category = model.get("difficulty_by_category") or []
    has_difficulty = any(r.get("avg_difficulty") is not None for r in difficulty_by_category)
    category_colors = model.get("category_colors") or {}

    opportunities = model.get("automation_opportunities") or []
    if opportunities:
        candidate_tag = '<span class="candidate-tag">candidate</span>'
        row_parts = []
        for o in opportunities:
            tag = candidate_tag if o.get("is_candidate") else ""
            row_parts.append(
                f"<tr><td>{_category_chip_html(o['category'], category_colors)}{tag}</td>"
                f"<td>{o['count']}</td>"
                f"<td>{_difficulty_dots_html(o['avg_difficulty'])}</td>"
                f"<td>{_automatable_meter_html(o['automatable_pct'])}</td>"
                f"<td>{html.escape(o['rationale'])}</td></tr>"
            )
        table_rows = "".join(row_parts)
    else:
        table_rows = '<tr><td colspan="5" class="empty-note">No automation opportunity data.</td></tr>'

    scatter_section_html = (
        '<div id="chart-automation" class="chart-wrap" style="min-height:340px"></div>'
        if has_difficulty
        else '<p class="empty-note">Difficulty scoring requires the LLM analysis '
             '(set ANTHROPIC_API_KEY). Automation opportunities below are ranked by volume only.</p>'
    )

    askers_html = '<div id="chart-top-askers" class="chart-wrap"></div>'

    summary = model.get("summary") or ""
    summary_section = (
        f'<div class="viz-section"><h2>Summary</h2><p class="summary-text">{html.escape(summary)}</p></div>'
        if summary
        else ""
    )

    model_json = json.dumps(model, default=str)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>#partnerships — Question Analysis</title>
<style>{_STYLE}</style>
</head>
<body>
<div class="viz-root">

<header class="site-header">
  <div class="site-header-inner">
    <div class="site-header-title">
      <h1>#partnerships — Question Analysis</h1>
      <div class="meta">Date range: {html.escape(date_range_str)} &middot; Updated {html.escape(generated_at)}</div>
    </div>
    <nav class="site-nav">
      <a href="./index.html" class="active">Overview</a>
      <a href="./automation.html">Automation</a>
    </nav>
    <div class="site-header-actions">
      <button id="spend-btn" class="theme-toggle" type="button" hidden>Spend</button>
      <button id="theme-toggle" class="theme-toggle" type="button">Dark mode</button>
    </div>
  </div>
</header>

<div class="viz-container">

  <div class="kpi-row">
    {kpi_html}
  </div>

  <div class="viz-section">
    <h2>Volume over time</h2>
    <div class="legend">
      <span><span class="swatch" style="background:var(--series-1)"></span>Messages</span>
      <span><span class="swatch" style="background:var(--series-2)"></span>Questions</span>
    </div>
    <div id="chart-volume" class="chart-wrap" style="min-height:280px"></div>
  </div>

  <div class="viz-section">
    <h2>Top question categories</h2>
    <div id="chart-categories" class="chart-wrap"></div>
  </div>

  <div class="viz-section">
    <h2>Automation opportunities</h2>
    <div class="chart-wrap">
      {scatter_section_html}
    </div>
  </div>

  <div class="viz-section">
    <h2>Automation opportunities (ranked)</h2>
    <table class="viz-table">
      <thead><tr><th>Category</th><th>Count</th><th>Avg difficulty</th><th>Automatable %</th><th>Rationale</th></tr></thead>
      <tbody>{table_rows}</tbody>
    </table>
  </div>

  <div class="viz-section">
    <h2>Top askers</h2>
    {askers_html}
  </div>

  {summary_section}

  <div class="viz-footer">
    Data sourced from committed <code>data/analysis/*.json</code> files.<br>
    Generated at {html.escape(generated_at)}.
  </div>

</div>
</div>

<script type="application/json" id="model">{model_json}</script>
<script>{_SCRIPT}</script>
</body>
</html>
"""
