from datetime import datetime, timezone

from src import store
from src.cli import _merge_daily_for_dashboard, cmd_analyze
from src.config import Config
from src.dashboard import aggregate


def _ts(date_str: str, hour: int = 12) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=hour, tzinfo=timezone.utc)
    return str(dt.timestamp())


def _config(lookback_days: int, categories: dict = None) -> Config:
    return Config(
        channel_id="C123",
        lookback_days=lookback_days,
        llm_enabled=False,
        llm_model="claude-opus-4-8",
        llm_batch_size=25,
        categories=categories or {},
        slack_token=None,
        anthropic_key=None,
        anthropic_base_url=None,
        post_channel_id=None,
        notion_token=None,
        notion_parent_page_id=None,
        notion_database_id=None,
    )


def _isolate_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(store, "ANALYSIS_DIR", tmp_path / "analysis")
    monkeypatch.setattr(store, "DAILY_ANALYSIS_DIR", tmp_path / "analysis_by_day")


def _seed_day(date_str: str, n_questions: int, n_other: int) -> None:
    messages = []
    for i in range(n_questions):
        messages.append({
            "ts": _ts(date_str, hour=9 + i),
            "user": f"U{i}",
            "text": "How do I connect the API?",
            "reply_count": 1 if i == 0 else 0,
            "replies": [{"ts": _ts(date_str, hour=10 + i), "user": "U9", "text": "here"}] if i == 0 else [],
            "permalink": None,
        })
    for i in range(n_other):
        messages.append({
            "ts": _ts(date_str, hour=15 + i),
            "user": f"U{i}",
            "text": "thanks!",
            "reply_count": 0,
            "replies": [],
            "permalink": None,
        })
    store.write_raw(messages, date_str)


def _seed_day_with_text(date_str: str, user: str, text: str) -> None:
    store.write_raw([{
        "ts": _ts(date_str),
        "user": user,
        "text": text,
        "reply_count": 0,
        "replies": [],
        "permalink": None,
    }], date_str)


def test_cmd_analyze_splits_output_per_day(tmp_path, monkeypatch):
    _isolate_store(tmp_path, monkeypatch)
    _seed_day("2026-07-08", n_questions=2, n_other=1)
    _seed_day("2026-07-09", n_questions=1, n_other=0)
    _seed_day("2026-07-10", n_questions=3, n_other=2)

    config = _config(lookback_days=3)
    cmd_analyze(config, "2026-07-10")

    day08 = store.read_json(store.daily_analysis_path("2026-07-08"))
    day09 = store.read_json(store.daily_analysis_path("2026-07-09"))
    day10 = store.read_json(store.daily_analysis_path("2026-07-10"))

    assert "totals" not in day08
    assert list(day08["per_day"].keys()) == ["2026-07-08"]
    assert day08["per_day"]["2026-07-08"]["messages"] == 4  # 2 questions + 1 other + 1 flattened reply
    assert day08["per_day"]["2026-07-08"]["questions"] == 2
    assert len(day08["questions"]) == 2

    assert day09["per_day"]["2026-07-09"]["messages"] == 2  # 1 question + 1 flattened reply
    assert day09["per_day"]["2026-07-09"]["questions"] == 1
    assert len(day09["questions"]) == 1

    assert day10["per_day"]["2026-07-10"]["messages"] == 6  # 3 questions + 2 other + 1 flattened reply
    assert day10["per_day"]["2026-07-10"]["questions"] == 3
    assert len(day10["questions"]) == 3


def test_cmd_analyze_second_run_with_smaller_window_is_idempotent_and_isolated(tmp_path, monkeypatch):
    _isolate_store(tmp_path, monkeypatch)
    _seed_day("2026-07-08", n_questions=2, n_other=1)
    _seed_day("2026-07-09", n_questions=1, n_other=0)
    _seed_day("2026-07-10", n_questions=3, n_other=2)

    cmd_analyze(_config(lookback_days=3), "2026-07-10")
    day08_before = store.read_json(store.daily_analysis_path("2026-07-08"))
    day09_before = store.read_json(store.daily_analysis_path("2026-07-09"))
    day10_before = store.read_json(store.daily_analysis_path("2026-07-10"))

    # Re-run same-day with a much smaller window overlapping only 2026-07-10.
    cmd_analyze(_config(lookback_days=1), "2026-07-10")

    day08_after = store.read_json(store.daily_analysis_path("2026-07-08"))
    day09_after = store.read_json(store.daily_analysis_path("2026-07-09"))
    day10_after = store.read_json(store.daily_analysis_path("2026-07-10"))

    assert day08_after == day08_before
    assert day09_after == day09_before
    assert day10_after == day10_before

    all_files = store.read_all_daily_analysis()
    assert len(all_files) == 3


def test_dashboard_merge_reflects_full_history_not_just_latest_day(tmp_path, monkeypatch):
    _isolate_store(tmp_path, monkeypatch)
    categories = {
        "api_technical": ["api"],
        "pricing_commercial": ["pricing"],
        "scheduling": ["calendar"],
    }
    _seed_day_with_text("2026-07-08", "alice", "How do I use the api?")
    _seed_day_with_text("2026-07-09", "bob", "What is the pricing?")
    _seed_day_with_text("2026-07-10", "carol", "Can I use the calendar?")

    cmd_analyze(_config(lookback_days=3, categories=categories), "2026-07-10")

    daily_files = store.read_all_daily_analysis()
    assert len(daily_files) == 3

    merged = _merge_daily_for_dashboard(daily_files)
    model = aggregate([merged])

    category_names = {c["name"] for c in model["categories"]}
    assert category_names == {"api_technical", "pricing_commercial", "scheduling"}

    asker_names = {a["user"] for a in model["top_askers"]}
    assert asker_names == {"alice", "bob", "carol"}

    opportunity_categories = {o["category"] for o in model["automation_opportunities"]}
    assert opportunity_categories == {"api_technical", "pricing_commercial", "scheduling"}
