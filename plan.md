# Plan: fix analysis-file overwrite/double-count bug

## Problem
`data/analysis/<end_date_str>.json` is keyed by the date the tool was **run**, not
by the date(s) the data covers. `cmd_analyze` (src/cli.py:68-86) always writes the
*entire requested window* into one file named after today.

Two related failure modes:
1. **Same-day overwrite (what just happened):** running the CLI/Action twice on
   the same calendar day with different `--days` values overwrites the earlier,
   larger window's file with the smaller one. Today's 90-day backfill file got
   replaced by a 1-day test run's output.
2. **Cross-day double count (latent, not yet hit):** `dashboard.aggregate()`
   (src/dashboard.py `_merge_timeseries`) sums `per_day` counts across *every*
   file in `data/analysis/`. If two files ever cover overlapping date ranges
   (e.g. a second backfill run overlapping days already covered by prior daily
   files), those overlapping days get double-counted in the dashboard.

Root cause of both: the storage unit (one file per invocation) doesn't match
the unit the dashboard needs (one file per calendar day of Slack activity,
never overlapping, safely re-computable).

## Fix
Keep today's whole-window blob at `data/analysis/<end_date_str>.json` exactly
as-is — `cmd_report` and `cmd_notion` read it and don't need to change.

Add a second, per-day store that the dashboard reads instead, built by
slicing the already-computed whole-window `analysis` dict by date:

- New path helper in `src/store.py`: `daily_analysis_path(date_str)` →
  `data/analysis_by_day/<date_str>.json`, plus `read_all_daily_analysis()`
  (glob + sort, mirrors existing `read_all_analysis()`).
- In `cmd_analyze` (src/cli.py), after the existing whole-window analysis is
  computed and classified, split it by date and upsert one file per date:
  - `totals`/`per_day` slice: reuse `analysis["per_day"][date_str]` for
    message/question counts.
  - `questions`: filter `analysis["questions"]` to entries whose `ts` falls on
    that date.
  - `category_distribution`, `top_askers`, `response` (median latency,
    unanswered count): recompute from that date's own question subset (small
    pure function, no new LLM/Slack calls — classification already happened
    once on the full window).
  - Write via `store.write_json(day_slice, store.daily_analysis_path(date_str))`.
    Because the filename is the message date (not the run date), re-running
    any window that includes that date always overwrites it with fresh,
    self-consistent data for that date only — never a different date's data,
    never a subset silently replacing a superset.
- `cmd_dashboard` (src/cli.py) switches its input from
  `store.read_all_analysis()` to `store.read_all_daily_analysis()`.
  `dashboard.py` itself (`aggregate()`, `render_html()`) is **unchanged** —
  it already expects "a list of non-overlapping per-day-ish analysis dicts";
  we're just making that assumption actually hold.
- `.github/workflows/daily-partnerships-analysis.yml`: add
  `data/analysis_by_day/` to the `git add` list in the commit step.

## Why not redesign `dashboard.aggregate()` instead
Considered a single cumulative `data/analysis/history.json` merged in place.
Rejected for now: bigger diff, breaks `aggregate()`'s existing signature and
all of `tests/test_dashboard.py` (9 call sites), for no behavior change the
per-day-file approach doesn't already give us. Per-day files are a smaller,
additive change — `dashboard.py` and its tests stay untouched.

## Files touched
- `src/store.py` — add 2 helpers, no changes to existing functions.
- `src/cli.py` — `cmd_analyze` gains the split/upsert step; `cmd_dashboard`
  points at the new read function.
- `.github/workflows/daily-partnerships-analysis.yml` — one line added to
  `git add`.
- New tests (implementer/grunt): per-day split correctness (message counts
  match `per_day`, questions correctly bucketed by date, re-running an
  overlapping window doesn't change other dates' files, same-day re-run with
  a smaller window no longer destroys other dates).

## Bootstrap
No migration script needed: the user's in-flight `analyze --days 90` re-run,
once this lands, will populate `data/analysis_by_day/` for all 90 days in one
shot. Existing `data/analysis/*.json` (the whole-window snapshots) are left
as-is — still consumed by `report`/`notion`, harmless to keep around.

## Out of scope
`store.write_raw()` still overwrites (not merges) a date's raw file on every
scrape (flagged in PROGRESS.md). Not touched here — separate concern, only
matters if a future scrape returns a partial subset for an already-fuller
cached date.
