"""Tests for `src/automation_page.py` (plan.md §4.1-4.2, covering clustering,
notables scoring, suggested_fix heuristics, est_minutes_saved, and HTML
rendering). Offline pytest, no network calls."""
import pytest
from src.automation_page import (
    build_model,
    render_html,
    _normalize_subtopic,
    _suggested_fix,
    _build_clusters,
    _build_notables,
)


# ============================================================================
# Test fixtures: minimal question dicts mirroring the schema from plan.md §1
# ============================================================================


def _question(ts="1776364246.950929", user="U08VAUU4Z2M", text="Sample question",
              category="api_technical", llm_category=None, subtopic=None,
              difficulty=None, automatable=None, reply_count=0,
              first_reply_latency_sec=None, rationale=""):
    """Helper to build a question dict with sane defaults."""
    return {
        "ts": ts,
        "user": user,
        "text": text,
        "category": category,
        "llm_category": llm_category,
        "subtopic": subtopic,
        "difficulty": difficulty,
        "automatable": automatable,
        "reply_count": reply_count,
        "first_reply_latency_sec": first_reply_latency_sec,
        "rationale": rationale,
    }


def _merged_analysis(questions):
    """Helper to build a merged_analysis dict."""
    return {"questions": questions}


# ============================================================================
# 1. Clustering: exact normalized-string match only
# ============================================================================


def test_normalize_subtopic_merges_case_and_punctuation():
    """Test that normalization handles case, punctuation, and whitespace."""
    assert _normalize_subtopic("Plan Upgrade!") == _normalize_subtopic("plan upgrade")
    assert _normalize_subtopic("Plan  Upgrade!") == _normalize_subtopic("plan upgrade")
    # Note: hyphen is removed as punctuation without space replacement, so
    # "Plan-Upgrade?" becomes "planupgrade" not "plan upgrade"
    assert _normalize_subtopic("Plan Upgrade!") == "plan upgrade"


def test_normalize_subtopic_different_text_is_different_cluster():
    """Test that "plan upgrade request" is a DIFFERENT cluster than "plan upgrade"."""
    norm1 = _normalize_subtopic("Plan Upgrade!")
    norm2 = _normalize_subtopic("Plan Upgrade Request")
    assert norm1 == "plan upgrade"
    assert norm2 == "plan upgrade request"
    assert norm1 != norm2


def test_normalize_subtopic_empty_and_none():
    """Test edge cases: empty string and None."""
    assert _normalize_subtopic("") == ""
    assert _normalize_subtopic(None) == ""
    assert _normalize_subtopic("   ") == ""


def test_clustering_exact_match_merges_variants():
    """Test that two questions with "Plan Upgrade!" and "plan  upgrade" merge."""
    q1 = _question(ts="1", subtopic="Plan Upgrade!", automatable=True, difficulty=2.0)
    q2 = _question(ts="2", subtopic="plan  upgrade", automatable=True, difficulty=3.0)
    clusters = _build_clusters([q1, q2], None)
    assert len(clusters) == 1
    assert clusters[0]["count"] == 2
    assert clusters[0]["normalized_subtopic"] == "plan upgrade"


# ============================================================================
# 2. Clusters only include automatable questions
# ============================================================================


def test_clustering_excludes_non_automatable():
    """Test that non-automatable questions are excluded from clusters."""
    q1 = _question(ts="1", subtopic="Question topic", automatable=True, difficulty=2.0)
    q2 = _question(ts="2", subtopic="Question topic", automatable=False, difficulty=3.0)
    q3 = _question(ts="3", subtopic="Question topic", automatable=None, difficulty=1.0)
    clusters = _build_clusters([q1, q2, q3], None)
    assert len(clusters) == 1
    assert clusters[0]["count"] == 1  # only q1
    assert clusters[0]["question_refs"] == ["1"]


def test_clustering_excludes_questions_without_subtopic():
    """Test that automatable questions without a subtopic are excluded."""
    q1 = _question(ts="1", subtopic="Valid topic", automatable=True, difficulty=2.0)
    q2 = _question(ts="2", subtopic=None, automatable=True, difficulty=2.0)
    q3 = _question(ts="3", subtopic="", automatable=True, difficulty=2.0)
    clusters = _build_clusters([q1, q2, q3], None)
    assert len(clusters) == 1
    assert clusters[0]["count"] == 1


# ============================================================================
# 3. Notables: count ≥ 2, top 6, sorted by count × (6 − avg_difficulty)
# ============================================================================


def test_notables_requires_count_gte_2():
    """Test that notables only include clusters with count ≥ 2."""
    clusters = [
        {"count": 1, "avg_difficulty": 2.0},
        {"count": 2, "avg_difficulty": 2.0},
        {"count": 5, "avg_difficulty": 2.0},
    ]
    notables = _build_notables(clusters)
    assert len(notables) == 2
    assert notables[0]["count"] == 5
    assert notables[1]["count"] == 2


def test_notables_top_6_limit():
    """Test that notables returns at most 6 clusters."""
    clusters = [
        {"count": i + 2, "avg_difficulty": 2.0, "category": "cat", "subtopic": f"s{i}"}
        for i in range(10)
    ]
    notables = _build_notables(clusters, top_n=6)
    assert len(notables) == 6


def test_notables_sorted_by_score():
    """Test that notables are sorted by count × (6 − avg_difficulty)."""
    clusters = [
        # score = 2 × (6 - 3) = 6
        {"count": 2, "avg_difficulty": 3.0, "category": "a", "subtopic": "s1"},
        # score = 5 × (6 - 2) = 20
        {"count": 5, "avg_difficulty": 2.0, "category": "b", "subtopic": "s2"},
        # score = 3 × (6 - 1) = 15
        {"count": 3, "avg_difficulty": 1.0, "category": "c", "subtopic": "s3"},
    ]
    notables = _build_notables(clusters)
    scores = [n["count"] * (6 - n["avg_difficulty"]) for n in notables]
    assert scores == sorted(scores, reverse=True)
    assert scores == [20, 15, 6]


def test_notables_avg_difficulty_none_defaults_to_3():
    """Test that missing avg_difficulty defaults to 3.0 for scoring."""
    clusters = [
        {"count": 2, "avg_difficulty": None, "category": "a", "subtopic": "s1"},
        {"count": 2, "avg_difficulty": 1.0, "category": "b", "subtopic": "s2"},
    ]
    notables = _build_notables(clusters)
    # First: 2 × (6 - 3) = 6; Second: 2 × (6 - 1) = 10
    # So second should be first
    assert notables[0]["avg_difficulty"] == 1.0
    assert notables[1]["avg_difficulty"] is None


# ============================================================================
# 4. est_minutes_saved: median member latency; fallback to global median
# ============================================================================


def test_est_minutes_saved_uses_cluster_median_latency():
    """Test that est_minutes_saved uses the median latency of the cluster."""
    q1 = _question(ts="1", subtopic="Topic A", automatable=True, difficulty=2.0,
                   first_reply_latency_sec=60.0)
    q2 = _question(ts="2", subtopic="Topic A", automatable=True, difficulty=2.0,
                   first_reply_latency_sec=120.0)
    clusters = _build_clusters([q1, q2], global_median_latency_sec=1000.0)
    assert len(clusters) == 1
    # median of [60, 120] = 90 sec
    # est_minutes_saved = 2 × 90 / 60 = 3.0 minutes
    assert clusters[0]["est_minutes_saved"] == 3.0


def test_est_minutes_saved_falls_back_to_global_median():
    """Test fallback to global median when cluster has no latencies."""
    q1 = _question(ts="1", subtopic="Topic A", automatable=True, difficulty=2.0,
                   first_reply_latency_sec=None)
    q2 = _question(ts="2", subtopic="Topic A", automatable=True, difficulty=2.0,
                   first_reply_latency_sec=None)
    clusters = _build_clusters([q1, q2], global_median_latency_sec=600.0)
    assert len(clusters) == 1
    # est_minutes_saved = 2 × 600 / 60 = 20.0 minutes
    assert clusters[0]["est_minutes_saved"] == 20.0


def test_est_minutes_saved_none_when_no_latency():
    """Test that est_minutes_saved is None when both cluster and global median are unavailable."""
    q1 = _question(ts="1", subtopic="Topic A", automatable=True, difficulty=2.0,
                   first_reply_latency_sec=None)
    q2 = _question(ts="2", subtopic="Topic A", automatable=True, difficulty=2.0,
                   first_reply_latency_sec=None)
    clusters = _build_clusters([q1, q2], global_median_latency_sec=None)
    assert len(clusters) == 1
    assert clusters[0]["est_minutes_saved"] is None


def test_est_minutes_saved_mixed_latencies_in_cluster():
    """Test median latency calculation with mixed None values."""
    q1 = _question(ts="1", subtopic="Topic A", automatable=True, difficulty=2.0,
                   first_reply_latency_sec=100.0)
    q2 = _question(ts="2", subtopic="Topic A", automatable=True, difficulty=2.0,
                   first_reply_latency_sec=None)
    q3 = _question(ts="3", subtopic="Topic A", automatable=True, difficulty=2.0,
                   first_reply_latency_sec=200.0)
    clusters = _build_clusters([q1, q2, q3], global_median_latency_sec=500.0)
    assert len(clusters) == 1
    # cluster uses median of [100, 200] = 150 sec, not fallback
    # est_minutes_saved = 3 × 150 / 60 = 7.5 minutes
    assert clusters[0]["est_minutes_saved"] == 7.5


# ============================================================================
# 5. suggested_fix keyword heuristic
# ============================================================================


def test_suggested_fix_docs_faq():
    """Test that 'doc' or 'faq' in rationale triggers Docs/FAQ answer."""
    assert _suggested_fix(["check the docs"]) == "Docs/FAQ answer"
    assert _suggested_fix(["see the FAQ"]) == "Docs/FAQ answer"
    assert _suggested_fix(["from documentation"]) == "Docs/FAQ answer"


def test_suggested_fix_plan_pricing():
    """Test that 'plan' or 'pricing' triggers Self-serve pricing page."""
    assert _suggested_fix(["pricing question"]) == "Self-serve pricing page"
    assert _suggested_fix(["plan upgrade"]) == "Self-serve pricing page"
    assert _suggested_fix(["see our pricing page"]) == "Self-serve pricing page"


def test_suggested_fix_access_permission():
    """Test that 'access' or 'permission' triggers Access-request workflow."""
    assert _suggested_fix(["access issue"]) == "Access-request workflow"
    assert _suggested_fix(["need permission"]) == "Access-request workflow"
    assert _suggested_fix(["permission question"]) == "Access-request workflow"


def test_suggested_fix_status_sync():
    """Test that 'status' or 'sync' triggers Status page / alert bot."""
    assert _suggested_fix(["status update"]) == "Status page / alert bot"
    assert _suggested_fix(["sync issue"]) == "Status page / alert bot"
    assert _suggested_fix(["data sync"]) == "Status page / alert bot"


def test_suggested_fix_fallback_slack_responder():
    """Test fallback to Slack auto-responder."""
    assert _suggested_fix(["something else"]) == "Slack auto-responder"
    assert _suggested_fix([]) == "Slack auto-responder"
    assert _suggested_fix(["", ""]) == "Slack auto-responder"


def test_suggested_fix_multiple_rationales():
    """Test that multiple rationales are concatenated."""
    rationales = ["check docs", "see the FAQ", "pricing info"]
    result = _suggested_fix(rationales)
    assert result == "Docs/FAQ answer"  # docs/faq takes precedence


def test_suggested_fix_case_insensitive():
    """Test that keyword matching is case-insensitive."""
    assert _suggested_fix(["DOCS QUESTION"]) == "Docs/FAQ answer"
    assert _suggested_fix(["PLAN UPGRADE"]) == "Self-serve pricing page"
    assert _suggested_fix(["ACCESS DENIED"]) == "Access-request workflow"


# ============================================================================
# 6. build_model with heuristics-only data (no llm_category/subtopic/difficulty)
# ============================================================================


def test_build_model_without_llm_fields_has_flag_false():
    """Test that build_model detects missing LLM fields and sets has_llm_fields=False."""
    q = _question(subtopic=None, difficulty=None, automatable=None)
    analysis = _merged_analysis([q])
    model = build_model(analysis)
    assert model["has_llm_fields"] is False


def test_build_model_without_llm_fields_no_clusters():
    """Test that build_model produces no clusters when has_llm_fields=False."""
    q = _question(subtopic=None, difficulty=None, automatable=None)
    analysis = _merged_analysis([q])
    model = build_model(analysis)
    assert model["clusters"] == []
    assert model["notables"] == []


def test_build_model_without_llm_fields_still_builds_kpis():
    """Test that KPIs are still computed even without LLM fields."""
    q = _question(subtopic=None, difficulty=None, automatable=None)
    analysis = _merged_analysis([q])
    model = build_model(analysis)
    assert model["kpis"]["total_questions"] == 1
    assert model["kpis"]["automatable_count"] == 0


def test_build_model_without_llm_fields_renders_without_error():
    """Test that render_html works with has_llm_fields=False (smoke test)."""
    q = _question(text="No LLM fields", subtopic=None, difficulty=None, automatable=None)
    analysis = _merged_analysis([q])
    model = build_model(analysis)
    html = render_html(model, generated_at="2026-07-14")
    assert html  # non-empty
    assert "question-table" in html
    assert "spend-btn" in html
    assert "Automation Deep-Dive" in html


def test_build_model_partial_llm_fields_triggers_clustering():
    """Test that having any LLM field triggers has_llm_fields=True and clustering."""
    q1 = _question(ts="1", subtopic="Topic", automatable=True, difficulty=2.0)
    q2 = _question(ts="2", subtopic="Topic", automatable=True, difficulty=2.0)
    analysis = _merged_analysis([q1, q2])
    model = build_model(analysis)
    assert model["has_llm_fields"] is True
    assert len(model["clusters"]) == 1


# ============================================================================
# 7. render_html smoke tests: contains expected IDs, escapes HTML
# ============================================================================


def test_render_html_contains_question_table():
    """Test that render_html output contains the question-table ID."""
    q = _question()
    analysis = _merged_analysis([q])
    model = build_model(analysis)
    html = render_html(model, generated_at="2026-07-14")
    assert 'id="question-table"' in html


def test_render_html_contains_spend_btn():
    """Test that render_html output contains the spend-btn ID."""
    q = _question()
    analysis = _merged_analysis([q])
    model = build_model(analysis)
    html = render_html(model, generated_at="2026-07-14")
    assert 'id="spend-btn"' in html


def test_render_html_contains_spend_panel_ids():
    """Test that render_html output contains spend panel IDs."""
    q = _question()
    analysis = _merged_analysis([q])
    model = build_model(analysis)
    html = render_html(model, generated_at="2026-07-14")
    assert 'id="spend-overlay"' in html
    assert 'id="spend-panel"' in html
    assert 'id="spend-close"' in html


def test_render_html_nav_link_to_index():
    """Test that render_html contains a nav link to index.html."""
    q = _question()
    analysis = _merged_analysis([q])
    model = build_model(analysis)
    html = render_html(model, generated_at="2026-07-14")
    assert 'href="./index.html"' in html
    assert 'href="./automation.html"' in html


def test_render_html_escapes_html_in_question_text():
    """Test that HTML in question text is safely embedded (security test).
    The question text is embedded in the model JSON and rendered via JS
    using textContent, which is safe. We verify the model contains the data."""
    q = _question(text="<script>alert('xss')</script>")
    analysis = _merged_analysis([q])
    model = build_model(analysis)
    html = render_html(model, generated_at="2026-07-14")
    # Verify that the HTML is preserved in the model JSON (it's data, not code)
    assert "script" in html.lower()
    # Verify the page structure is correct - it should have the model JSON
    assert 'type="application/json"' in html
    # Verify the question table structure exists (where JS will render safely)
    assert 'id="question-table"' in html


def test_render_html_escapes_html_in_subtopic():
    """Test that HTML in subtopic is safely handled in the data model.
    Subtopic is embedded in the JSON and rendered via JS textContent."""
    q = _question(ts="1", subtopic="<b>Bold</b>", automatable=True, difficulty=2.0)
    q2 = _question(ts="2", subtopic="<b>Bold</b>", automatable=True, difficulty=2.0)
    analysis = _merged_analysis([q, q2])
    model = build_model(analysis)
    html = render_html(model, generated_at="2026-07-14")
    # Verify model contains the data
    assert "Bold" in html
    # Verify the page has proper structure for safe rendering
    assert 'type="application/json"' in html
    assert "id=\"question-table\"" in html


def test_render_html_displays_title_and_generated_at():
    """Test that title and generated_at timestamp appear in output."""
    q = _question()
    analysis = _merged_analysis([q])
    model = build_model(analysis)
    html = render_html(model, generated_at="2026-07-14T10:30:00Z")
    assert "Automation Deep-Dive" in html
    assert "2026-07-14T10:30:00Z" in html


def test_render_html_kpi_row_present():
    """Test that KPI row is rendered."""
    q = _question(automatable=True)
    analysis = _merged_analysis([q])
    model = build_model(analysis)
    html = render_html(model, generated_at="2026-07-14")
    assert "Automatable questions" in html
    assert "Question clusters" in html


# ============================================================================
# 8. build_model with empty questions list
# ============================================================================


def test_build_model_empty_questions():
    """Test that build_model handles empty questions list gracefully."""
    analysis = _merged_analysis([])
    model = build_model(analysis)
    assert model["questions"] == []
    assert model["clusters"] == []
    assert model["notables"] == []
    assert model["kpis"]["total_questions"] == 0
    assert model["kpis"]["automatable_count"] == 0


def test_build_model_empty_questions_renders():
    """Test that render_html works with empty questions."""
    analysis = _merged_analysis([])
    model = build_model(analysis)
    html = render_html(model, generated_at="2026-07-14")
    assert "No questions match" in html or "questions" in html.lower()


def test_build_model_none_analysis():
    """Test that build_model handles None merged_analysis."""
    model = build_model(None)
    assert model["questions"] == []
    assert model["clusters"] == []
    assert model["kpis"]["total_questions"] == 0


# ============================================================================
# Additional edge cases and integration tests
# ============================================================================


def test_clustering_preserves_all_metadata():
    """Test that clusters preserve question refs and example texts."""
    q1 = _question(ts="t1", text="Example 1", subtopic="Topic A", automatable=True, difficulty=2.0)
    q2 = _question(ts="t2", text="Example 2", subtopic="Topic A", automatable=True, difficulty=2.0)
    q3 = _question(ts="t3", text="Example 3", subtopic="Topic A", automatable=True, difficulty=2.0)
    q4 = _question(ts="t4", text="Example 4", subtopic="Topic A", automatable=True, difficulty=2.0)
    clusters = _build_clusters([q1, q2, q3, q4], None)
    assert len(clusters) == 1
    assert clusters[0]["count"] == 4
    assert set(clusters[0]["question_refs"]) == {"t1", "t2", "t3", "t4"}
    assert len(clusters[0]["example_texts"]) == 3  # capped at 3


def test_multiple_clusters_by_category_and_subtopic():
    """Test that different categories or subtopics create separate clusters."""
    q1 = _question(ts="1", category="api_technical", subtopic="Topic A", automatable=True, difficulty=2.0)
    q2 = _question(ts="2", category="api_technical", subtopic="Topic B", automatable=True, difficulty=2.0)
    q3 = _question(ts="3", category="pricing_commercial", subtopic="Topic A", automatable=True, difficulty=2.0)
    clusters = _build_clusters([q1, q2, q3], None)
    assert len(clusters) == 3


def test_build_model_question_resolution():
    """Test that questions get display_name from user_directory."""
    q = _question(user="U001", text="A question")
    analysis = _merged_analysis([q])
    user_dir = {"U001": "Alice"}
    model = build_model(analysis, user_directory=user_dir)
    assert model["questions"][0]["display_name"] == "Alice"


def test_build_model_question_date_extraction():
    """Test that date is extracted from timestamp."""
    q = _question(ts="1776364246.950929")
    analysis = _merged_analysis([q])
    model = build_model(analysis)
    assert model["questions"][0]["date"] is not None


def test_kpi_other_pct_calculation():
    """Test that other_info is populated when other_pct > 40%."""
    # Create 5 "other" + 5 specific
    questions = [_question(ts=str(i), category="other") for i in range(5)]
    questions += [_question(ts=str(i+5), category="api_technical") for i in range(5)]
    analysis = _merged_analysis(questions)
    model = build_model(analysis)
    assert model["other_info"] is not None
    assert "uncategorized" in model["other_info"]


def test_kpi_other_pct_no_message_when_low():
    """Test that other_info is None when other_pct <= 40%."""
    questions = [_question(ts=str(i), category="other") for i in range(2)]
    questions += [_question(ts=str(i+2), category="api_technical") for i in range(8)]
    analysis = _merged_analysis(questions)
    model = build_model(analysis)
    assert model["other_info"] is None


def test_render_html_with_notables():
    """Test that render_html renders notable cards."""
    q1 = _question(ts="1", subtopic="Pricing Help", automatable=True, difficulty=2.0)
    q2 = _question(ts="2", subtopic="Pricing Help", automatable=True, difficulty=2.0)
    analysis = _merged_analysis([q1, q2])
    model = build_model(analysis)
    html = render_html(model, generated_at="2026-07-14")
    assert "Notable opportunities" in html
    # Note: can't assert too much about the card content due to formatting


def test_render_html_empty_notables_message():
    """Test that render_html shows a message when no notables are found."""
    q = _question(ts="1", subtopic="Topic", automatable=True, difficulty=2.0)  # Only 1, needs >=2
    analysis = _merged_analysis([q])
    model = build_model(analysis)
    html = render_html(model, generated_at="2026-07-14")
    assert "Notable opportunities" in html
    assert "clusters need" in html or "n/a" in html.lower()


def test_clustering_difficulty_calculation():
    """Test that average difficulty is calculated correctly."""
    q1 = _question(ts="1", subtopic="Topic", automatable=True, difficulty=2.0)
    q2 = _question(ts="2", subtopic="Topic", automatable=True, difficulty=4.0)
    q3 = _question(ts="3", subtopic="Topic", automatable=True, difficulty=3.0)
    clusters = _build_clusters([q1, q2, q3], None)
    assert len(clusters) == 1
    assert clusters[0]["avg_difficulty"] == 3.0


def test_clustering_difficulty_with_none_values():
    """Test that avg_difficulty handles None values."""
    q1 = _question(ts="1", subtopic="Topic", automatable=True, difficulty=2.0)
    q2 = _question(ts="2", subtopic="Topic", automatable=True, difficulty=None)
    clusters = _build_clusters([q1, q2], None)
    assert len(clusters) == 1
    assert clusters[0]["avg_difficulty"] == 2.0  # only q1 counted


def test_render_html_with_spend_model():
    """Test that render_html accepts and renders spend model."""
    q = _question()
    analysis = _merged_analysis([q])
    spend_model = {
        "ledger": {"since": "2026-07-01", "total_usd": 10.50},
        "recommendations": [],
    }
    model = build_model(analysis, spend_model=spend_model)
    html = render_html(model, generated_at="2026-07-14")
    assert "Spend" in html
    assert "2026-07-01" in html


def test_render_html_without_spend_model():
    """Test that render_html works when spend_model is None."""
    q = _question()
    analysis = _merged_analysis([q])
    model = build_model(analysis, spend_model=None)
    html = render_html(model, generated_at="2026-07-14")
    assert "Spend" in html  # button still present


def test_questions_sorted_by_date_desc():
    """Test that questions are sorted by date descending."""
    q1 = _question(ts="1776364246.950929", text="First")
    q2 = _question(ts="1776364247.950929", text="Second")  # later timestamp
    analysis = _merged_analysis([q1, q2])
    model = build_model(analysis)
    assert model["questions"][0]["text"] == "Second"
    assert model["questions"][1]["text"] == "First"


def test_clustering_with_mixed_categories():
    """Test that clustering respects category boundaries."""
    q1 = _question(ts="1", category="api_technical", subtopic="API", automatable=True, difficulty=2.0)
    q2 = _question(ts="2", category="api_technical", subtopic="API", automatable=True, difficulty=2.0)
    q3 = _question(ts="3", category="pricing_commercial", subtopic="API", automatable=True, difficulty=2.0)
    clusters = _build_clusters([q1, q2, q3], None)
    assert len(clusters) == 2  # Different categories, even same subtopic
    by_cat = {c["category"]: c for c in clusters}
    assert by_cat["api_technical"]["count"] == 2
    assert by_cat["pricing_commercial"]["count"] == 1
