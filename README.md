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

## Daily GitHub Action

`.github/workflows/daily-partnerships-analysis.yml` runs the pipeline daily
(`0 13 * * *` UTC) with a 1-day look-back, and commits the new report + analysis
cache back to the repo if anything changed. It also supports manual runs via
`workflow_dispatch` with a `days` input for on-demand look-backs (e.g. a 30-day
backfill).

Required repository secrets:

- `SLACK_BOT_TOKEN`
- `SLACK_CHANNEL_ID`
- `ANTHROPIC_API_KEY` (optional — omit to run heuristics-only)

## Tests

```
make test
```

`src/analyze.py` is pure (no network calls) and unit-tested against fixture message
dicts in `tests/`.
