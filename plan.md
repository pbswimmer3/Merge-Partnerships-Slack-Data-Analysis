# plan.md — Dashboard v2: polish + Automation Deep-Dive page + Spend Tracker

Status: CONFIRMED by user 2026-07-14 — executing.
Branch: `claude/ui-automation-opportunities-ahugpl`.
Amendments from confirmation: (a) gateway usage API **cancelled** — it is org-wide
only, cannot scope to one API key (verified: `api_key_id`/`key`/`scope`/`per_key`
params all ignored; no per-key endpoints). Spend tracker = local ledger only.
No AH_API_KEY secret needed. (b) Phase D added: widen LLM category taxonomy +
re-run backfill workflow.

---

## 0. Scope — three independently shippable phases, in order

- **Phase A** — polish `site/index.html` (all changes in `src/dashboard.py`).
- **Phase B** — new drill-down page `site/automation.html` (new `src/automation_page.py`).
- **Phase C** — spend tracker: local LLM cost ledger + gateway usage fetch + panel UI.

Each phase ends with: tests green, both pages rendered from real committed data,
screenshots reviewed, PROGRESS.md updated.

---

## 1. Verified facts (do NOT re-derive; verified live 2026-07-14)

**Data.** `data/analysis/2026-07-13.json` (whole-window) + `data/analysis_by_day/*.json`
(one file per activity date). Question objects:

```json
{"ts": "1776364246.950929", "user": "U08VAUU4Z2M", "text": "...",
 "category": "pricing_commercial", "is_question": true, "reply_count": 5,
 "first_reply_latency_sec": 751.12, "permalink": null,
 "llm_category": "pricing_commercial", "subtopic": "plan upgrade request",
 "difficulty": 2, "automatable": true, "rationale": "..."}
```

`data/user_directory.json` maps Slack user ID → real name (already used for Top askers).

**Dashboard architecture.** `src/dashboard.py`: `aggregate(analysis_files, user_directory)`
builds a JSON model; `render_html(model, generated_at)` emits one self-contained HTML file
(no CDNs/fonts — hard constraint, keep it). Charts are rendered **client-side** by inline
JS from the embedded model (`renderLineChart`, `renderBarChart`, …) with shared tooltip
helpers `makeTooltip/showTip/hideTip` and a `css(name)` var reader. Theme: CSS custom
properties on `.viz-root`, dark via `prefers-color-scheme` + `data-theme` override
(toggle exists). `cmd_dashboard` in `src/cli.py` merges per-day files via
`_merge_daily_for_dashboard()` before calling `aggregate()`.

**Gateway usage API** (probe results):

```
GET https://ah-api.merge.dev/api/v1/usage?start_time=2026-07-01T00:00:00Z&end_time=2026-07-14T00:00:00Z
→ {"total_credits_used": 9659, "total_call_count": 9659, "last_updated_at": "..."}
```

- Both params required, ISO 8601 **with timezone** (`...T00:00:00Z`) or 400.
- Aggregate only — `group_by`/`granularity`/`breakdown` params are silently ignored.
  Per-day series ⇒ one request per day window, so cache each day to disk and only
  fetch missing days.
- Credits ≈ calls (1 credit/call in sample). **Org-wide**, not project-scoped —
  label it "Merge Gateway (org-wide)" in UI; never present credits as dollars.
- Auth: works from Claude remote sessions (proxy-injected). In GitHub Actions it will
  need an `AH_API_KEY` secret → send `Authorization: Bearer $AH_API_KEY` when set;
  on 401/403/network error, log a warning and skip (panel shows "gateway data
  unavailable"), never fail the build.

**Model pricing for the local ledger** (per MTok, from Anthropic docs 2026-06):
`claude-opus-4-8` $5 in / $25 out · `claude-sonnet-5` $3/$15 · `claude-haiku-4-5` $1/$5.
Cache reads ≈ 0.1× input price; Batch API = 50% off. Repo currently uses
`claude-opus-4-8` (config.yaml).

---

## 2. Design system — BINDING for all UI work in every phase

### 2.1 Tokens

Reuse the existing `.viz-root` custom properties verbatim (`--surface-1 #fcfcfb/#1a1a19`,
`--page`, `--text-primary/secondary`, `--muted`, `--grid`, `--baseline`, `--border`,
`--series-1..3`). **Extend** with categorical slots 4–8 from the validated reference
palette (light / dark):

| Slot | Light | Dark |
|---|---|---|
| 4 green | `#008300` | `#008300` |
| 5 violet | `#4a3aa7` | `#9085e9` |
| 6 red | `#e34948` | `#e66767` |
| 7 magenta | `#e87ba4` | `#d55181` |
| 8 orange | `#eb6834` | `#d95926` |

### 2.2 Fixed category → color mapping (never cycled, never re-ranked)

```
api_technical         → --series-1 (blue)
integration_connector → --series-2 (aqua)
pricing_commercial    → --series-3 (yellow)
data_sync             → --series-4 (green)
access_permissions    → --series-5 (violet)
bug_issue             → --series-6 (red)
auth_scopes / any new → --series-7, then --series-8, in first-seen alphabetical order
other                 → --muted (gray) — ALWAYS, it is a bucket not an entity
```

Define once in JS as `CATEGORY_COLOR` and use in every chart/chip on both pages.
Color follows the category, never its rank; a filter must not repaint survivors.

### 2.3 Chart rules (from the dataviz method — violations are review blockers)

1. One axis per chart. Never dual y-scales.
2. Thin marks: 2px lines, bars with 4px rounded data-end anchored to baseline,
   ≥8px dot/bubble markers, 2px surface gap between adjacent fills.
3. Every chart ships a hover layer (crosshair+tooltip on lines, per-mark tooltip on
   bars/bubbles) reusing the existing `makeTooltip/showTip/hideTip` helpers. Hit
   targets larger than the mark.
4. Axis ticks are round numbers (0/20/40/60, not 0/21/43/64). Add
   `niceTicks(maxVal, n)` helper; use everywhere.
5. Text wears text tokens (`--text-*`, `--muted`), never series colors. Identity comes
   from a colored mark beside the text.
6. ≥2 series ⇒ legend present; ≤4 series also direct-labeled. Single series ⇒ no legend.
7. Labels must not collide: measure with `getBBox()`, push apart vertically, clamp to
   plot, leader line when displaced >8px. (This is the fix for the current scatter.)
8. Dark mode uses the dark-column hexes above — not an automatic flip. Aqua/yellow/
   magenta are low-contrast on light surface ⇒ those marks always get visible direct
   labels or a table fallback (the ranked table already satisfies this).
9. Number format: counts as integers with thin-space grouping ≥10 000; percents 1
   decimal; minutes as `8.2 min`; hours as `4.6 h`; credits as integers.
10. No external resources of any kind (CSP-like constraint of the repo's Pages setup).
11. Every dashboard panel keeps an accessible table equivalent (existing ranked table
    pattern) when the chart encodes with color.

Palette validation (already-validated reference values, so only needed if hexes change):
`node <dataviz-skill>/scripts/validate_palette.js "<hexes>" --mode light|dark`.
In this repo's CI-less flow: run once locally during Phase A, paste result in PR.

### 2.4 Layout & chrome (both pages)

- Sticky page header: title, date range, "Updated <generated_at>", theme toggle,
  nav (`Overview | Automation` links between the two pages).
- Card grid: KPI row (5 tiles) full-width; below, cards may sit 2-up ≥1000px
  (`grid-template-columns: repeat(auto-fit, minmax(480px, 1fr))`); volume chart and
  the question table always full-width.
- Spacing scale 4/8/12/16/24/32px; card radius 10px; `border: 1px solid var(--border)`.

---

## 3. Phase A — polish `site/index.html` (all in `src/dashboard.py`)

**A1. Replace the automation scatter with a bubble quadrant chart.** Current chart is
broken: x=volume compressed by the `other`=64 outlier, labels overlap, y-label clipped.
New encoding: **x = automatable % (0–100), y = difficulty (1 top … 5 bottom, keep
"easier is up" via inverted axis + axis caption "▲ easier to automate"), bubble area ∝
question count (area, not radius: `r = rmax * sqrt(count/maxCount)`, rmin 6px,
rmax 22px)**, color = `CATEGORY_COLOR`, `other` gray. Quadrant guide lines at x=50%,
y=3 with a muted "automate first" annotation top-right. Collision-avoiding direct
labels (rule 2.3.7). Tooltip: category, count, avg difficulty, automatable %, candidate
flag. The ranked table below stays (it is the accessible fallback) with two upgrades:
difficulty rendered as 5-dot meter, automatable % as a 60px inline meter bar.

**A2. KPI tiles.** Add to each tile: a 30-day sparkline (messages/questions tiles) and
a delta vs the previous equal-length window ("▲ 12% vs prior 30d", `--text-secondary`;
compute from `analysis_by_day` split — data already per-day). Unanswered tile: keep
neutral ink (status colors are reserved), add sub-line "69 of 94 questions".

**A3. Volume chart.** Add a 7-day centered rolling mean per series as the primary 2px
line; raw daily as 1px 35%-opacity line of the same hue. Soft area fill (8% opacity)
under the messages average. Round ticks via `niceTicks`. Keep crosshair tooltip
(show raw + avg). Legend stays.

**A4. Category bar chart.** Bars take `CATEGORY_COLOR` (identity consistent with the
quadrant chart), count + pct direct label at bar end in text ink, round ticks.

**A5. Top askers.** Drop the full-width track (it flattens differences); value label
directly at bar end; bars all `--series-1` (single measure, identity is the name).

**A6. Header per §2.4** with nav to `automation.html`, plus a "Spend" button
(Phase C; hidden until spend model exists).

Estimated diff: ~350 lines in `dashboard.py` (mostly the JS chart functions) + test
updates. `aggregate()` gains: `kpi_deltas`, `kpi_sparklines`, per-category color-slot
assignment passed into the model (compute mapping in Python so both pages agree).

---

## 4. Phase B — `site/automation.html` deep-dive page

**New module `src/automation_page.py`** (mirrors dashboard.py: `build_model()` +
`render_html()`), invoked from `cmd_dashboard` so `python -m src.cli dashboard` and CI
build both pages. Shares CSS/JS helpers: extract the common `_STYLE` + tooltip/format
JS into `src/site_common.py` string constants imported by both renderers (no behavior
change to index.html beyond Phase A).

### 4.1 Model (`build_model(merged_analysis, user_directory)`)

- `questions`: every question, with `display_name` resolved, `date` derived from `ts`
  (UTC), text kept whole (it's already redacted upstream; page is org-internal).
- `clusters`: group **automatable** questions by `(llm_category, normalized subtopic)`
  (lowercase, strip punctuation, collapse whitespace). Cluster fields: `category`,
  `subtopic`, `count`, `avg_difficulty`, `question_refs` (ts list), `example_texts`
  (≤3), `est_minutes_saved` = `count × median(first_reply_latency of members, fallback
  global median)`, `suggested_fix` — keyword heuristic on member `rationale` strings:
  docs/FAQ words → "Docs/FAQ answer", plan/pricing → "Self-serve pricing page",
  access/permission → "Access-request workflow", status/sync → "Status page / alert
  bot", else "Slack auto-responder".
- `notables`: clusters sorted by `score = count × (6 − avg_difficulty)`, top 6,
  require `count ≥ 2`.
- `kpis`: automatable count + % of questions, est. total reply-time on automatable
  questions (sum of latency, shown in hours), cluster count, top category.
- Note: 64/94 questions are `other` — surface an info line "68% uncategorized —
  subtopics below give the real themes" rather than hiding it (§9 has the follow-up).

### 4.2 UI

- Header (§2.4) with **Spend tracker** button (opens slide-over panel, Phase C).
- KPI row from `kpis`.
- **Notable opportunities**: card per notable — subtopic as title, category chip
  (CATEGORY_COLOR dot + name), count, 5-dot difficulty, `est_minutes_saved` as
  "≈ N h of reply time", `suggested_fix` tag, one example question in quotes,
  "show matching questions" → applies table filter below.
- **Question explorer table** (full-width): Date · Question (2-line clamp, click row
  to expand full text + rationale) · Asker · Category chip · Subtopic · Difficulty
  dots · Automatable ✓ · Replies · First reply (min). Controls in one row above:
  free-text search (text+subtopic), category chips (multi-toggle), "automatable only"
  switch, difficulty range (two selects 1–5), column-click sort. All plain JS over the
  embedded array; re-render `<tbody>` on change; show "N of M questions" count.
  94 rows now, design for ~1–2k (no pagination needed; cap rendered rows at 500 with
  a "refine filters" notice).

Estimated: ~500 lines new module + ~40 in cli.py/site_common extraction + tests.

---

## 5. Phase C — Spend tracker

### C1. Local ledger (project-scoped truth, in dollars)

`src/llm.py`: both call sites already receive the SDK response — capture
`response.usage.input_tokens/output_tokens` (+ `cache_read_input_tokens` if present)
and return usage alongside results. `cmd_analyze` writes one entry per run to
`data/spend/ledger/YYYY-MM.json` (monthly files, append):

```json
{"run_at": "2026-07-14T01:00:00Z", "command": "analyze --days 1",
 "model": "claude-opus-4-8", "calls": 4, "input_tokens": 51234,
 "output_tokens": 8021, "cache_read_input_tokens": 0, "est_cost_usd": 0.4568,
 "dates_analyzed": ["2026-07-13"]}
```

`est_cost_usd` from a `PRICING` table in `src/config.py` (§1 values; unknown model →
cost `null`, never crash). Ledger starts at deploy — label "since <first entry>" in UI;
no fabricated backfill.

### C2. ~~Gateway fetcher~~ CANCELLED (org-wide only; cannot scope to this API key)

### C3. Spend model + panel UI (lives on automation.html; button on both headers)

Model: `{ledger: {since, total_usd, total_calls, total_input_tokens,
total_output_tokens, by_day[], per_run_recent[10], cost_per_question,
cost_per_candidate}, recommendations[]}`. `by_day` aggregates ledger entries by
run date for the chart.

Slide-over panel (480px, right, ESC/overlay close): KPI row ($ total since ledger
start · cost/question · calls · tokens in/out), line chart of $ per day from the
ledger (rule set §2.3), recent-runs table, recommendations list. Label: "Measured
from this project's LLM calls since <ledger start>". Empty ledger → panel shows a
short explainer ("spend tracking starts with the next analyze run") instead of
charts.

### C4. Recommendations engine (pure rules at build time, each with computed $ impact)

1. **Batch API**: nightly cron is latency-insensitive → "Batches API halves token cost;
   est. save $X/mo" (X = ledger monthly cost × 0.5).
2. **Prompt caching**: classifier resends the same system prompt per batch; if
   `cache_read_input_tokens == 0` in ledger, estimate savings at 0.9 × (input cost ×
   estimated shared-prefix share 0.6), labeled "estimate".
3. **Model mix**: comparison table of the ledger's token volume priced on opus-4-8 /
   sonnet-5 / haiku-4-5, labeled "classification quality must be spot-checked —
   user decision, not auto-applied".
4. **Re-run overlap**: from ledger `dates_analyzed`, flag dates analyzed >1× and the
   wasted $ (guards against the July 13 `--days 1` overwrite class of incident).
5. Emit only rules whose computed impact ≥ $0.10/mo; each rec = `{title, detail,
   est_monthly_savings_usd | null, kind}`.

Estimated: ~120 lines llm/config, ~110 gateway_usage.py, ~150 model+rules, ~200 panel
HTML/JS, + tests.

---

## 5b. Phase D — widen LLM category taxonomy + backfill re-run

Problem: 64/94 questions land in `other`. Fix in `src/llm.py` classifier prompt:
expand the allowed category list to (snake_case, comma-free for the Notion select
edge): `api_technical, integration_connector, data_sync, pricing_commercial,
access_permissions, bug_issue, auth_scopes, partnership_process, customer_request,
feature_request, sales_marketing, internal_ops, other`. Keep `other` as explicit
last resort; instruct the model to prefer a specific category. Update any category
list constants/tests that enumerate categories (grep first). §2.2 already assigns
slots 7/8 + first-seen ordering for new categories — verify chip/color rendering
handles >8 categories by folding extras into slot 8 is NOT allowed; instead extras
get `--muted` like `other` and a note in the ranked table (rare in practice).
Execution: after phases A–C are pushed, trigger `.github/workflows/
backfill-llm-analysis.yml` via workflow_dispatch **with ref = this branch** so the
widened classifier re-runs the committed 90-day history and commits refreshed
`data/analysis*`+ dashboard onto this branch (repo secrets are available to
workflow_dispatch on a branch). Verify category distribution afterward.

## 6. Delegation map (per CLAUDE.md routing)

| Task | Agent |
|---|---|
| A1–A6, B renderer+model, C3–C4 | `implementer` (spec = the relevant § above + §2 verbatim) |
| `site_common.py` extraction, `niceTicks`/format helpers, C1 ledger plumbing, C2 fetcher | `grunt` |
| All test files | `grunt` (from schemas in this plan) |
| Diff review each phase | `reviewer` |

Workers get: this plan section, file list, and the §1 facts. Workers must NOT
re-probe the gateway API or re-read data files beyond one sample.

## 7. Tests (pytest, offline, extend `tests/`)

- `test_dashboard.py`: niceTicks-equivalent Python helper if any; kpi_deltas +
  sparkline model fields; category color-slot assignment stability (new category →
  slot 7/8; `other` → gray sentinel).
- `test_automation_page.py`: clustering (normalization merges "Plan Upgrade" /
  "plan upgrade request"? — no: exact normalized-string match only, no fuzzy),
  notable scoring/threshold, suggested_fix keyword rules, minutes-saved fallback,
  HTML renders with 0 questions / no LLM fields (heuristic-only mode).
- `test_spend.py`: cost math vs PRICING (incl. unknown model → null), ledger
  append/idempotency, gateway fetcher URL formatting + error paths (mock transport),
  recommendations thresholds, rec suppression when ledger empty.
- Existing 36 tests stay green; `render_html` snapshot-ish assertions updated.

## 8. Acceptance (per phase)

1. `make test` green.
2. `python -m src.cli dashboard` from committed data; open both pages; screenshot
   light+dark; check against §2.3 and the dataviz anti-pattern list (no label
   collisions, round ticks, no color-only identity).
3. `reviewer` pass on the diff.
4. PROGRESS.md updated; plan.md deleted only after ALL phases complete.

## 9. Open questions / risks (answer these with confirmation)

1. **Gateway in CI**: add `AH_API_KEY` repo secret so the daily Action can refresh
   gateway spend? Without it, gateway numbers only refresh when a Claude session
   runs the build. (Panel degrades gracefully either way.)
2. **Historical spend**: no per-project attribution exists for past runs; ledger is
   forward-only, gateway credits are org-wide. Acceptable framing?
3. **Scatter replacement**: OK to replace volume-vs-difficulty scatter with the
   automatable%-vs-difficulty bubble chart (volume = bubble size)? It fixes the
   outlier compression and is the decision-relevant view.
4. **`other` = 68%**: separate follow-up (not in this plan) to widen the LLM category
   taxonomy in `llm.py`'s prompt and re-run the backfill workflow — cheap, high value
   for this page. Include as Phase D?
