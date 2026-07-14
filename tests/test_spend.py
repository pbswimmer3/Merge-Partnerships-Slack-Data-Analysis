"""Tests for spend tracking: cost estimation and ledger management."""
import json
from pathlib import Path

from src.config import estimate_cost_usd
from src import store


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
