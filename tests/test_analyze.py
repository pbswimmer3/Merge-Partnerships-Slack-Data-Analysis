import time

from src.analyze import analyze, categorize_heuristic, is_question

CATEGORIES = {
    "api_technical": ["api", "webhook", "401"],
    "pricing_commercial": ["pricing", "price"],
}


def test_is_question_trailing_mark():
    assert is_question("Can you help with this?") is True


def test_is_question_interrogative_start():
    assert is_question("How do I set this up") is True


def test_is_question_ask_pattern():
    assert is_question("I need help with the sync") is True


def test_is_question_false_for_statement():
    assert is_question("Doing great today, thanks") is False


def test_is_question_empty():
    assert is_question("") is False


def test_categorize_heuristic_matches_keyword():
    assert categorize_heuristic("Getting a 401 on the webhook", CATEGORIES) == "api_technical"


def test_categorize_heuristic_other():
    assert categorize_heuristic("just saying hi", CATEGORIES) == "other"


def test_analyze_basic_counts():
    now = time.time()
    messages = [
        {
            "ts": str(now - 3600),
            "user": "U1",
            "text": "How do I connect the API webhook, getting a 401 error?",
            "reply_count": 1,
            "replies": [{"ts": str(now - 3500), "user": "U2", "text": "try this"}],
            "permalink": "http://x",
        },
        {
            "ts": str(now - 7200),
            "user": "U2",
            "text": "What is the pricing for this plan?",
            "reply_count": 0,
            "replies": [],
            "permalink": None,
        },
        {
            "ts": str(now - 10800),
            "user": "U3",
            "text": "just a status update, all good",
            "reply_count": 0,
            "replies": [],
        },
    ]
    config = {"categories": CATEGORIES}
    result = analyze(messages, config)

    assert result["totals"]["message_count"] == 4  # 3 roots + 1 flattened reply
    assert result["totals"]["question_count"] == 2
    assert result["totals"]["thread_count"] == 1
    assert result["totals"]["unique_users"] == 3
    assert result["category_distribution"]["api_technical"] == 1
    assert result["category_distribution"]["pricing_commercial"] == 1
    assert result["response"]["unanswered_question_count"] == 1
    assert result["response"]["median_first_reply_latency_sec"] is not None


def test_analyze_empty_messages():
    result = analyze([], {"categories": CATEGORIES})
    assert result["totals"]["message_count"] == 0
    assert result["response"]["median_first_reply_latency_sec"] is None
    assert result["top_askers"] == []


def test_analyze_counts_question_in_thread_reply():
    now = time.time()
    messages = [
        {
            "ts": str(now - 3600),
            "user": "U1",
            "text": "just an fyi, deploy went out",
            "reply_count": 1,
            "replies": [
                {
                    "ts": str(now - 3500),
                    "user": "U2",
                    "text": "Does this affect the webhook pricing?",
                    "permalink": "http://reply",
                }
            ],
            "permalink": "http://x",
        },
    ]
    config = {"categories": CATEGORIES}
    result = analyze(messages, config)

    assert result["totals"]["message_count"] == 2
    assert result["totals"]["question_count"] == 1
    assert result["totals"]["unique_users"] == 2
    assert ("U2", 1) in result["top_askers"]
    reply_question = result["questions"][0]
    assert reply_question["user"] == "U2"
    assert reply_question["reply_count"] == 0
    assert reply_question["first_reply_latency_sec"] is None
