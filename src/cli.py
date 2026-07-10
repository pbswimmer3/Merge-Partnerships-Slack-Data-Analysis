"""CLI entrypoint: scrape / analyze / report / run.

Usage:
    python -m src.cli run --days 30
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from src.config import Config
from src.slack_client import SlackScraper, load_export
from src import store
from src.analyze import analyze as run_analyze
from src.llm import classify_questions, summarize_trends
from src.report import render_markdown

BASE_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = BASE_DIR / "reports"


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


def cmd_scrape(config: Config, export_dir: Optional[str] = None) -> List[str]:
    """Fetch messages and cache them per-day under data/raw/. Returns list of date strs written."""
    if export_dir:
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

    if config.llm_enabled:
        analysis["questions"] = classify_questions(analysis["questions"], config)

    store.write_json(analysis, store.analysis_path(end_date_str))
    return analysis


def cmd_report(config: Config, end_date_str: str) -> Path:
    analysis = store.read_json(store.analysis_path(end_date_str))
    if analysis is None:
        print(f"ERROR: no analysis found for {end_date_str}. Run `analyze` first.", file=sys.stderr)
        sys.exit(1)

    llm_summary = ""
    if config.llm_enabled:
        llm_summary = summarize_trends(analysis, config)

    markdown = render_markdown(analysis, llm_summary, config, end_date_str)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"partnerships-{end_date_str}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    return report_path


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="partnerships-analysis", description="Scrape and analyze #partnerships Slack activity.")
    _add_global_args(parser)

    subparsers = parser.add_subparsers(dest="command")
    for name, help_text in (
        ("scrape", "Fetch and cache raw messages."),
        ("analyze", "Analyze cached raw messages."),
        ("report", "Render the markdown report from cached analysis."),
        ("run", "Run scrape -> analyze -> report (default)."),
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
        written = cmd_scrape(config, export_dir=args.export_dir)
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

    if command == "run":
        cmd_scrape(config, export_dir=args.export_dir)
        cmd_analyze(config, today_str)
        report_path = cmd_report(config, today_str)
        if args.post:
            cmd_post(config, report_path)
        if args.notion:
            analysis = store.read_json(store.analysis_path(today_str))
            if analysis is not None:
                cmd_notion(config, analysis)
        print(f"Report written to {report_path}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
