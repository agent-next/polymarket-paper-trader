"""Tests for pm_benchmark.history."""
from __future__ import annotations

import json

import pytest

from pm_benchmark.history import append_to_history, load_history
from pm_benchmark.runner import EvalRun, MarketResult
from pm_benchmark.scoring import BenchmarkScores


def _make_eval_run() -> EvalRun:
    run = EvalRun(
        model="test-model",
        market_set="mini",
        budget=10_000.0,
        timestamp="2025-03-01T00:00:00Z",
    )
    run.market_results = [
        MarketResult(slug="m1", question="Q?", model_probability=0.6),
    ]
    return run


def _make_scores() -> BenchmarkScores:
    return BenchmarkScores(
        brier_score=0.2,
        calibration_error=0.05,
        composite_score=65.0,
        roi_pct=5.0,
        sharpe_ratio=0.8,
        win_rate=0.6,
        max_drawdown=0.02,
        skip_rate=0.0,
        alpha_score=-0.01,
    )


class TestAppendToHistory:
    def test_creates_file(self, tmp_path):
        path = tmp_path / "history.jsonl"
        append_to_history(_make_eval_run(), _make_scores(), path)
        assert path.exists()
        content = path.read_text().strip()
        data = json.loads(content)
        assert data["model"] == "test-model"
        assert data["scores"]["composite_score"] == 65.0

    def test_appends_multiple(self, tmp_path):
        path = tmp_path / "history.jsonl"
        append_to_history(_make_eval_run(), _make_scores(), path)
        append_to_history(_make_eval_run(), _make_scores(), path)
        lines = [l for l in path.read_text().strip().split("\n") if l]
        assert len(lines) == 2

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "history.jsonl"
        append_to_history(_make_eval_run(), _make_scores(), path)
        assert path.exists()


class TestLoadHistory:
    def test_load_entries(self, tmp_path):
        path = tmp_path / "history.jsonl"
        append_to_history(_make_eval_run(), _make_scores(), path)
        append_to_history(_make_eval_run(), _make_scores(), path)
        entries = load_history(path)
        assert len(entries) == 2
        assert entries[0]["model"] == "test-model"

    def test_empty_file(self, tmp_path):
        path = tmp_path / "history.jsonl"
        path.write_text("")
        entries = load_history(path)
        assert entries == []

    def test_nonexistent_file(self, tmp_path):
        path = tmp_path / "nonexistent.jsonl"
        entries = load_history(path)
        assert entries == []

    def test_roundtrip(self, tmp_path):
        path = tmp_path / "history.jsonl"
        run = _make_eval_run()
        scores = _make_scores()
        append_to_history(run, scores, path)
        entries = load_history(path)
        assert len(entries) == 1
        assert entries[0]["n_markets"] == 1
        assert entries[0]["completed"] == 1
        assert entries[0]["scores"]["alpha_score"] == -0.01
