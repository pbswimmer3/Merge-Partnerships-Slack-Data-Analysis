"""Automation deep-dive page: `build_model()` + `render_html()` for
`site/automation.html` (plan.md §4). Mirrors `src/dashboard.py`'s shape but
groups **automatable** questions into subtopic clusters and renders a
question explorer table + notable-opportunity cards. Shares CSS/JS/color
helpers with the overview page via `src.site_common`.
"""
from __future__ import annotations

import html
import json
import re
import statistics
from collections import Counter
from typing import Dict, List, Optional

from src.analyze import _ts_to_date
from src.site_common import (
    STYLE as _STYLE,
    COMMON_JS as _COMMON_JS,
    category_color_map as _category_color_map,
    difficulty_dots_html as _difficulty_dots_html,
    category_chip_html as _category_chip_html,
)


# ---------------------------------------------------------------------------
# build_model()
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def _normalize_subtopic(subtopic: Optional[str]) -> str:
    """lowercase, strip punctuation, collapse whitespace. Exact-match only
    (no fuzzy matching) is used to key clusters."""
    s = (subtopic or "").lower()
    s = _PUNCT_RE.sub("", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def _suggested_fix(rationales: List[str]) -> str:
    text = " ".join(r for r in rationales if r).lower()
    if "doc" in text or "faq" in text:
        return "Docs/FAQ answer"
    if "plan" in text or "pricing" in text:
        return "Self-serve pricing page"
    if "access" in text or "permission" in text:
        return "Access-request workflow"
    if "status" in text or "sync" in text:
        return "Status page / alert bot"
    return "Slack auto-responder"


def _build_questions(raw_questions: List[dict], user_directory: dict) -> List[dict]:
    out = []
    for q in raw_questions or []:
        ts = q.get("ts")
        user = q.get("user")
        category = q.get("llm_category") or q.get("category") or "other"
        lat = q.get("first_reply_latency_sec")
        try:
            lat = float(lat) if lat is not None else None
        except (TypeError, ValueError):
            lat = None
        difficulty = q.get("difficulty")
        try:
            difficulty = float(difficulty) if difficulty is not None else None
        except (TypeError, ValueError):
            difficulty = None
        automatable = q.get("automatable")
        automatable = bool(automatable) if automatable is not None else None
        out.append({
            "ts": ts,
            "date": _ts_to_date(ts),
            "user": user,
            "display_name": (user_directory or {}).get(user, user),
            "text": q.get("text") or "",
            "category": category,
            "subtopic": q.get("subtopic"),
            "difficulty": difficulty,
            "automatable": automatable,
            "rationale": q.get("rationale") or "",
            "reply_count": int(q.get("reply_count", 0) or 0),
            "first_reply_latency_sec": lat,
            "first_reply_min": (lat / 60.0) if lat is not None else None,
        })
    out.sort(key=lambda q: (q["date"] or "", str(q["ts"] or "")), reverse=True)
    return out


def _build_clusters(questions: List[dict], global_median_latency_sec: Optional[float]) -> List[dict]:
    clusters: Dict[tuple, dict] = {}
    for q in questions:
        if not q.get("automatable"):
            continue
        norm = _normalize_subtopic(q.get("subtopic"))
        if not norm:
            continue
        key = (q["category"], norm)
        c = clusters.setdefault(key, {
            "category": q["category"],
            "subtopic": q.get("subtopic"),
            "normalized_subtopic": norm,
            "count": 0,
            "difficulties": [],
            "question_refs": [],
            "example_texts": [],
            "latencies": [],
            "rationales": [],
        })
        c["count"] += 1
        if q.get("difficulty") is not None:
            c["difficulties"].append(q["difficulty"])
        c["question_refs"].append(q["ts"])
        if len(c["example_texts"]) < 3 and q.get("text"):
            c["example_texts"].append(q["text"])
        if q.get("first_reply_latency_sec") is not None:
            c["latencies"].append(q["first_reply_latency_sec"])
        if q.get("rationale"):
            c["rationales"].append(q["rationale"])

    out = []
    for c in clusters.values():
        avg_difficulty = (
            sum(c["difficulties"]) / len(c["difficulties"]) if c["difficulties"] else None
        )
        median_latency_sec = (
            statistics.median(c["latencies"]) if c["latencies"] else global_median_latency_sec
        )
        est_minutes_saved = (
            c["count"] * median_latency_sec / 60.0 if median_latency_sec is not None else None
        )
        out.append({
            "category": c["category"],
            "subtopic": c["subtopic"],
            "normalized_subtopic": c["normalized_subtopic"],
            "count": c["count"],
            "avg_difficulty": avg_difficulty,
            "question_refs": c["question_refs"],
            "example_texts": c["example_texts"],
            "est_minutes_saved": est_minutes_saved,
            "suggested_fix": _suggested_fix(c["rationales"]),
        })
    out.sort(key=lambda c: c["count"], reverse=True)
    return out


def _build_notables(clusters: List[dict], top_n: int = 6) -> List[dict]:
    eligible = [c for c in clusters if c["count"] >= 2]
    scored = []
    for c in eligible:
        diff_for_score = c["avg_difficulty"] if c["avg_difficulty"] is not None else 3.0
        score = c["count"] * (6 - diff_for_score)
        scored.append((score, c))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [c for _, c in scored[:top_n]]


def _build_kpis(questions: List[dict], clusters: List[dict]) -> dict:
    total_questions = len(questions)
    automatable_qs = [q for q in questions if q.get("automatable")]
    automatable_count = len(automatable_qs)
    automatable_pct = (
        100.0 * automatable_count / total_questions if total_questions else 0.0
    )
    total_reply_hours = sum(
        q["first_reply_latency_sec"] for q in automatable_qs
        if q.get("first_reply_latency_sec") is not None
    ) / 3600.0
    category_counts = Counter(q["category"] for q in questions)
    top_category = category_counts.most_common(1)[0][0] if category_counts else None
    return {
        "automatable_count": automatable_count,
        "automatable_pct": automatable_pct,
        "total_questions": total_questions,
        "total_reply_hours": total_reply_hours,
        "cluster_count": len(clusters),
        "top_category": top_category,
    }


def build_model(merged_analysis: dict, user_directory: Optional[dict] = None) -> dict:
    merged_analysis = merged_analysis or {}
    user_directory = user_directory or {}
    raw_questions = merged_analysis.get("questions") or []

    has_llm_fields = any(
        q.get("subtopic") is not None or q.get("difficulty") is not None or q.get("automatable") is not None
        for q in raw_questions
    )

    questions = _build_questions(raw_questions, user_directory)

    all_latencies = [
        q["first_reply_latency_sec"] for q in questions if q.get("first_reply_latency_sec") is not None
    ]
    global_median_latency_sec = statistics.median(all_latencies) if all_latencies else None

    if has_llm_fields:
        clusters = _build_clusters(questions, global_median_latency_sec)
        notables = _build_notables(clusters)
    else:
        clusters = []
        notables = []

    kpis = _build_kpis(questions, clusters)

    total_questions = len(questions)
    other_count = sum(1 for q in questions if q["category"] == "other")
    other_pct = 100.0 * other_count / total_questions if total_questions else 0.0
    other_info = (
        f"{other_pct:.0f}% uncategorized — subtopics below give the real themes"
        if other_pct > 40 else None
    )

    category_name_order = [q["category"] for q in questions]
    category_colors = _category_color_map(category_name_order)
    category_order = list(category_colors.keys())

    return {
        "questions": questions,
        "clusters": clusters,
        "notables": notables,
        "kpis": kpis,
        "category_colors": category_colors,
        "category_order": category_order,
        "has_llm_fields": has_llm_fields,
        "other_info": other_info,
        "generated_note": None,
    }


# ---------------------------------------------------------------------------
# render_html()
# ---------------------------------------------------------------------------

_PAGE_STYLE = """
.info-line { background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px;
  padding: .6rem 1rem; font-size: .85rem; color: var(--text-secondary); margin-bottom: 1.25rem; }
.notable-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(480px, 1fr)); gap: 1rem; }
.notable-card { border: 1px solid var(--border); border-radius: 10px; padding: 1rem; background: var(--page); }
.notable-head { display: flex; align-items: baseline; justify-content: space-between; gap: .5rem; margin-bottom: .4rem; }
.notable-head h3 { font-size: .98rem; margin: 0; color: var(--text-primary); }
.notable-chip { font-size: .78rem; color: var(--text-secondary); white-space: nowrap; }
.notable-meta { display: flex; align-items: center; gap: .9rem; font-size: .82rem; color: var(--text-secondary); margin-bottom: .5rem; }
.notable-est { font-size: .85rem; color: var(--text-primary); margin-bottom: .5rem; }
.fix-tag { display: inline-block; background: var(--grid); color: var(--text-primary); border-radius: 12px;
  padding: .15rem .6rem; font-size: .74rem; margin-bottom: .5rem; }
.notable-example { margin: 0 0 .6rem 0; padding-left: .7rem; border-left: 2px solid var(--border);
  color: var(--text-secondary); font-size: .85rem; font-style: italic; }
.show-matching { font-size: .8rem; color: var(--text-primary); text-decoration: underline; cursor: pointer; }
.explorer-controls { display: flex; flex-wrap: wrap; align-items: center; gap: .6rem; margin-bottom: .85rem; }
.explorer-controls input[type="text"] {
  background: var(--surface-1); color: var(--text-primary); border: 1px solid var(--border);
  border-radius: 6px; padding: .35rem .6rem; font-size: .85rem; min-width: 220px;
}
.explorer-controls label { font-size: .82rem; color: var(--text-secondary); display: flex; align-items: center; gap: .3rem; }
.explorer-controls select {
  background: var(--surface-1); color: var(--text-primary); border: 1px solid var(--border);
  border-radius: 6px; padding: .3rem .4rem; font-size: .82rem;
}
.chip-row { display: flex; flex-wrap: wrap; gap: .4rem; }
.chip-toggle {
  display: inline-flex; align-items: center; border: 1px solid var(--border); border-radius: 12px;
  background: var(--page); color: var(--muted); font-size: .78rem; padding: .2rem .6rem .2rem .5rem;
  cursor: pointer;
}
.chip-toggle.active { color: var(--text-primary); background: var(--surface-1); }
#clear-cluster-filter {
  border: 1px solid var(--border); border-radius: 12px; background: var(--page); color: var(--text-secondary);
  font-size: .78rem; padding: .2rem .6rem; cursor: pointer;
}
.q-text-clamp {
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  max-width: 46ch; line-height: 1.35;
}
table.viz-table tbody tr.q-row { cursor: pointer; }
table.viz-table tbody tr.q-row:hover { background: var(--page); }
tr.q-detail-row td { background: var(--page); }
.q-detail { padding: .3rem 0; font-size: .85rem; line-height: 1.4; color: var(--text-primary); white-space: pre-wrap; }
.q-rationale { margin-top: .4rem; color: var(--text-secondary); font-style: italic; }
.explorer-count { font-size: .82rem; color: var(--muted); margin-bottom: .5rem; }
"""


def _kpi_tile(value: str, label: str, subline: str = "") -> str:
    extra = f'<div class="kpi-subline">{html.escape(subline)}</div>' if subline else ""
    return (
        f'<div class="kpi-tile"><div class="value">{html.escape(value)}</div>'
        f'<div class="label">{html.escape(label)}</div>{extra}</div>'
    )


def _notable_card_html(cluster: dict, category_colors: dict) -> str:
    subtopic = cluster.get("subtopic") or "(unlabeled)"
    est_minutes = cluster.get("est_minutes_saved")
    est_hours_label = f"≈ {est_minutes / 60.0:.1f} h of reply time" if est_minutes is not None else "n/a"
    example = cluster.get("example_texts") or []
    example_html = f'<blockquote class="notable-example">“{html.escape(example[0])}”</blockquote>' if example else ""
    return (
        '<div class="notable-card">'
        '<div class="notable-head">'
        f'<h3>{html.escape(subtopic)}</h3>'
        f'<span class="notable-chip">{_category_chip_html(cluster["category"], category_colors)}</span>'
        '</div>'
        '<div class="notable-meta">'
        f'<span>{cluster["count"]} questions</span>'
        f'{_difficulty_dots_html(cluster.get("avg_difficulty"))}'
        '</div>'
        f'<div class="notable-est">{est_hours_label}</div>'
        f'<div><span class="fix-tag">{html.escape(cluster.get("suggested_fix") or "")}</span></div>'
        f'{example_html}'
        f'<a href="#question-table" class="show-matching" '
        f'data-category="{html.escape(cluster["category"])}" '
        f'data-subtopic="{html.escape(cluster["normalized_subtopic"])}">Show matching questions →</a>'
        '</div>'
    )


def render_html(model: dict, generated_at: str) -> str:
    kpis = model.get("kpis") or {}
    category_colors = model.get("category_colors") or {}
    has_llm_fields = bool(model.get("has_llm_fields"))
    notables = model.get("notables") or []
    other_info = model.get("other_info")

    kpi_html = "".join([
        _kpi_tile(
            str(kpis.get("automatable_count", 0)), "Automatable questions",
            subline=f"{kpis.get('automatable_pct', 0.0):.1f}% of {kpis.get('total_questions', 0)} questions",
        ),
        _kpi_tile(f"{kpis.get('total_reply_hours', 0.0):.1f} h", "Reply time on automatable Qs"),
        _kpi_tile(str(kpis.get("cluster_count", 0)), "Question clusters"),
        _kpi_tile(str(kpis.get("top_category") or "n/a"), "Top category"),
    ])

    other_info_html = (
        f'<div class="info-line">{html.escape(other_info)}</div>' if other_info else ""
    )

    if not has_llm_fields:
        notables_section = ""
    elif notables:
        cards = "".join(_notable_card_html(c, category_colors) for c in notables)
        notables_section = (
            '<div class="viz-section">'
            '<h2>Notable opportunities</h2>'
            f'<div class="notable-grid">{cards}</div>'
            '</div>'
        )
    else:
        notables_section = (
            '<div class="viz-section"><h2>Notable opportunities</h2>'
            '<p class="empty-note">No repeated automatable subtopics yet '
            '(clusters need ≥ 2 similar questions).</p></div>'
        )

    column_defs = [("date", "Date"), ("text", "Question"), ("display_name", "Asker"), ("category", "Category")]
    if has_llm_fields:
        column_defs += [("subtopic", "Subtopic"), ("difficulty", "Difficulty")]
    column_defs += [("automatable", "Automatable"), ("reply_count", "Replies"), ("first_reply_min", "First reply (min)")]
    thead_html = "".join(
        f'<th data-key="{key}">{html.escape(label)}</th>' for key, label in column_defs
    )

    diff_range_html = ""
    if has_llm_fields:
        options = "".join(f'<option value="{i}">{i}</option>' for i in range(1, 6))
        diff_range_html = (
            '<label>Difficulty <select id="diff-min">' + options.replace('value="1"', 'value="1" selected') + '</select>'
            ' to <select id="diff-max">' + options.replace('value="5"', 'value="5" selected') + '</select></label>'
        )

    model_json = json.dumps(model, default=str)

    script = "(function () {\n" + _COMMON_JS + _PAGE_SCRIPT

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>#partnerships — Automation Deep-Dive</title>
<style>{_STYLE}{_PAGE_STYLE}</style>
</head>
<body>
<div class="viz-root">

<header class="site-header">
  <div class="site-header-inner">
    <div class="site-header-title">
      <h1>#partnerships — Automation Deep-Dive</h1>
      <div class="meta">Updated {html.escape(generated_at)}</div>
    </div>
    <nav class="site-nav">
      <a href="./index.html">Overview</a>
      <a href="./automation.html" class="active">Automation</a>
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

  {other_info_html}

  {notables_section}

  <div class="viz-section">
    <h2>Question explorer</h2>
    <div class="explorer-controls">
      <input type="text" id="q-search" placeholder="Search question or subtopic...">
      <div class="chip-row" id="category-chip-toggles"></div>
      <label><input type="checkbox" id="automatable-only"> Automatable only</label>
      {diff_range_html}
      <button id="clear-cluster-filter" type="button" hidden>Clear cluster filter ×</button>
    </div>
    <div class="explorer-count" id="question-count"></div>
    <p class="empty-note" id="refine-notice" hidden></p>
    <div style="overflow-x:auto">
      <table class="viz-table" id="question-table">
        <thead><tr>{thead_html}</tr></thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

  <div class="viz-footer">
    Data sourced from committed <code>data/analysis/*.json</code> files.<br>
    Generated at {html.escape(generated_at)}.
  </div>

</div>
</div>

<script type="application/json" id="model">{model_json}</script>
<script>{script}</script>
</body>
</html>
"""


_PAGE_SCRIPT = r"""

  var hasLlm = !!model.has_llm_fields;
  var categoryOrder = model.category_order || [];
  var allColumns = ['date', 'text', 'display_name', 'category'];
  if (hasLlm) allColumns = allColumns.concat(['subtopic', 'difficulty']);
  allColumns = allColumns.concat(['automatable', 'reply_count', 'first_reply_min']);

  var state = {
    search: '',
    categories: {},
    automatableOnly: false,
    diffMin: 1,
    diffMax: 5,
    sortKey: 'date',
    sortDir: 'desc',
    clusterFilter: null
  };
  categoryOrder.forEach(function (c) { state.categories[c] = true; });

  function normalizeSubtopic(s) {
    s = (s || '').toLowerCase();
    s = s.replace(/[^\w\s]/g, '');
    s = s.replace(/\s+/g, ' ').trim();
    return s;
  }

  function colorVarFor(name) {
    var slot = (model.category_colors && model.category_colors[name]) || 'muted';
    return slot === 'muted' ? '--muted' : '--' + slot;
  }

  function buildCategoryChip(name) {
    var span = document.createElement('span');
    var dot = document.createElement('span');
    dot.className = 'cat-chip-dot';
    dot.style.background = 'var(' + colorVarFor(name) + ')';
    span.appendChild(dot);
    span.appendChild(document.createTextNode(name || ''));
    return span;
  }

  function buildDiffDots(diff) {
    var wrap = document.createElement('span');
    if (diff === null || diff === undefined) {
      wrap.className = 'empty-note';
      wrap.textContent = 'n/a';
      return wrap;
    }
    wrap.className = 'diff-dots';
    wrap.title = diff.toFixed(1) + '/5';
    var filled = Math.max(0, Math.min(5, Math.round(diff)));
    for (var i = 0; i < 5; i++) {
      var d = document.createElement('span');
      d.className = 'diff-dot' + (i < filled ? ' filled' : '');
      wrap.appendChild(d);
    }
    return wrap;
  }

  function renderChips() {
    var wrap = document.getElementById('category-chip-toggles');
    if (!wrap) return;
    wrap.innerHTML = '';
    categoryOrder.forEach(function (c) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'chip-toggle' + (state.categories[c] ? ' active' : '');
      btn.appendChild(buildCategoryChip(c));
      btn.addEventListener('click', function () {
        state.categories[c] = !state.categories[c];
        state.clusterFilter = null;
        var clearBtn = document.getElementById('clear-cluster-filter');
        if (clearBtn) clearBtn.hidden = true;
        renderChips();
        renderTable();
      });
      wrap.appendChild(btn);
    });
  }

  function matchesFilters(q) {
    if (state.clusterFilter) {
      if (q.category !== state.clusterFilter.category) return false;
      if (normalizeSubtopic(q.subtopic) !== state.clusterFilter.normalizedSubtopic) return false;
      return true;
    }
    if (state.categories[q.category] === false) return false;
    if (state.automatableOnly && !q.automatable) return false;
    if (hasLlm && q.difficulty !== null && q.difficulty !== undefined) {
      if (q.difficulty < state.diffMin || q.difficulty > state.diffMax) return false;
    }
    if (state.search) {
      var hay = ((q.text || '') + ' ' + (q.subtopic || '')).toLowerCase();
      if (hay.indexOf(state.search) === -1) return false;
    }
    return true;
  }

  function compareRows(a, b) {
    var key = state.sortKey, dir = state.sortDir === 'asc' ? 1 : -1;
    var av = a[key], bv = b[key];
    if (av === null || av === undefined) av = '';
    if (bv === null || bv === undefined) bv = '';
    if (typeof av === 'string' || typeof bv === 'string') {
      return String(av).localeCompare(String(bv)) * dir;
    }
    return (av - bv) * dir;
  }

  var expanded = {};

  function appendCells(tr, q) {
    allColumns.forEach(function (key) {
      var td = document.createElement('td');
      if (key === 'date') {
        td.textContent = q.date || '';
      } else if (key === 'text') {
        var clamp = document.createElement('div');
        clamp.className = 'q-text-clamp';
        clamp.textContent = q.text || '';
        td.appendChild(clamp);
      } else if (key === 'display_name') {
        td.textContent = q.display_name || q.user || '';
      } else if (key === 'category') {
        td.appendChild(buildCategoryChip(q.category));
      } else if (key === 'subtopic') {
        td.textContent = q.subtopic || '';
      } else if (key === 'difficulty') {
        td.appendChild(buildDiffDots(q.difficulty));
      } else if (key === 'automatable') {
        td.textContent = q.automatable === true ? '✓' : (q.automatable === false ? '—' : 'n/a');
      } else if (key === 'reply_count') {
        td.textContent = fmtCount(q.reply_count);
      } else if (key === 'first_reply_min') {
        td.textContent = (q.first_reply_min !== null && q.first_reply_min !== undefined) ? fmtMinutes(q.first_reply_min) : 'n/a';
      }
      tr.appendChild(td);
    });
  }

  function renderTable() {
    var filtered = (model.questions || []).filter(matchesFilters);
    filtered.sort(compareRows);
    var total = (model.questions || []).length;
    var countEl = document.getElementById('question-count');
    if (countEl) countEl.textContent = filtered.length + ' of ' + total + ' questions';
    var notice = document.getElementById('refine-notice');
    var capped = filtered.slice(0, 500);
    if (notice) {
      if (filtered.length > 500) {
        notice.hidden = false;
        notice.textContent = 'Showing 500 of ' + filtered.length + ' matching questions — refine filters to see more.';
      } else {
        notice.hidden = true;
      }
    }
    var tbody = document.querySelector('#question-table tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    if (!capped.length) {
      var emptyRow = document.createElement('tr');
      var emptyTd = document.createElement('td');
      emptyTd.colSpan = allColumns.length;
      emptyTd.className = 'empty-note';
      emptyTd.textContent = 'No questions match the current filters.';
      emptyRow.appendChild(emptyTd);
      tbody.appendChild(emptyRow);
      return;
    }
    capped.forEach(function (q) {
      var tr = document.createElement('tr');
      tr.className = 'q-row';
      appendCells(tr, q);
      tr.addEventListener('click', function () {
        expanded[q.ts] = !expanded[q.ts];
        renderTable();
      });
      tbody.appendChild(tr);

      var detail = document.createElement('tr');
      detail.className = 'q-detail-row';
      detail.hidden = !expanded[q.ts];
      var td = document.createElement('td');
      td.colSpan = allColumns.length;
      var full = document.createElement('div');
      full.className = 'q-detail';
      var textDiv = document.createElement('div');
      textDiv.textContent = q.text || '';
      full.appendChild(textDiv);
      if (q.rationale) {
        var rDiv = document.createElement('div');
        rDiv.className = 'q-rationale';
        rDiv.textContent = 'Rationale: ' + q.rationale;
        full.appendChild(rDiv);
      }
      td.appendChild(full);
      detail.appendChild(td);
      tbody.appendChild(detail);
    });
  }

  function wireControls() {
    var search = document.getElementById('q-search');
    if (search) search.addEventListener('input', function () {
      state.search = search.value.trim().toLowerCase();
      renderTable();
    });
    var autoOnly = document.getElementById('automatable-only');
    if (autoOnly) autoOnly.addEventListener('change', function () {
      state.automatableOnly = autoOnly.checked;
      renderTable();
    });
    var diffMin = document.getElementById('diff-min');
    var diffMax = document.getElementById('diff-max');
    if (diffMin) diffMin.addEventListener('change', function () {
      state.diffMin = parseInt(diffMin.value, 10);
      renderTable();
    });
    if (diffMax) diffMax.addEventListener('change', function () {
      state.diffMax = parseInt(diffMax.value, 10);
      renderTable();
    });

    var headers = document.querySelectorAll('#question-table thead th[data-key]');
    headers.forEach(function (th) {
      th.addEventListener('click', function () {
        var key = th.getAttribute('data-key');
        if (state.sortKey === key) {
          state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
        } else {
          state.sortKey = key;
          state.sortDir = 'asc';
        }
        renderTable();
      });
    });

    var clearBtn = document.getElementById('clear-cluster-filter');
    if (clearBtn) clearBtn.addEventListener('click', function () {
      state.clusterFilter = null;
      clearBtn.hidden = true;
      renderTable();
    });

    var showMatchLinks = document.querySelectorAll('.show-matching');
    showMatchLinks.forEach(function (a) {
      a.addEventListener('click', function (evt) {
        evt.preventDefault();
        state.clusterFilter = {
          category: a.getAttribute('data-category'),
          normalizedSubtopic: a.getAttribute('data-subtopic')
        };
        var clearBtn2 = document.getElementById('clear-cluster-filter');
        if (clearBtn2) clearBtn2.hidden = false;
        renderTable();
        var tableEl = document.getElementById('question-table');
        if (tableEl && tableEl.scrollIntoView) tableEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    });
  }

  function init() {
    renderChips();
    wireControls();
    renderTable();
  }

  document.addEventListener('DOMContentLoaded', init);
  if (document.readyState !== 'loading') init();

  setupThemeToggle();
})();
"""
