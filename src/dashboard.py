"""Pure aggregation + self-contained HTML rendering for the static dashboard.

`aggregate()` takes a list of loaded analysis JSON dicts (each the output of
`src.analyze.analyze()`, possibly round-tripped through JSON) and produces a
plain-dict "dashboard model". `render_html()` turns that model into a
complete, dependency-free HTML document (inline CSS + inline SVG/JS charts,
no CDNs/network calls).
"""
from __future__ import annotations

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

    return {
        "kpis": {
            "total_messages": total_messages,
            "total_questions": total_questions,
            "unanswered_count": unanswered_count,
            "unanswered_pct": unanswered_pct,
            "median_first_reply_min": median_first_reply_min,
            "automation_candidate_count": automation_candidate_count,
        },
        "timeseries": timeseries,
        "categories": categories,
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
}
* { box-sizing: border-box; }
.viz-container { max-width: 1080px; margin: 0 auto; padding: 1.5rem; }
.viz-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; margin-bottom: 1.5rem; }
.viz-header h1 { font-size: 1.35rem; margin: 0 0 .25rem 0; }
.viz-header .meta { color: var(--text-secondary); font-size: .85rem; }
.theme-toggle {
  background: var(--surface-1); color: var(--text-primary); border: 1px solid var(--border);
  border-radius: 6px; padding: .4rem .75rem; font-size: .85rem; cursor: pointer;
}
.kpi-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: .75rem; margin-bottom: 1.75rem; }
.kpi-tile { background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px; padding: .9rem 1rem; }
.kpi-tile .value { font-size: 1.6rem; font-weight: 600; color: var(--text-primary); line-height: 1.15; }
.kpi-tile .label { font-size: .78rem; color: var(--muted); margin-top: .2rem; }
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
.asker-bar-row { display: flex; align-items: center; gap: .6rem; margin-bottom: .4rem; font-size: .82rem; }
.asker-bar-row .name { width: 130px; color: var(--text-secondary); flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.asker-bar-track { flex: 1; background: var(--grid); border-radius: 3px; height: 10px; overflow: hidden; }
.asker-bar-fill { background: var(--series-1); height: 100%; border-radius: 3px; }
.asker-bar-row .count { width: 30px; text-align: right; color: var(--text-primary); }
"""

_SCRIPT = r"""
(function () {
  var model = JSON.parse(document.getElementById('model').textContent);
  var root = document.querySelector('.viz-root');

  function css(name) {
    return getComputedStyle(root).getPropertyValue(name).trim();
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

  // ---- Line chart: volume over time ----
  function renderLineChart(containerId, series) {
    var container = document.getElementById(containerId);
    if (!series || !series.length) {
      container.innerHTML = '<p class="empty-note">No time series data available.</p>';
      return;
    }
    var w = container.clientWidth || 900, h = 280;
    var padL = 40, padR = 16, padT = 12, padB = 28;
    var plotW = w - padL - padR, plotH = h - padT - padB;
    var maxVal = 1;
    series.forEach(function (d) { maxVal = Math.max(maxVal, d.messages, d.questions); });
    var n = series.length;
    function x(i) { return padL + (n <= 1 ? 0 : (i / (n - 1)) * plotW); }
    function y(v) { return padT + plotH - (v / maxVal) * plotH; }

    var svg = el('svg', { width: w, height: h, viewBox: '0 0 ' + w + ' ' + h, style: 'display:block' });

    // grid lines + y-axis tick labels
    var gridColor = css('--grid'), baseline = css('--baseline'), textColor = css('--text-secondary');
    for (var g = 0; g <= 4; g++) {
      var gy = padT + (plotH / 4) * g;
      el('line', { x1: padL, x2: w - padR, y1: gy, y2: gy, stroke: gridColor, 'stroke-width': 1 }, svg);
      var tickVal = Math.round(maxVal * (4 - g) / 4);
      var tickLabel = el('text', {
        x: padL - 8, y: gy + 4, 'text-anchor': 'end', fill: textColor, 'font-size': 11,
        style: 'font-variant-numeric:tabular-nums'
      }, svg);
      tickLabel.textContent = tickVal.toLocaleString();
    }
    el('line', { x1: padL, x2: w - padR, y1: padT + plotH, y2: padT + plotH, stroke: baseline, 'stroke-width': 1 }, svg);

    // x-axis date tick labels
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

    function pathFor(key) {
      return series.map(function (d, i) { return (i === 0 ? 'M' : 'L') + x(i) + ',' + y(d[key]); }).join(' ');
    }
    var showMarkers = n <= 20;
    el('path', { d: pathFor('messages'), fill: 'none', stroke: css('--series-1'), 'stroke-width': 2 }, svg);
    el('path', { d: pathFor('questions'), fill: 'none', stroke: css('--series-2'), 'stroke-width': 2 }, svg);
    if (showMarkers) {
      series.forEach(function (d, i) {
        el('circle', { cx: x(i), cy: y(d.messages), r: 4, fill: css('--series-1') }, svg);
        el('circle', { cx: x(i), cy: y(d.questions), r: 4, fill: css('--series-2') }, svg);
      });
    }

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
        '<strong>' + d.date + '</strong><br>Messages: ' + d.messages + '<br>Questions: ' + d.questions);
    });
    hitArea.addEventListener('mouseleave', function () { crosshair.setAttribute('opacity', 0); hideTip(tip); });
  }

  // ---- Horizontal bar chart: categories ----
  function renderBarChart(containerId, categories) {
    var container = document.getElementById(containerId);
    if (!categories || !categories.length) {
      container.innerHTML = '<p class="empty-note">No categorized questions available.</p>';
      return;
    }
    var rowH = 28, gap = 2, axisH = 20;
    var w = container.clientWidth || 900;
    var barsH = categories.length * (rowH + gap);
    var h = barsH + axisH;
    var padL = 140, padR = 50;
    var plotW = w - padL - padR;
    var maxVal = Math.max.apply(null, categories.map(function (c) { return c.count; })) || 1;

    var svg = el('svg', { width: w, height: h, viewBox: '0 0 ' + w + ' ' + h, style: 'display:block' });
    container.innerHTML = '';
    container.appendChild(svg);
    var tip = makeTooltip(container);

    var gridColor = css('--grid'), textColor = css('--text-secondary');
    for (var t = 0; t <= 3; t++) {
      var tickVal = Math.round(maxVal * t / 3);
      var tx = padL + (tickVal / maxVal) * plotW;
      el('line', { x1: tx, x2: tx, y1: 0, y2: barsH, stroke: gridColor, 'stroke-width': 1 }, svg);
      el('text', { x: tx, y: barsH + 14, 'text-anchor': 'middle', fill: textColor, 'font-size': 11 }, svg).textContent = tickVal.toLocaleString();
    }

    categories.forEach(function (c, i) {
      var barY = i * (rowH + gap);
      var barW = Math.max(2, (c.count / maxVal) * plotW);
      el('text', { x: padL - 10, y: barY + rowH / 2 + 4, 'text-anchor': 'end', fill: css('--text-secondary'), 'font-size': 12 }, svg).textContent = c.name;
      var bar = el('rect', { x: padL, y: barY + 3, width: barW, height: rowH - 6, rx: 4, fill: css('--series-1') }, svg);
      el('text', { x: padL + barW + 8, y: barY + rowH / 2 + 4, fill: css('--text-primary'), 'font-size': 12 }, svg).textContent = c.count;
      var hit = el('rect', { x: padL, y: barY, width: Math.max(barW, 30), height: rowH, fill: 'transparent' }, svg);
      hit.addEventListener('mousemove', function (evt) {
        var rect = svg.getBoundingClientRect();
        showTip(tip, container, evt.clientX - rect.left, barY + rowH / 2,
          '<strong>' + c.name + '</strong><br>Count: ' + c.count + '<br>' + c.pct.toFixed(1) + '%');
      });
      hit.addEventListener('mouseleave', function () { hideTip(tip); });
    });
  }

  // ---- Scatter: automation opportunities ----
  function renderScatterChart(containerId, opportunities, hasDifficulty) {
    var container = document.getElementById(containerId);
    if (!opportunities || !opportunities.length) {
      container.innerHTML = '<p class="empty-note">No automation opportunity data available.</p>';
      return;
    }
    var w = container.clientWidth || 900, h = 300;
    var padL = 50, padR = 30, padT = 20, padB = 36;
    var plotW = w - padL - padR, plotH = h - padT - padB;
    var maxCount = Math.max.apply(null, opportunities.map(function (o) { return o.count; })) || 1;

    var svg = el('svg', { width: w, height: h, viewBox: '0 0 ' + w + ' ' + h, style: 'display:block' });
    var gridColor = css('--grid'), textColor = css('--text-secondary');

    // x-axis: volume ticks + gridlines
    for (var t = 0; t <= 3; t++) {
      var tickVal = Math.round(maxCount * t / 3);
      var tx = padL + (tickVal / maxCount) * plotW;
      el('line', { x1: tx, x2: tx, y1: padT, y2: padT + plotH, stroke: gridColor, 'stroke-width': 1 }, svg);
      el('text', { x: tx, y: padT + plotH + 16, 'text-anchor': 'middle', fill: textColor, 'font-size': 11 }, svg).textContent = tickVal.toLocaleString();
    }
    el('text', { x: padL, y: h - 4, fill: textColor, 'font-size': 11 }, svg).textContent = 'Volume (count)';

    // y-axis: difficulty ticks (1-5), only when difficulty scoring is available
    if (hasDifficulty) {
      for (var dv = 1; dv <= 5; dv++) {
        var dy = padT + ((dv - 1) / 4) * plotH;
        el('text', { x: padL - 8, y: dy + 4, 'text-anchor': 'end', fill: textColor, 'font-size': 11 }, svg).textContent = dv;
      }
    }
    el('text', { x: 8, y: padT + 10, fill: textColor, 'font-size': 11 }, svg).textContent = hasDifficulty ? 'Easier (automate)' : '';

    container.innerHTML = '';
    container.appendChild(svg);
    var tip = makeTooltip(container);

    opportunities.forEach(function (o) {
      var cx = padL + (o.count / maxCount) * plotW;
      var yVal = hasDifficulty && o.avg_difficulty != null ? o.avg_difficulty : 3;
      // invert so easy (1) is near top
      var cy = hasDifficulty ? padT + ((yVal - 1) / 4) * plotH : padT + plotH / 2;
      var r = 8;
      var fill = o.is_candidate ? css('--good') : css('--series-1');
      el('circle', { cx: cx, cy: cy, r: r, fill: fill, opacity: 0.85 }, svg);
      var labelText = o.category + (o.is_candidate ? ' (candidate)' : '');
      var estWidth = labelText.length * 6.2;
      var labelEl;
      if (cx + r + 4 + estWidth > w - 4) {
        labelEl = el('text', { x: cx - r - 4, y: cy + 4, 'text-anchor': 'end', fill: css('--text-secondary'), 'font-size': 11 }, svg);
      } else {
        labelEl = el('text', { x: cx + r + 4, y: cy + 4, fill: css('--text-secondary'), 'font-size': 11 }, svg);
      }
      labelEl.textContent = labelText;
      var hit = el('circle', { cx: cx, cy: cy, r: r + 6, fill: 'transparent' }, svg);
      hit.addEventListener('mousemove', function (evt) {
        var rect = svg.getBoundingClientRect();
        var diffStr = o.avg_difficulty != null ? o.avg_difficulty.toFixed(1) : 'n/a';
        var autoStr = o.automatable_pct != null ? o.automatable_pct.toFixed(0) + '%' : 'n/a';
        showTip(tip, container, evt.clientX - rect.left, evt.clientY - rect.top,
          '<strong>' + o.category + '</strong><br>Count: ' + o.count + '<br>Avg difficulty: ' + diffStr + '<br>Automatable: ' + autoStr);
      });
      hit.addEventListener('mouseleave', function () { hideTip(tip); });
    });
  }

  function init() {
    renderLineChart('chart-volume', model.timeseries);
    renderBarChart('chart-categories', model.categories);
    var hasDifficulty = (model.difficulty_by_category || []).some(function (r) { return r.avg_difficulty != null; });
    if (hasDifficulty) {
      renderScatterChart('chart-automation', model.automation_opportunities, true);
    }
  }

  document.addEventListener('DOMContentLoaded', init);
  if (document.readyState !== 'loading') init();

  var toggle = document.getElementById('theme-toggle');
  if (toggle) {
    toggle.addEventListener('click', function () {
      var current = root.getAttribute('data-theme');
      var next = current === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      toggle.textContent = next === 'dark' ? 'Light mode' : 'Dark mode';
      setTimeout(init, 0);
    });
  }
})();
"""


def _fmt_num(v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.1f}"
    return str(v)


def _kpi_tile(value: str, label: str) -> str:
    return (
        f'<div class="kpi-tile"><div class="value">{html.escape(value)}</div>'
        f'<div class="label">{html.escape(label)}</div></div>'
    )


def render_html(model: dict, generated_at: str) -> str:
    kpis = model.get("kpis") or {}
    date_range = model.get("date_range") or {}
    date_range_str = (
        f"{date_range.get('start')} to {date_range.get('end')}"
        if date_range.get("start") and date_range.get("end")
        else "n/a"
    )

    unanswered_pct = kpis.get("unanswered_pct")
    unanswered_label = (
        f"{kpis.get('unanswered_count', 0)} ({unanswered_pct:.1f}%)"
        if unanswered_pct is not None
        else str(kpis.get("unanswered_count", 0))
    )
    median_min = kpis.get("median_first_reply_min")
    median_label = f"{median_min:.1f} min" if median_min is not None else "n/a"

    kpi_html = "".join([
        _kpi_tile(str(kpis.get("total_messages", 0)), "Messages"),
        _kpi_tile(str(kpis.get("total_questions", 0)), "Questions"),
        _kpi_tile(unanswered_label, "Unanswered"),
        _kpi_tile(median_label, "Median first reply"),
        _kpi_tile(str(kpis.get("automation_candidate_count", 0)), "Automation candidates"),
    ])

    difficulty_by_category = model.get("difficulty_by_category") or []
    has_difficulty = any(r.get("avg_difficulty") is not None for r in difficulty_by_category)

    opportunities = model.get("automation_opportunities") or []
    if opportunities:
        candidate_tag = '<span class="candidate-tag">candidate</span>'
        row_parts = []
        for o in opportunities:
            tag = candidate_tag if o.get("is_candidate") else ""
            automatable_str = _fmt_num(o["automatable_pct"])
            if o["automatable_pct"] is not None:
                automatable_str += "%"
            row_parts.append(
                f"<tr><td>{html.escape(str(o['category']))}{tag}</td>"
                f"<td>{o['count']}</td>"
                f"<td>{_fmt_num(o['avg_difficulty'])}</td>"
                f"<td>{automatable_str}</td>"
                f"<td>{html.escape(o['rationale'])}</td></tr>"
            )
        table_rows = "".join(row_parts)
    else:
        table_rows = '<tr><td colspan="5" class="empty-note">No automation opportunity data.</td></tr>'

    scatter_section_html = (
        '<div id="chart-automation" class="chart-wrap" style="min-height:300px"></div>'
        if has_difficulty
        else '<p class="empty-note">Difficulty scoring requires the LLM analysis '
             '(set ANTHROPIC_API_KEY). Automation opportunities below are ranked by volume only.</p>'
    )

    top_askers = model.get("top_askers") or []
    max_asker_count = max((a.get("count", 0) for a in top_askers), default=0) or 1
    if top_askers:
        askers_html = "".join(
            f'<div class="asker-bar-row"><div class="name">'
            f'{html.escape(str(a.get("display_name") or a.get("user", "")))}</div>'
            f'<div class="asker-bar-track"><div class="asker-bar-fill" '
            f'style="width:{100.0 * a.get("count", 0) / max_asker_count:.1f}%"></div></div>'
            f'<div class="count">{a.get("count", 0)}</div></div>'
            for a in top_askers
        )
    else:
        askers_html = '<p class="empty-note">No question askers recorded.</p>'

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
<div class="viz-root" data-theme="light">
<div class="viz-container">

  <div class="viz-header">
    <div>
      <h1>#partnerships — Question Analysis</h1>
      <div class="meta">Date range: {html.escape(date_range_str)} &middot; Generated at {html.escape(generated_at)}</div>
    </div>
    <button id="theme-toggle" class="theme-toggle" type="button">Dark mode</button>
  </div>

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
