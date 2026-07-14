"""Shared CSS + JS + small Python rendering helpers for the static dashboard
pages (`site/index.html`, `site/automation.html`). Extracted from
`dashboard.py` (Phase B) so both pages share one design system (plan.md §2)
without duplicating markup/CSS/JS. Keep page-specific CSS/JS in each page's
own module.
"""
from __future__ import annotations

import html
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# category -> color-slot assignment (§2.2, binding across dashboard + automation
# page). Fixed categories always map to the same slot regardless of rank;
# unrecognized categories (excluding "other") get slot 7 then 8, in
# alphabetical order among themselves; anything beyond that, and "other"
# itself, always falls back to the muted gray sentinel.
# ---------------------------------------------------------------------------

# The validated categorical palette has exactly 8 slots. With the widened
# taxonomy (13 categories) we can't give every category a distinct hue, so we
# assign the 8 slots to a FIXED, data-independent set of the most prominent
# categories; everything else (and `other`) renders in `--muted` gray, with the
# ranked/explorer tables as the accessible fallback. Being a pure static lookup,
# the mapping is identical across both pages regardless of which category field
# or ordering they feed in (no rank- or input-dependent slots).
_FIXED_CATEGORY_SLOTS = {
    "api_technical": "series-1",
    "integration_connector": "series-2",
    "pricing_commercial": "series-3",
    "partnership_process": "series-4",
    "access_permissions": "series-5",
    "bug_issue": "series-6",
    "auth_scopes": "series-7",
    "internal_ops": "series-8",
}
_MUTED_SLOT = "muted"


def category_color_map(category_names: List[str]) -> Dict[str, str]:
    """Map each seen category name to a stable color slot. Fixed categories get
    their dedicated slot; every other category (including `other`) -> muted.
    Purely a static lookup: independent of input order and identical on both
    pages, so a given category always renders in the same color."""
    seen: List[str] = []
    seen_set = set()
    for cat in category_names or []:
        if cat and cat not in seen_set:
            seen_set.add(cat)
            seen.append(cat)
    return {cat: _FIXED_CATEGORY_SLOTS.get(cat, _MUTED_SLOT) for cat in seen}


def difficulty_dots_html(avg_difficulty: Optional[float]) -> str:
    if avg_difficulty is None:
        return '<span class="empty-note">n/a</span>'
    filled = max(0, min(5, round(avg_difficulty)))
    dots = "".join(
        f'<span class="diff-dot{" filled" if i < filled else ""}"></span>' for i in range(5)
    )
    return f'<span class="diff-dots" title="{avg_difficulty:.1f}/5">{dots}</span>'


def automatable_meter_html(pct: Optional[float]) -> str:
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


def category_chip_html(category: str, category_colors: dict) -> str:
    slot = (category_colors or {}).get(category, "muted")
    color_var = "--muted" if slot == "muted" else f"--{slot}"
    dot = f'<span class="cat-chip-dot" style="background:var({color_var})"></span>'
    return f'{dot}{html.escape(str(category))}'


# ---------------------------------------------------------------------------
# Shared CSS. Both pages embed this verbatim inside their own <style> tag,
# then append page-specific rules after it.
# ---------------------------------------------------------------------------

STYLE = """
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
  text-decoration: none; display: inline-block; line-height: 1.2;
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


# ---------------------------------------------------------------------------
# Shared JS. Both pages embed this inside their own `(function () { ... })();`
# IIFE (each page parses its own embedded `#model` JSON into `model`/`root`).
# Provides: css/el/makeTooltip/showTip/hideTip/niceTicks/categoryColor, the
# §2.3-rule-9 number format helpers, and the dark/light toggle wiring
# (no initial `data-theme` is stamped server-side; `effectiveTheme()` reads
# `prefers-color-scheme` until the user clicks the toggle).
# ---------------------------------------------------------------------------

COMMON_JS = r"""
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

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
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

  // §2.3 rule 9 number formatting: counts as integers with thin-space
  // grouping >= 10,000; percents 1 decimal; minutes as "8.2 min"; hours as
  // "4.6 h".
  function fmtCount(n) {
    if (n === null || n === undefined || isNaN(n)) return 'n/a';
    n = Math.round(n);
    var neg = n < 0;
    var s = Math.abs(n).toString();
    if (s.length > 4) {
      var parts = [];
      while (s.length > 3) {
        parts.unshift(s.slice(-3));
        s = s.slice(0, -3);
      }
      parts.unshift(s);
      s = parts.join(' ');
    }
    return (neg ? '-' : '') + s;
  }

  function fmtPct(p) {
    if (p === null || p === undefined || isNaN(p)) return 'n/a';
    return p.toFixed(1) + '%';
  }

  function fmtMinutes(m) {
    if (m === null || m === undefined || isNaN(m)) return 'n/a';
    return m.toFixed(1) + ' min';
  }

  function fmtHours(h) {
    if (h === null || h === undefined || isNaN(h)) return 'n/a';
    return h.toFixed(1) + ' h';
  }

  // Dark/light toggle: no data-theme is stamped server-side, so the initial
  // state follows `prefers-color-scheme` until the user overrides it by
  // clicking. `onToggle` (usually the page's own `init`) re-renders charts
  // that read colors via `css()` so they pick up the new theme's hexes.
  function setupThemeToggle(onToggle) {
    var toggle = document.getElementById('theme-toggle');
    if (!toggle) return;
    function effectiveTheme() {
      var explicit = root.getAttribute('data-theme');
      if (explicit) return explicit;
      return (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
    }
    toggle.textContent = effectiveTheme() === 'dark' ? 'Light mode' : 'Dark mode';
    toggle.addEventListener('click', function () {
      var next = effectiveTheme() === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      toggle.textContent = next === 'dark' ? 'Light mode' : 'Dark mode';
      if (onToggle) setTimeout(onToggle, 0);
    });
  }
"""
