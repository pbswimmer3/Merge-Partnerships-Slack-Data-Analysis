from src.dashboard import aggregate, render_html


def _file(per_day, category_distribution=None, top_askers=None, questions=None, summary=None):
    data = {
        "totals": {
            "message_count": sum(v["messages"] for v in per_day.values()),
            "question_count": sum(v["questions"] for v in per_day.values()),
            "thread_count": 0,
            "unique_users": 1,
        },
        "per_day": per_day,
        "category_distribution": category_distribution or {},
        "top_askers": top_askers or [],
        "response": {"median_first_reply_latency_sec": None, "unanswered_question_count": 0},
        "questions": questions or [],
    }
    if summary is not None:
        data["llm_summary"] = summary
    return data


def test_aggregate_merges_timeseries_across_files_summing_collisions():
    file_a = _file({
        "2026-06-01": {"messages": 5, "questions": 2},
        "2026-06-02": {"messages": 3, "questions": 1},
    })
    file_b = _file({
        "2026-06-02": {"messages": 1, "questions": 1},  # collision with file_a: sum
        "2026-06-03": {"messages": 4, "questions": 0},
    })
    model = aggregate([file_a, file_b])
    ts = {row["date"]: row for row in model["timeseries"]}
    assert ts["2026-06-01"]["messages"] == 5
    assert ts["2026-06-02"]["messages"] == 4  # 3 + 1
    assert ts["2026-06-02"]["questions"] == 2  # 1 + 1
    assert ts["2026-06-03"]["messages"] == 4
    assert model["date_range"] == {"start": "2026-06-01", "end": "2026-06-03"}
    assert model["kpis"]["total_messages"] == 13
    assert model["kpis"]["total_questions"] == 4


def test_aggregate_categories_from_most_recent_file_sorted_desc():
    older = _file(
        {"2026-06-01": {"messages": 1, "questions": 1}},
        category_distribution={"pricing": 9},
    )
    newer = _file(
        {"2026-06-05": {"messages": 4, "questions": 3}},
        category_distribution={"api_technical": 2, "pricing": 5, "billing": 8},
    )
    model = aggregate([older, newer])
    names = [c["name"] for c in model["categories"]]
    assert names == ["billing", "pricing", "api_technical"]
    total = sum(c["count"] for c in model["categories"])
    assert round(sum(c["pct"] for c in model["categories"]), 1) == 100.0
    assert total == 15


def test_aggregate_automation_ranking_prefers_high_automatable_and_volume():
    questions = [
        {"category": "billing", "difficulty": 2, "automatable": True},
        {"category": "billing", "difficulty": 2, "automatable": True},
        {"category": "billing", "difficulty": 1, "automatable": True},
        {"category": "custom_integration", "difficulty": 5, "automatable": False},
        {"category": "custom_integration", "difficulty": 4, "automatable": False},
    ]
    f = _file({"2026-06-05": {"messages": 5, "questions": 5}}, questions=questions)
    model = aggregate([f])

    by_cat = {row["category"]: row for row in model["automation_opportunities"]}
    assert by_cat["billing"]["automatable_pct"] == 100.0
    assert by_cat["custom_integration"]["automatable_pct"] == 0.0
    # billing should rank ahead of custom_integration (higher automatable_pct)
    order = [row["category"] for row in model["automation_opportunities"]]
    assert order.index("billing") < order.index("custom_integration")
    assert model["kpis"]["automation_candidate_count"] >= 1


def test_aggregate_empty_data_is_safe():
    model = aggregate([])
    assert model["kpis"]["total_messages"] == 0
    assert model["kpis"]["total_questions"] == 0
    assert model["kpis"]["unanswered_pct"] == 0.0
    assert model["kpis"]["median_first_reply_min"] is None
    assert model["timeseries"] == []
    assert model["categories"] == []
    assert model["automation_opportunities"] == []
    assert model["date_range"] == {"start": None, "end": None}


def test_aggregate_no_llm_data_sets_avg_difficulty_none():
    questions = [
        {"category": "other", "difficulty": None, "automatable": None},
        {"category": "other", "difficulty": None, "automatable": None},
    ]
    f = _file({"2026-06-01": {"messages": 2, "questions": 2}}, questions=questions)
    model = aggregate([f])
    assert model["difficulty_by_category"][0]["avg_difficulty"] is None
    assert model["difficulty_by_category"][0]["automatable_pct"] is None
    # ranking falls back to volume-only rationale
    assert "volume" in model["automation_opportunities"][0]["rationale"]


def test_aggregate_passes_through_summary_from_most_recent_file():
    older = _file({"2026-06-01": {"messages": 1, "questions": 0}}, summary="old summary")
    newer = _file({"2026-06-05": {"messages": 1, "questions": 0}}, summary="new summary")
    model = aggregate([older, newer])
    assert model["summary"] == "new summary"


def test_aggregate_missing_summary_defaults_empty_string():
    f = _file({"2026-06-01": {"messages": 1, "questions": 0}})
    model = aggregate([f])
    assert model["summary"] == ""


def test_aggregate_top_askers_uses_user_directory_display_name():
    f = _file(
        {"2026-06-01": {"messages": 1, "questions": 1}},
        top_askers=[("U1", 3)],
    )
    model = aggregate([f], user_directory={"U1": "Alice"})
    asker = model["top_askers"][0]
    assert asker["user"] == "U1"
    assert asker["display_name"] == "Alice"


def test_aggregate_top_askers_falls_back_to_raw_id_when_missing_from_directory():
    f = _file(
        {"2026-06-01": {"messages": 1, "questions": 1}},
        top_askers=[("U2", 1)],
    )
    model = aggregate([f], user_directory={"U1": "Alice"})
    asker = model["top_askers"][0]
    assert asker["user"] == "U2"
    assert asker["display_name"] == "U2"


def test_render_html_smoke():
    f = _file(
        {"2026-06-01": {"messages": 3, "questions": 2}},
        category_distribution={"billing": 2},
        top_askers=[("U1", 2)],
        questions=[{"category": "billing", "difficulty": 2, "automatable": True}],
    )
    model = aggregate([f])
    markup = render_html(model, "2026-06-02T00:00:00Z")
    assert "<html" in markup
    assert 'id="chart-volume"' in markup
    assert "#partnerships" in markup


def test_render_html_empty_state_smoke():
    model = aggregate([])
    markup = render_html(model, "2026-06-02T00:00:00Z")
    assert "<html" in markup
    assert "empty-note" in markup
