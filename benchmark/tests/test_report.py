"""Tests for pm_benchmark.report."""
from __future__ import annotations

import json

import pytest

from pm_benchmark.runner import EvalRun, MarketResult
from pm_benchmark.scoring import BenchmarkScores
from pm_benchmark.report import (
    generate_json_report,
    generate_markdown_report,
    save_reports,
)


def _make_eval_run() -> EvalRun:
    run = EvalRun(
        model="claude-opus-4",
        market_set="mini",
        budget=10_000.0,
        timestamp="2025-03-01T00:00:00Z",
        total_latency_seconds=12.5,
    )
    run.market_results = [
        MarketResult(
            slug="market-1",
            question="Will X?",
            model_probability=0.7,
            action="buy_yes",
            confidence="high",
            amount_usd=250.0,
            reasoning="Strong signal",
            latency_seconds=3.5,
            market_price_yes=0.65,
        ),
        MarketResult(
            slug="market-2",
            question="Will Y?",
            error="API timeout",
            latency_seconds=10.0,
        ),
        MarketResult(
            slug="market-3",
            question="Will Z?",
            skipped_reason="Market closed",
            latency_seconds=0.1,
        ),
    ]
    return run


def _make_scores() -> BenchmarkScores:
    return BenchmarkScores(
        brier_score=0.18,
        calibration_error=0.05,
        composite_score=72.5,
        roi_pct=8.5,
        sharpe_ratio=1.1,
        win_rate=0.65,
        max_drawdown=0.03,
        skip_rate=0.333,
        alpha_score=-0.02,
        details={"n_predictions": 1, "n_total": 3},
    )


class TestGenerateJsonReport:
    def test_valid_json(self):
        report = generate_json_report(_make_eval_run(), _make_scores())
        data = json.loads(report)
        assert data["model"] == "claude-opus-4"
        assert data["scores"]["composite_score"] == 72.5
        assert len(data["market_results"]) == 3

    def test_summary(self):
        report = generate_json_report(_make_eval_run(), _make_scores())
        data = json.loads(report)
        assert data["summary"]["completed"] == 1
        assert data["summary"]["errors"] == 1
        assert data["summary"]["skipped"] == 1


class TestGenerateMarkdownReport:
    def test_contains_headers(self):
        md = generate_markdown_report(_make_eval_run(), _make_scores())
        assert "# Benchmark Report: claude-opus-4" in md
        assert "## Scores" in md
        assert "## Summary" in md
        assert "## Market Details" in md
        assert "## Scoring Methodology" in md

    def test_contains_scores(self):
        md = generate_markdown_report(_make_eval_run(), _make_scores())
        assert "72.5" in md
        assert "0.18" in md
        assert "8.5%" in md
        assert "Alpha Score" in md
        assert "-0.02" in md

    def test_contains_market_rows(self):
        md = generate_markdown_report(_make_eval_run(), _make_scores())
        assert "market-1" in md
        assert "market-2" in md
        assert "market-3" in md
        assert "Error:" in md
        assert "Skipped:" in md

    def test_contains_market_price_column(self):
        md = generate_markdown_report(_make_eval_run(), _make_scores())
        assert "Market Price" in md
        # market-1 has market_price_yes=0.65
        lines = md.split("\n")
        market1_line = [l for l in lines if "market-1" in l][0]
        assert "0.65" in market1_line

    def test_probability_dash_for_none(self):
        md = generate_markdown_report(_make_eval_run(), _make_scores())
        # market-2 has no probability and no market price
        lines = md.split("\n")
        market2_line = [l for l in lines if "market-2" in l][0]
        assert "| - |" in market2_line


class TestSaveReports:
    def test_creates_files(self, tmp_path):
        out_dir = tmp_path / "output"
        paths = save_reports(_make_eval_run(), _make_scores(), out_dir)

        assert paths["json"].exists()
        assert paths["markdown"].exists()

        data = json.loads(paths["json"].read_text())
        assert data["model"] == "claude-opus-4"

        md = paths["markdown"].read_text()
        assert "# Benchmark Report" in md

    def test_creates_directory(self, tmp_path):
        out_dir = tmp_path / "nested" / "output"
        save_reports(_make_eval_run(), _make_scores(), out_dir)
        assert out_dir.exists()
