# Merge Partnerships Slack Data Analysis

Scrapes the `#partnerships` Slack channel and produces a Markdown analysis report:
trends, most-asked topics, question categories/types, difficulty ranking, and
automation opportunities.

## Setup

1. **Create a Slack app** at https://api.slack.com/apps (or use an existing one) and
   install it to your workspace.
   - Add Bot Token Scopes: `channels:history`, `channels:read`
     (add `groups:history` / `groups:read` too if `#partnerships` is a private channel).
   - Install the app to the workspace and invite the bot user into `#partnerships`.
   - Copy the **Bot User OAuth Token** (`xoxb-...`).
2. **Find the channel ID** for `#partnerships` (right-click the channel > View channel
   details > copy Channel ID, or use `conversations.list`).
3. **Copy `.env.example` to `.env`** and fill in:
   ```
   SLACK_BOT_TOKEN=xoxb-...
   SLACK_CHANNEL_ID=C0123456789
   ANTHROPIC_API_KEY=sk-ant-...   # optional, enables LLM categorization/difficulty
   SLACK_POST_CHANNEL_ID=C0987654321   # optional, only used with --post
   ```
4. **Install dependencies:**
   ```
   make install
   ```

## Usage

Run the full pipeline (scrape -> analyze -> report) for a 30-day look-back:

```
make run ARGS="--days 30"
```

This is equivalent to:

```
python -m src.cli run --days 30
```

Individual stages are also available:

```
python -m src.cli scrape --days 30
python -m src.cli analyze
python -m src.cli report
```

The rendered report is written to `reports/partnerships-YYYY-MM-DD.md`, and
intermediate data is cached in `data/raw/` (per-day raw messages) and
`data/analysis/` (per-day computed stats), both idempotent by date.

Add `--post` to also post the digest to `SLACK_POST_CHANNEL_ID` via
`chat.postMessage` (requires `SLACK_BOT_TOKEN` and `SLACK_POST_CHANNEL_ID` to be set).

Add `--notion` to also write each analyzed question as a row in a Notion database
(see "Notion output" below).

### Look-back window

`--days N` (or `lookback_days` in `config.yaml`) controls how far back messages are
pulled. The look-back is computed as `now - N days` using `time.time()`; when run via
the scheduled GitHub Action, "now" is UTC, so day boundaries are UTC-aligned.

## Offline export mode

If you don't have a live Slack token (or want to analyze a Slack-exported archive),
use `--export-dir`:

```
python -m src.cli run --export-dir path/to/export/partnerships
```

The export directory should contain per-day Slack export JSON files (the same
format produced by Slack's built-in workspace export: one `.json` file per day,
each containing a list of message objects, with thread replies sharing the same
`thread_ts`). These are loaded and normalized into the same shape as the live API
path, so `analyze`/`report` behave identically.

## LLM analysis toggle

Set in `config.yaml`:

```yaml
llm:
  enabled: auto     # auto = on if ANTHROPIC_API_KEY is set, off otherwise
  model: claude-opus-4-8
  batch_size: 25
```

- `auto` (default): LLM categorization/difficulty/automation scoring is used only if
  `ANTHROPIC_API_KEY` is present in the environment.
- `true` / `false`: force on/off regardless of the key (forcing `true` without a key
  simply no-ops and falls back to heuristics; nothing crashes).

Without an Anthropic key, the report still includes heuristic categorization,
question detection, trends, and a heuristic difficulty/automation proxy.

## Notion output

Analyzed questions can be written to a Notion database (one row per question,
upserted by Slack message timestamp so re-runs update rather than duplicate rows):

1. **Create an internal Notion integration** at https://www.notion.so/my-integrations
   and copy its **Internal Integration Secret** — this is `NOTION_API_KEY`.
2. **Create or choose a parent page** in Notion under which the database will live.
   Copy its page ID (the 32-char id in the page URL) — this is `NOTION_PARENT_PAGE_ID`.
3. **Share that page with the integration**: open the page > `...` menu > Connections
   (or Add connections) > select your integration.
4. Set `NOTION_API_KEY` and `NOTION_PARENT_PAGE_ID` in `.env` (or as GitHub secrets)
   and run with `--notion`:
   ```
   python -m src.cli run --notion
   ```

The database is auto-created under the parent page on first run. Its id is written
to `notion_state.json` at the repo root and printed to stdout — save it as the
`NOTION_DATABASE_ID` secret/env var so future runs reuse the same database instead
of creating a new one each time (the GitHub Action commits `notion_state.json`
automatically so this happens without manual copying).

Columns created in the Notion database:

- `Question` (title) — question text, truncated to 200 chars
- `Date` (date) — UTC date derived from the Slack message timestamp
- `Category` (select) — heuristic category
- `LLM Category` (select) — LLM-assigned category, if LLM analysis is enabled
- `Subtopic` (rich text) — LLM-assigned subtopic, if enabled
- `Difficulty` (number) — LLM difficulty score 1-5, if enabled
- `Automatable` (checkbox) — whether a doc/FAQ/bot could answer it
- `Reply Count` (number)
- `First Reply Latency (min)` (number)
- `Slack User` (rich text)
- `Message TS` (rich text) — used as the upsert key
- `Permalink` (url)

If `NOTION_API_KEY` is unset, `--notion` is skipped with a warning rather than
failing the run.

## Daily GitHub Action

`.github/workflows/daily-partnerships-analysis.yml` runs the pipeline daily
(`0 1 * * *` UTC = 17:00 PST; GitHub cron doesn't observe DST, so this is
18:00 during PDT) with a 1-day look-back, and commits the new report + analysis
cache (and `notion_state.json`) back to the repo if anything changed. It also
supports manual runs via `workflow_dispatch` with a `days` input for on-demand
look-backs (e.g. a 30-day backfill).

Required repository secrets:

- `SLACK_BOT_TOKEN`
- `SLACK_CHANNEL_ID`
- `ANTHROPIC_API_KEY` (optional — omit to run heuristics-only)
- `NOTION_API_KEY`, `NOTION_PARENT_PAGE_ID`, `NOTION_DATABASE_ID` (optional —
  omit to skip Notion output; `NOTION_DATABASE_ID` is only needed after the
  first run creates the database)

## Tests

```
make test
```

`src/analyze.py` is pure (no network calls) and unit-tested against fixture message
dicts in `tests/`.
