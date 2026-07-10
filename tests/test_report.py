from src.report import render_markdown

CONFIG = {"channel_id": "C123", "lookback_days": 7, "llm_enabled": False}


def _sample_analysis():
    return {
        "totals": {"message_count": 3, "question_count": 2, "thread_count": 1, "unique_users": 3},
        "per_day": {
            "2026-07-09": {"messages": 1, "questions": 1},
            "2026-07-10": {"messages": 2, "questions": 1},
        },
        "category_distribution": {"api_technical": 1, "pricing_commercial": 1},
        "top_askers": [("U1", 1), ("U2", 1)],
        "response": {"median_first_reply_latency_sec": 90.0, "unanswered_question_count": 1},
        "questions": [
            {
                "ts": "123.0",
                "user": "U1",
                "text": "How do I connect the API?",
                "category": "api_technical",
                "is_question": True,
                "reply_count": 1,
                "first_reply_latency_sec": 90.0,
                "permalink": None,
            },
            {
                "ts": "456.0",
                "user": "U2",
                "text": "What is the pricing?",
                "category": "pricing_commercial",
                "is_question": True,
                "reply_count": 0,
                "first_reply_latency_sec": None,
                "permalink": None,
            },
        ],
    }


def test_render_markdown_contains_key_sections():
    md = render_markdown(_sample_analysis(), "", CONFIG, "2026-07-10")
    assert "# #partnerships Slack Analysis" in md
    assert "## Executive Summary" in md
    assert "## Trends" in md
    assert "## Top Question Categories" in md
    assert "## Question Types Breakdown" in md
    assert "## Difficulty Ranking" in md
    assert "## Automation Opportunities" in md
    assert "## Top Askers" in md
    assert "api_technical" in md
    assert "pricing_commercial" in md


def test_render_markdown_no_llm_shows_enable_note():
    md = render_markdown(_sample_analysis(), "", CONFIG, "2026-07-10")
    assert "Enable LLM analysis" in md


def test_render_markdown_with_llm_summary():
    md = render_markdown(_sample_analysis(), "Volume is steady this week.", CONFIG, "2026-07-10")
    assert "Volume is steady this week." in md
