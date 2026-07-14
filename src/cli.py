"""CLI entrypoint: scrape / analyze / report / run.

Usage:
    python -m src.cli run --days 30
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from src.config import Config, estimate_cost_usd
from src.slack_client import SlackScraper, load_export
from src import store
from src.analyze import analyze as run_analyze, _ts_to_date
from src.llm import classify_questions, summarize_trends
from src.report import render_markdown
from src.dashboard import aggregate as run_aggregate, render_html
from src import automation_page

BASE_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = BASE_DIR / "reports"
SITE_DIR = BASE_DIR / "site"


def _date_strs_for_range(days: int, end_date: Optional[datetime] = None) -> List[str]:
    end_date = end_date or datetime.now(timezone.utc)
    return [
        (end_date - timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in range(days - 1, -1, -1)
    ]


def _group_by_date(messages: List[dict]) -> dict:
    grouped: dict = {}
    for msg in messages:
        ts = msg.get("ts")
        try:
            date_str = datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            date_str = "unknown"
        grouped.setdefault(date_str, []).append(msg)
    return grouped


def cmd_scrape(config: Config, export_dir: Optional[str] = None, seed_file: Optional[str] = None) -> List[str]:
    """Fetch messages and cache them per-day under data/raw/. Returns list of date strs written."""
    if seed_file:
        try:
            with open(seed_file, "r", encoding="utf-8") as f:
                messages = json.load(f)
        except (OSError, ValueError) as exc:
            print(f"ERROR: could not read --seed-file {seed_file}: {exc}", file=sys.stderr)
            sys.exit(1)
    elif export_dir:
        messages = load_export(export_dir)
    else:
        if not config.slack_token:
            print("ERROR: SLACK_BOT_TOKEN not set and no --export-dir provided.", file=sys.stderr)
            sys.exit(1)
        oldest_ts = time.time() - (config.lookback_days * 86400)
        scraper = SlackScraper(config.slack_token)
        messages = scraper.fetch_channel_history(config.channel_id, oldest_ts)

    grouped = _group_by_date(messages)
    written = []
    for date_str, day_messages in sorted(grouped.items()):
        store.write_raw(day_messages, date_str)
        written.append(date_str)
    return written


def _split_analysis_by_day(analysis: dict) -> None:
    """Slice a whole-window analysis dict into one self-contained file per
    calendar date under data/analysis_by_day/, keyed by the date the data is
    *about* rather than the date the CLI ran, so re-running any overlapping
    window only ever overwrites that date's own file."""
    for date_str, day_counts in analysis.get("per_day", {}).items():
        if date_str == "unknown":
            continue
        day_questions = [
            q for q in analysis.get("questions", []) if _ts_to_date(q.get("ts")) == date_str
        ]
        latencies = [
            q["first_reply_latency_sec"] for q in day_questions if q.get("first_reply_latency_sec") is not None
        ]
        day_slice = {
            "per_day": {date_str: day_counts},
            "category_distribution": dict(Counter(q.get("category") for q in day_questions)),
            "top_askers": Counter(q.get("user") for q in day_questions).most_common(10),
            "response": {
                "median_first_reply_latency_sec": statistics.median(latencies) if latencies else None,
                "unanswered_question_count": sum(1 for q in day_questions if (q.get("reply_count", 0) or 0) == 0),
            },
            "questions": day_questions,
        }
        store.write_json(day_slice, store.daily_analysis_path(date_str))


def cmd_analyze(config: Config, end_date_str: str) -> dict:
    """Read cached raw messages for the look-back window ending end_date_str, analyze, cache result."""
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    # Scrape's rolling oldest_ts window can touch one UTC calendar date earlier
    # than a naive "lookback_days back from end_date" range, so pull in one
    # extra boundary date to avoid dropping cached messages.
    date_strs = _date_strs_for_range(config.lookback_days + 1, end_date=end_date)

    all_messages: List[dict] = []
    for date_str in date_strs:
        all_messages.extend(store.read_raw(date_str))

    analysis = run_analyze(all_messages, config)

    # Accumulate LLM usage across all calls
    total_usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}

    if config.llm_enabled:
        analysis["questions"], classify_usage = classify_questions(analysis["questions"], config)
        total_usage["calls"] += classify_usage.get("calls", 0)
        total_usage["input_tokens"] += classify_usage.get("input_tokens", 0)
        total_usage["output_tokens"] += classify_usage.get("output_tokens", 0)
        total_usage["cache_read_input_tokens"] += classify_usage.get("cache_read_input_tokens", 0)

    store.write_json(analysis, store.analysis_path(end_date_str))
    _split_analysis_by_day(analysis)

    # Write ledger entry if any LLM calls were made
    if config.llm_enabled and total_usage["calls"] > 0:
        dates_analyzed = sorted([
            date_str for date_str in analysis.get("per_day", {}).keys() if date_str != "unknown"
        ])
        est_cost_usd = estimate_cost_usd(
            config.llm_model,
            total_usage["input_tokens"],
            total_usage["output_tokens"],
            total_usage["cache_read_input_tokens"],
        )
        store.append_spend_ledger(
            run_at=datetime.now(timezone.utc).isoformat() + "Z",
            command=f"analyze --days {config.lookback_days}",
            model=config.llm_model,
            calls=total_usage["calls"],
            input_tokens=total_usage["input_tokens"],
            output_tokens=total_usage["output_tokens"],
            cache_read_input_tokens=total_usage["cache_read_input_tokens"],
            est_cost_usd=est_cost_usd,
            dates_analyzed=dates_analyzed,
        )

    return analysis


def cmd_report(config: Config, end_date_str: str) -> Path:
    analysis = store.read_json(store.analysis_path(end_date_str))
    if analysis is None:
        print(f"ERROR: no analysis found for {end_date_str}. Run `analyze` first.", file=sys.stderr)
        sys.exit(1)

    llm_summary = ""
    if config.llm_enabled:
        llm_summary, _ = summarize_trends(analysis, config)

    markdown = render_markdown(analysis, llm_summary, config, end_date_str)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"partnerships-{end_date_str}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    return report_path


def _merge_daily_for_dashboard(daily_files: List[dict]) -> dict:
    """Merge per-day analysis files (each disjoint by date, by construction of
    _split_analysis_by_day) into a single pseudo-file so dashboard.aggregate()'s
    _most_recent() picks up the full history for category/asker/difficulty
    panels instead of just the latest day."""
    merged_per_day: dict = {}
    merged_questions: List[dict] = []
    for f in daily_files:
        merged_per_day.update(f.get("per_day") or {})
        merged_questions.extend(f.get("questions") or [])

    latencies = [
        q["first_reply_latency_sec"] for q in merged_questions if q.get("first_reply_latency_sec") is not None
    ]
    return {
        "per_day": merged_per_day,
        "category_distribution": dict(Counter(q.get("category") for q in merged_questions)),
        "top_askers": Counter(q.get("user") for q in merged_questions).most_common(10),
        "response": {
            "median_first_reply_latency_sec": statistics.median(latencies) if latencies else None,
            "unanswered_question_count": sum(1 for q in merged_questions if (q.get("reply_count", 0) or 0) == 0),
        },
        "questions": merged_questions,
    }


def cmd_dashboard() -> Path:
    """Aggregate all cached analysis files into site/index.html and
    site/automation.html from the same merged model inputs. Always writes
    valid pages, even with zero analysis files (empty-state). Returns the
    index.html path (automation.html is written alongside it)."""
    analysis_files = store.read_all_daily_analysis()
    merged = _merge_daily_for_dashboard(analysis_files)

    user_directory_path = BASE_DIR / "data" / "user_directory.json"
    try:
        with open(user_directory_path, "r", encoding="utf-8") as f:
            user_directory = json.load(f)
    except (OSError, ValueError):
        user_directory = {}

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    model = run_aggregate([merged] if analysis_files else [], user_directory=user_directory)
    markup = render_html(model, generated_at)

    automation_model = automation_page.build_model(
        merged if analysis_files else {}, user_directory=user_directory
    )
    automation_markup = automation_page.render_html(automation_model, generated_at)

    SITE_DIR.mkdir(parents=True, exist_ok=True)
    index_path = SITE_DIR / "index.html"
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(markup)

    automation_path = SITE_DIR / "automation.html"
    with open(automation_path, "w", encoding="utf-8") as f:
        f.write(automation_markup)

    return index_path


def cmd_notion(config: Config, analysis: dict) -> None:
    if not config.notion_token:
        print("Skipping Notion: NOTION_API_KEY not set.", file=sys.stderr)
        return

    creating = not config.notion_database_id
    if creating and not config.notion_parent_page_id:
        print("Skipping Notion: NOTION_PARENT_PAGE_ID not set (required to create a new database).", file=sys.stderr)
        return

    from src import notion_writer

    try:
        database_id = notion_writer.write_analysis(
            analysis, config.notion_token, config.notion_parent_page_id, config.notion_database_id
        )
    except Exception as exc:
        print(f"ERROR: Notion write failed: {exc}", file=sys.stderr)
        return

    if creating:
        from src.config import write_notion_state
        write_notion_state(database_id)
        print(f"Created new Notion database: {database_id}")
        print("Save this as NOTION_DATABASE_ID secret (also written to notion_state.json).")


def cmd_post(config: Config, report_path: Path) -> None:
    if not config.post_channel_id or not config.slack_token:
        print("Skipping post: SLACK_POST_CHANNEL_ID or SLACK_BOT_TOKEN not set.", file=sys.stderr)
        return
    text = report_path.read_text(encoding="utf-8")
    # Slack messages have a size limit; truncate defensively.
    if len(text) > 39000:
        text = text[:39000] + "\n\n_...truncated, see full report in repo._"
    scraper = SlackScraper(config.slack_token)
    scraper.post_message(config.post_channel_id, text)


def _add_global_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--days", type=int, default=None, help="Override lookback_days from config.")
    p.add_argument("--config", type=str, default=None, help="Path to config.yaml (default: repo root).")
    p.add_argument("--post", action="store_true", help="Post the rendered digest to SLACK_POST_CHANNEL_ID.")
    p.add_argument("--notion", action="store_true", help="Write analyzed questions to the Notion database.")
    p.add_argument("--export-dir", type=str, default=None, help="Use an offline Slack export directory instead of the live API.")
    p.add_argument("--seed-file", type=str, default=None, help="Load a pre-normalized JSON message list from PATH (same shape as python-backfill/merged_messages.json) into data/raw/ instead of scraping Slack live.")
    p.add_argument("--dashboard", action="store_true", help="Also (re)build the static dashboard at site/index.html.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="partnerships-analysis", description="Scrape and analyze #partnerships Slack activity.")
    _add_global_args(parser)

    subparsers = parser.add_subparsers(dest="command")
    for name, help_text in (
        ("scrape", "Fetch and cache raw messages."),
        ("analyze", "Analyze cached raw messages."),
        ("report", "Render the markdown report from cached analysis."),
        ("run", "Run scrape -> analyze -> report (default)."),
        ("dashboard", "Build the static dashboard (site/index.html) from all cached analysis files."),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        _add_global_args(sub)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "run"

    config = Config.resolve(config_path=args.config, days_override=args.days)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if command == "scrape":
        written = cmd_scrape(config, export_dir=args.export_dir, seed_file=args.seed_file)
        print(f"Scraped and cached raw messages for: {', '.join(written) if written else '(none)'}")
        return 0

    if command == "analyze":
        cmd_analyze(config, today_str)
        print(f"Analysis cached at {store.analysis_path(today_str)}")
        return 0

    if command == "report":
        report_path = cmd_report(config, today_str)
        if args.notion:
            analysis = store.read_json(store.analysis_path(today_str))
            if analysis is not None:
                cmd_notion(config, analysis)
        print(f"Report written to {report_path}")
        return 0

    if command == "dashboard":
        index_path = cmd_dashboard()
        print(f"Dashboard written to {index_path}")
        return 0

    if command == "run":
        cmd_scrape(config, export_dir=args.export_dir, seed_file=args.seed_file)
        cmd_analyze(config, today_str)
        report_path = cmd_report(config, today_str)
        if args.post:
            cmd_post(config, report_path)
        if args.notion:
            analysis = store.read_json(store.analysis_path(today_str))
            if analysis is not None:
                cmd_notion(config, analysis)
        if args.dashboard:
            index_path = cmd_dashboard()
            print(f"Dashboard written to {index_path}")
        print(f"Report written to {report_path}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
