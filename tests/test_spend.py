"""Tests for spend tracking: cost estimation, ledger management, and the
spend model + recommendations builder (plan.md §5, Phase C3/C4)."""
import json
from pathlib import Path

from src.config import estimate_cost_usd
from src import store
from src import spend as spend_module


class TestEstimateCostUsd:
    """Tests for estimate_cost_usd function."""

    def test_estimate_cost_opus_basic(self):
        """Test cost calculation for claude-opus-4-8."""
        # 1M input tokens at $5, 1M output tokens at $25 = $30 total
        cost = estimate_cost_usd("claude-opus-4-8", 1_000_000, 1_000_000)
        assert cost == 30.0

    def test_estimate_cost_sonnet_basic(self):
        """Test cost calculation for claude-sonnet-5."""
        # 1M input tokens at $3, 1M output tokens at $15 = $18 total
        cost = estimate_cost_usd("claude-sonnet-5", 1_000_000, 1_000_000)
        assert cost == 18.0

    def test_estimate_cost_haiku_basic(self):
        """Test cost calculation for claude-haiku-4-5."""
        # 1M input tokens at $1, 1M output tokens at $5 = $6 total
        cost = estimate_cost_usd("claude-haiku-4-5", 1_000_000, 1_000_000)
        assert cost == 6.0

    def test_estimate_cost_with_cache_read(self):
        """Test cost calculation with cache-read tokens (0.1× input rate)."""
        # 1M input at $5/MTok + 1M output at $25/MTok + 1M cache_read at $0.50/MTok
        cost = estimate_cost_usd("claude-opus-4-8", 1_000_000, 1_000_000, 1_000_000)
        assert cost == 30.5

    def test_estimate_cost_partial_tokens(self):
        """Test cost calculation with fractional token counts."""
        # 1000 input, 2000 output, 500 cache_read
        # (1000/1M)*5 + (2000/1M)*25 + (500/1M)*0.5
        # = 0.005 + 0.05 + 0.00025 = 0.05525
        cost = estimate_cost_usd("claude-opus-4-8", 1000, 2000, 500)
        assert abs(cost - 0.05525) < 0.00001

    def test_estimate_cost_zero_tokens(self):
        """Test cost calculation with zero tokens."""
        cost = estimate_cost_usd("claude-opus-4-8", 0, 0, 0)
        assert cost == 0.0

    def test_estimate_cost_unknown_model_returns_none(self):
        """Test that unknown models return None, never raise."""
        cost = estimate_cost_usd("claude-unknown-model", 1000, 2000)
        assert cost is None

    def test_estimate_cost_unknown_model_with_cache_read(self):
        """Test that unknown models return None even with cache-read tokens."""
        cost = estimate_cost_usd("gpt-4", 1000, 2000, 500)
        assert cost is None


class TestSpendLedger:
    """Tests for spend ledger append and management."""

    def test_append_spend_ledger_creates_file(self, tmp_path, monkeypatch):
        """Test that append_spend_ledger creates a ledger file."""
        monkeypatch.setattr(store, "SPEND_LEDGER_DIR", tmp_path / "spend" / "ledger")

        store.append_spend_ledger(
            run_at="2026-07-14T01:00:00Z",
            command="analyze --days 1",
            model="claude-opus-4-8",
            calls=4,
            input_tokens=51234,
            output_tokens=8021,
            cache_read_input_tokens=0,
            est_cost_usd=0.4568,
            dates_analyzed=["2026-07-13"],
        )

        ledger_path = tmp_path / "spend" / "ledger" / "2026-07.json"
        assert ledger_path.exists()

        with open(ledger_path, "r") as f:
            ledger = json.load(f)

        assert len(ledger) == 1
        assert ledger[0]["run_at"] == "2026-07-14T01:00:00Z"
        assert ledger[0]["command"] == "analyze --days 1"
        assert ledger[0]["model"] == "claude-opus-4-8"
        assert ledger[0]["calls"] == 4
        assert ledger[0]["input_tokens"] == 51234
        assert ledger[0]["output_tokens"] == 8021
        assert ledger[0]["cache_read_input_tokens"] == 0
        assert ledger[0]["est_cost_usd"] == 0.4568
        assert ledger[0]["dates_analyzed"] == ["2026-07-13"]

    def test_append_spend_ledger_appends_to_existing(self, tmp_path, monkeypatch):
        """Test that append_spend_ledger appends to an existing ledger."""
        monkeypatch.setattr(store, "SPEND_LEDGER_DIR", tmp_path / "spend" / "ledger")

        # First entry
        store.append_spend_ledger(
            run_at="2026-07-14T01:00:00Z",
            command="analyze --days 1",
            model="claude-opus-4-8",
            calls=4,
            input_tokens=51234,
            output_tokens=8021,
            cache_read_input_tokens=0,
            est_cost_usd=0.4568,
            dates_analyzed=["2026-07-13"],
        )

        # Second entry
        store.append_spend_ledger(
            run_at="2026-07-14T02:00:00Z",
            command="analyze --days 7",
            model="claude-sonnet-5",
            calls=2,
            input_tokens=10000,
            output_tokens=5000,
            cache_read_input_tokens=100,
            est_cost_usd=0.15,
            dates_analyzed=["2026-07-08", "2026-07-09"],
        )

        ledger_path = tmp_path / "spend" / "ledger" / "2026-07.json"
        with open(ledger_path, "r") as f:
            ledger = json.load(f)

        assert len(ledger) == 2
        assert ledger[0]["run_at"] == "2026-07-14T01:00:00Z"
        assert ledger[1]["run_at"] == "2026-07-14T02:00:00Z"

    def test_append_spend_ledger_handles_corrupt_file(self, tmp_path, monkeypatch, capsys):
        """Test that append_spend_ledger handles corrupt ledger files."""
        monkeypatch.setattr(store, "SPEND_LEDGER_DIR", tmp_path / "spend" / "ledger")

        # Create a corrupt ledger file
        ledger_path = tmp_path / "spend" / "ledger"
        ledger_path.mkdir(parents=True, exist_ok=True)
        corrupt_file = ledger_path / "2026-07.json"
        corrupt_file.write_text("{ invalid json }")

        # Append should handle it gracefully
        store.append_spend_ledger(
            run_at="2026-07-14T01:00:00Z",
            command="analyze --days 1",
            model="claude-opus-4-8",
            calls=1,
            input_tokens=1000,
            output_tokens=500,
            cache_read_input_tokens=0,
            est_cost_usd=0.05,
            dates_analyzed=["2026-07-13"],
        )

        # Check that a backup was created
        backup_file = ledger_path / "2026-07.json.bak"
        assert backup_file.exists()

        # Check that the new entry was written
        with open(corrupt_file, "r") as f:
            ledger = json.load(f)
        assert len(ledger) == 1
        assert ledger[0]["run_at"] == "2026-07-14T01:00:00Z"

        # Check warning was printed
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "Corrupt ledger" in captured.err

    def test_append_spend_ledger_multiple_months(self, tmp_path, monkeypatch):
        """Test that ledger entries for different months go to different files."""
        monkeypatch.setattr(store, "SPEND_LEDGER_DIR", tmp_path / "spend" / "ledger")

        # Entry in July
        store.append_spend_ledger(
            run_at="2026-07-14T01:00:00Z",
            command="analyze --days 1",
            model="claude-opus-4-8",
            calls=1,
            input_tokens=1000,
            output_tokens=500,
            cache_read_input_tokens=0,
            est_cost_usd=0.05,
            dates_analyzed=["2026-07-13"],
        )

        # Entry in August
        store.append_spend_ledger(
            run_at="2026-08-01T01:00:00Z",
            command="analyze --days 1",
            model="claude-opus-4-8",
            calls=1,
            input_tokens=1000,
            output_tokens=500,
            cache_read_input_tokens=0,
            est_cost_usd=0.05,
            dates_analyzed=["2026-07-31"],
        )

        july_path = tmp_path / "spend" / "ledger" / "2026-07.json"
        august_path = tmp_path / "spend" / "ledger" / "2026-08.json"

        assert july_path.exists()
        assert august_path.exists()

        with open(july_path, "r") as f:
            july_ledger = json.load(f)
        with open(august_path, "r") as f:
            august_ledger = json.load(f)

        assert len(july_ledger) == 1
        assert len(august_ledger) == 1

    def test_append_spend_ledger_null_cost_for_unknown_model(self, tmp_path, monkeypatch):
        """Test that est_cost_usd can be None for unknown models."""
        monkeypatch.setattr(store, "SPEND_LEDGER_DIR", tmp_path / "spend" / "ledger")

        store.append_spend_ledger(
            run_at="2026-07-14T01:00:00Z",
            command="analyze --days 1",
            model="unknown-model",
            calls=1,
            input_tokens=1000,
            output_tokens=500,
            cache_read_input_tokens=0,
            est_cost_usd=None,
            dates_analyzed=["2026-07-13"],
        )

        ledger_path = tmp_path / "spend" / "ledger" / "2026-07.json"
        with open(ledger_path, "r") as f:
            ledger = json.load(f)

        assert len(ledger) == 1
        assert ledger[0]["est_cost_usd"] is None

    def test_append_spend_ledger_questions_analyzed_optional(self, tmp_path, monkeypatch):
        """questions_analyzed is only written when explicitly passed (backward compat)."""
        monkeypatch.setattr(store, "SPEND_LEDGER_DIR", tmp_path / "spend" / "ledger")

        store.append_spend_ledger(
            run_at="2026-07-14T01:00:00Z",
            command="analyze --days 1",
            model="claude-opus-4-8",
            calls=1,
            input_tokens=1000,
            output_tokens=500,
            cache_read_input_tokens=0,
            est_cost_usd=0.05,
            dates_analyzed=["2026-07-13"],
            questions_analyzed=42,
        )
        store.append_spend_ledger(
            run_at="2026-07-14T02:00:00Z",
            command="analyze --days 1",
            model="claude-opus-4-8",
            calls=1,
            input_tokens=1000,
            output_tokens=500,
            cache_read_input_tokens=0,
            est_cost_usd=0.05,
            dates_analyzed=["2026-07-13"],
        )

        ledger_path = tmp_path / "spend" / "ledger" / "2026-07.json"
        with open(ledger_path, "r") as f:
            ledger = json.load(f)

        assert ledger[0]["questions_analyzed"] == 42
        assert "questions_analyzed" not in ledger[1]


class TestReadAllSpendLedger:
    def test_empty_dir_returns_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store, "SPEND_LEDGER_DIR", tmp_path / "spend" / "ledger")
        assert store.read_all_spend_ledger() == []

    def test_flattens_and_sorts_across_months(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store, "SPEND_LEDGER_DIR", tmp_path / "spend" / "ledger")
        store.append_spend_ledger(
            run_at="2026-08-01T00:00:00Z", command="analyze --days 1", model="claude-opus-4-8",
            calls=1, input_tokens=100, output_tokens=50, cache_read_input_tokens=0,
            est_cost_usd=0.01, dates_analyzed=["2026-07-31"],
        )
        store.append_spend_ledger(
            run_at="2026-07-14T00:00:00Z", command="analyze --days 1", model="claude-opus-4-8",
            calls=1, input_tokens=100, output_tokens=50, cache_read_input_tokens=0,
            est_cost_usd=0.01, dates_analyzed=["2026-07-13"],
        )

        entries = store.read_all_spend_ledger()
        assert len(entries) == 2
        assert entries[0]["run_at"] == "2026-07-14T00:00:00Z"
        assert entries[1]["run_at"] == "2026-08-01T00:00:00Z"


class TestBuildSpendModel:
    """Tests for src.spend.build_spend_model (pure builder + recommendations)."""

    def test_empty_entries_gives_null_ledger_and_no_recs(self):
        model = spend_module.build_spend_model(entries=[])
        assert model == {"ledger": None, "recommendations": []}

    def test_reads_from_disk_when_entries_is_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store, "SPEND_LEDGER_DIR", tmp_path / "spend" / "ledger")
        store.append_spend_ledger(
            run_at="2026-07-14T00:00:00Z", command="analyze --days 1", model="claude-opus-4-8",
            calls=1, input_tokens=1000, output_tokens=500, cache_read_input_tokens=0,
            est_cost_usd=0.05, dates_analyzed=["2026-07-13"],
        )
        model = spend_module.build_spend_model()
        assert model["ledger"] is not None
        assert model["ledger"]["total_calls"] == 1

    def test_ledger_summary_fields_and_by_day_aggregation(self):
        entries = [
            {
                "run_at": "2026-07-01T00:00:00Z", "command": "analyze --days 1",
                "model": "claude-opus-4-8", "calls": 1, "input_tokens": 1000,
                "output_tokens": 500, "cache_read_input_tokens": 0,
                "est_cost_usd": 0.02, "dates_analyzed": ["2026-06-30"],
                "questions_analyzed": 10,
            },
            {
                # same day as above -> by_day should sum
                "run_at": "2026-07-01T05:00:00Z", "command": "analyze --days 1",
                "model": "claude-opus-4-8", "calls": 1, "input_tokens": 1000,
                "output_tokens": 500, "cache_read_input_tokens": 0,
                "est_cost_usd": 0.03, "dates_analyzed": ["2026-07-01"],
                # no questions_analyzed -> excluded from cost_per_question denominator
            },
        ]
        model = spend_module.build_spend_model(entries)
        ledger = model["ledger"]
        assert ledger["since"] == "2026-07-01T00:00:00Z"
        assert round(ledger["total_usd"], 4) == 0.05
        assert ledger["total_calls"] == 2
        assert ledger["total_input_tokens"] == 2000
        assert ledger["total_output_tokens"] == 1000
        assert ledger["by_day"] == [{"date": "2026-07-01", "usd": 0.05}]
        # numerator = total_usd across ALL entries; denominator = questions_analyzed
        # summed only over entries that carry the field (honest-or-None design).
        assert round(ledger["cost_per_question"], 4) == round(0.05 / 10, 4)
        assert ledger["cost_per_candidate"] is None

    def test_cost_per_question_none_when_no_entry_carries_field(self):
        entries = [{
            "run_at": "2026-07-01T00:00:00Z", "command": "analyze --days 1",
            "model": "claude-opus-4-8", "calls": 1, "input_tokens": 1000,
            "output_tokens": 500, "cache_read_input_tokens": 0,
            "est_cost_usd": 0.02, "dates_analyzed": ["2026-06-30"],
        }]
        model = spend_module.build_spend_model(entries)
        assert model["ledger"]["cost_per_question"] is None

    def test_per_run_recent_capped_at_10_newest_first(self):
        entries = [
            {
                "run_at": f"2026-07-{i:02d}T00:00:00Z", "command": "analyze --days 1",
                "model": "claude-opus-4-8", "calls": 1, "input_tokens": 100,
                "output_tokens": 50, "cache_read_input_tokens": 0,
                "est_cost_usd": 0.01, "dates_analyzed": [f"2026-07-{i:02d}"],
            }
            for i in range(1, 13)
        ]
        model = spend_module.build_spend_model(entries)
        recent = model["ledger"]["per_run_recent"]
        assert len(recent) == 10
        assert recent[0]["run_at"] == "2026-07-12T00:00:00Z"
        assert recent[-1]["run_at"] == "2026-07-03T00:00:00Z"


class TestRecommendations:
    """Tests for the C4 recommendations rules in src.spend."""

    def _big_entry(self, run_at, dates_analyzed):
        return {
            "run_at": run_at, "command": "analyze --days 1", "model": "claude-opus-4-8",
            "calls": 2, "input_tokens": 1_000_000, "output_tokens": 200_000,
            "cache_read_input_tokens": 0, "est_cost_usd": 10.0,
            "dates_analyzed": dates_analyzed, "questions_analyzed": 40,
        }

    def test_all_four_recs_emitted_for_large_overlapping_ledger(self):
        entries = [
            self._big_entry("2026-07-10T00:00:00Z", ["2026-07-13"]),
            self._big_entry("2026-07-14T00:00:00Z", ["2026-07-13"]),  # re-run of same date
        ]
        model = spend_module.build_spend_model(entries)
        recs = {r["kind"]: r for r in model["recommendations"]}

        assert "batch_api" in recs
        assert recs["batch_api"]["est_monthly_savings_usd"] == 10.0

        assert "prompt_caching" in recs
        assert recs["prompt_caching"]["est_monthly_savings_usd"] == round(0.9 * 0.6 * 10.0, 4)
        assert recs["prompt_caching"]["label"] == "estimate"

        assert "model_mix" in recs
        assert recs["model_mix"]["table"][0]["model"] == "claude-haiku-4-5"
        assert recs["model_mix"]["est_monthly_savings_usd"] == round(20.0 - 4.0, 4)

        assert "rerun_overlap" in recs
        assert recs["rerun_overlap"]["est_monthly_savings_usd"] == 10.0
        assert "1 date(s)" in recs["rerun_overlap"]["title"]

    def test_no_recs_below_threshold(self):
        entries = [{
            "run_at": "2026-07-01T00:00:00Z", "command": "analyze --days 1",
            "model": "claude-haiku-4-5", "calls": 1, "input_tokens": 1000,
            "output_tokens": 200, "cache_read_input_tokens": 0,
            "est_cost_usd": 0.002, "dates_analyzed": ["2026-06-30"],
        }]
        model = spend_module.build_spend_model(entries)
        assert model["recommendations"] == []

    def test_prompt_caching_suppressed_when_cache_reads_present(self):
        entries = [
            self._big_entry("2026-07-10T00:00:00Z", ["2026-07-13"]),
            dict(self._big_entry("2026-07-11T00:00:00Z", ["2026-07-14"]), cache_read_input_tokens=500),
        ]
        model = spend_module.build_spend_model(entries)
        kinds = {r["kind"] for r in model["recommendations"]}
        assert "prompt_caching" not in kinds

    def test_no_rerun_overlap_when_dates_distinct(self):
        entries = [
            self._big_entry("2026-07-10T00:00:00Z", ["2026-07-10"]),
            self._big_entry("2026-07-11T00:00:00Z", ["2026-07-11"]),
        ]
        model = spend_module.build_spend_model(entries)
        kinds = {r["kind"] for r in model["recommendations"]}
        assert "rerun_overlap" not in kinds

    def test_recommendations_empty_when_ledger_empty(self):
        model = spend_module.build_spend_model(entries=[])
        assert model["recommendations"] == []
