"""Tests for pm_benchmark.export."""
from __future__ import annotations

import json

import pytest

from pm_benchmark.runner import EvalRun, MarketResult
from pm_benchmark.scoring import BenchmarkScores
from pm_benchmark.export import generate_huggingface_jsonl, save_huggingface_jsonl


def _make_eval_run() -> EvalRun:
    run = EvalRun(
        model="test-model",
        market_set="mini",
        budget=10_000.0,
        timestamp="2025-03-01T00:00:00Z",
    )
    run.market_results = [
        MarketResult(
            slug="m1",
            question="Q1?",
            model_probability=0.7,
            action="buy_yes",
            confidence="high",
            amount_usd=250.0,
            reasoning="Good signal",
            latency_seconds=2.5,
            market_price_yes=0.65,
        ),
        MarketResult(
            slug="m2",
            question="Q2?",
            error="timeout",
        ),
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
    )


class TestGenerateHuggingfaceJsonl:
    def test_line_count(self):
        jsonl = generate_huggingface_jsonl(_make_eval_run(), _make_scores())
        lines = [l for l in jsonl.strip().split("\n") if l]
        assert len(lines) == 3  # 2 market results + 1 summary

    def test_each_line_valid_json(self):
        jsonl = generate_huggingface_jsonl(_make_eval_run(), _make_scores())
        for line in jsonl.strip().split("\n"):
            if line:
                data = json.loads(line)
                assert "type" in data

    def test_market_result_fields(self):
        jsonl = generate_huggingface_jsonl(_make_eval_run(), _make_scores())
        first = json.loads(jsonl.strip().split("\n")[0])
        assert first["type"] == "market_result"
        assert first["slug"] == "m1"
        assert first["model_probability"] == 0.7
        assert first["action"] == "buy_yes"
        assert first["market_price_yes"] == 0.65

    def test_summary_fields(self):
        jsonl = generate_huggingface_jsonl(_make_eval_run(), _make_scores())
        last = json.loads(jsonl.strip().split("\n")[-1])
        assert last["type"] == "summary"
        assert last["composite_score"] == 65.0
        assert last["total_markets"] == 2
        assert last["completed"] == 1
        assert last["errors"] == 1

    def test_trailing_newline(self):
        jsonl = generate_huggingface_jsonl(_make_eval_run(), _make_scores())
        assert jsonl.endswith("\n")


class TestSaveHuggingfaceJsonl:
    def test_creates_file(self, tmp_path):
        path = tmp_path / "results.jsonl"
        result = save_huggingface_jsonl(_make_eval_run(), _make_scores(), path)
        assert result == path
        assert path.exists()
        content = path.read_text()
        assert "market_result" in content

    def test_creates_parent_dir(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "results.jsonl"
        save_huggingface_jsonl(_make_eval_run(), _make_scores(), path)
        assert path.exists()
