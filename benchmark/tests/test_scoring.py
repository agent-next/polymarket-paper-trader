"""Tests for pm_benchmark.scoring."""
from __future__ import annotations

import pytest

from pm_benchmark.runner import EvalRun, MarketResult, RoundResult
from pm_benchmark.scoring import (
    SCORING_METHODOLOGY,
    BenchmarkScores,
    _WEIGHTS_FULL,
    _WEIGHTS_LLM_ONLY,
    _sigmoid,
    compute_alpha_score,
    compute_brier_score,
    compute_calibration_error,
    compute_composite_score,
    score_run,
)


class TestScoringMethodology:
    def test_weights_full_sum(self):
        assert abs(sum(_WEIGHTS_FULL.values()) - 1.0) < 1e-9

    def test_weights_llm_only_sum(self):
        assert abs(sum(_WEIGHTS_LLM_ONLY.values()) - 1.0) < 1e-9

    def test_methodology_string(self):
        assert "Brier Score" in SCORING_METHODOLOGY
        assert "Alpha Score" in SCORING_METHODOLOGY
        assert "Composite Score" in SCORING_METHODOLOGY


class TestBrierScore:
    def test_perfect_predictions(self):
        preds = [(1.0, 1.0), (0.0, 0.0)]
        assert compute_brier_score(preds) == 0.0

    def test_worst_predictions(self):
        preds = [(1.0, 0.0), (0.0, 1.0)]
        assert compute_brier_score(preds) == 1.0

    def test_moderate_predictions(self):
        preds = [(0.7, 1.0), (0.3, 0.0)]
        expected = ((0.3**2) + (0.3**2)) / 2
        assert compute_brier_score(preds) == pytest.approx(expected)

    def test_empty(self):
        assert compute_brier_score([]) == 1.0

    def test_single(self):
        assert compute_brier_score([(0.5, 1.0)]) == pytest.approx(0.25)


class TestCalibrationError:
    def test_perfectly_calibrated(self):
        preds = [(0.5, 0.5)] * 10
        assert compute_calibration_error(preds) == pytest.approx(0.0)

    def test_empty(self):
        assert compute_calibration_error([]) == 1.0

    def test_overconfident(self):
        # Predict 0.9 but actual is 0.5 — should have nonzero ECE
        preds = [(0.9, 0.5)] * 10
        ece = compute_calibration_error(preds)
        assert ece > 0

    def test_n_bins(self):
        preds = [(0.5, 1.0)]
        # With 5 bins, prediction 0.5 goes to bin 2
        ece = compute_calibration_error(preds, n_bins=5)
        assert ece == pytest.approx(0.5)


class TestCompositeScore:
    def test_perfect_agent_mode(self):
        score = compute_composite_score(
            roi_pct=100, brier=0.0, sharpe=3.0, win_rate=1.0,
            calibration=0.0, max_drawdown=0.0, skip_rate=0.0,
            alpha=-1.0, agent_mode=True,
        )
        assert score > 90

    def test_terrible_agent_mode(self):
        score = compute_composite_score(
            roi_pct=-100, brier=1.0, sharpe=-3.0, win_rate=0.0,
            calibration=1.0, max_drawdown=1.0, skip_rate=1.0,
            alpha=1.0, agent_mode=True,
        )
        assert score < 10

    def test_llm_only_good_predictions(self):
        """LLM-only mode with good predictions gives reasonable score."""
        score = compute_composite_score(
            roi_pct=0, brier=0.05, sharpe=0, win_rate=0,
            calibration=0.05, max_drawdown=0, skip_rate=0.1,
            alpha=-0.5, agent_mode=False,
        )
        assert 60 < score < 90

    def test_llm_only_ignores_trade_metrics(self):
        """LLM-only: trade metrics don't affect score."""
        base = dict(roi_pct=0, brier=0.1, sharpe=0, win_rate=0,
                    calibration=0.1, max_drawdown=0, skip_rate=0.1,
                    alpha=0.0, agent_mode=False)
        score_a = compute_composite_score(**base)
        score_b = compute_composite_score(**{**base, "roi_pct": 100, "sharpe": 5.0, "win_rate": 1.0})
        assert score_a == score_b

    def test_average(self):
        score = compute_composite_score(
            roi_pct=0, brier=0.25, sharpe=0.0, win_rate=0.5,
            calibration=0.1, max_drawdown=0.1, skip_rate=0.1,
        )
        assert 30 < score < 80

    def test_bounds(self):
        score = compute_composite_score(
            roi_pct=0, brier=0.5, sharpe=0, win_rate=0.5,
            calibration=0.5, max_drawdown=0.5, skip_rate=0.5,
        )
        assert 0 <= score <= 100


class TestSigmoid:
    def test_center(self):
        assert _sigmoid(0, center=0, scale=1) == pytest.approx(0.5)

    def test_positive(self):
        assert _sigmoid(10, center=0, scale=1) > 0.99

    def test_negative(self):
        assert _sigmoid(-10, center=0, scale=1) < 0.01

    def test_scale_zero(self):
        assert _sigmoid(5, center=0, scale=0) == pytest.approx(0.5)

    def test_large_value(self):
        # Should not overflow
        assert _sigmoid(1000, center=0, scale=1) == pytest.approx(1.0)


class TestAlphaScore:
    def test_model_beats_market(self):
        """Negative alpha = model beats market."""
        # model=0.8, market=0.5, actual=1.0
        # model_brier = (0.8-1)^2 = 0.04, market_brier = (0.5-1)^2 = 0.25
        # alpha = 0.04 - 0.25 = -0.21
        triples = [(0.8, 0.5, 1.0)]
        assert compute_alpha_score(triples) == pytest.approx(-0.21)

    def test_market_beats_model(self):
        """Positive alpha = market beats model."""
        # model=0.2, market=0.8, actual=1.0
        # model_brier = (0.2-1)^2 = 0.64, market_brier = (0.8-1)^2 = 0.04
        # alpha = 0.64 - 0.04 = 0.60
        triples = [(0.2, 0.8, 1.0)]
        assert compute_alpha_score(triples) == pytest.approx(0.60)

    def test_empty_returns_zero(self):
        assert compute_alpha_score([]) == 0.0

    def test_proxy_mode(self):
        """In proxy mode actual=market, so market_brier=0, alpha=divergence^2."""
        # model=0.7, market=0.5, actual=0.5 (proxy)
        # model_brier = (0.7-0.5)^2 = 0.04, market_brier = (0.5-0.5)^2 = 0
        # alpha = 0.04
        triples = [(0.7, 0.5, 0.5)]
        assert compute_alpha_score(triples) == pytest.approx(0.04)

    def test_multiple_markets(self):
        """Alpha averages across markets."""
        triples = [
            (0.8, 0.5, 1.0),  # model_b=0.04, market_b=0.25
            (0.3, 0.6, 0.0),  # model_b=0.09, market_b=0.36
        ]
        # avg model_brier = (0.04+0.09)/2 = 0.065
        # avg market_brier = (0.25+0.36)/2 = 0.305
        # alpha = 0.065 - 0.305 = -0.24
        assert compute_alpha_score(triples) == pytest.approx(-0.24)


class TestScoreRun:
    def _make_run(
        self,
        n_completed: int = 3,
        n_errors: int = 0,
        n_skipped: int = 0,
        agent_stats: dict | None = None,
    ) -> EvalRun:
        run = EvalRun(model="test", market_set="mini", budget=10000)
        for i in range(n_completed):
            run.market_results.append(
                MarketResult(
                    slug=f"market-{i}",
                    question=f"Q{i}",
                    model_probability=0.6,
                    action="buy_yes",
                    amount_usd=250.0,
                )
            )
        for i in range(n_errors):
            run.market_results.append(
                MarketResult(slug=f"err-{i}", question="E", error="fail")
            )
        for i in range(n_skipped):
            run.market_results.append(
                MarketResult(slug=f"skip-{i}", question="S", skipped_reason="closed")
            )
        run.agent_stats = agent_stats or {}
        return run

    def test_basic_scoring(self):
        run = self._make_run(n_completed=3)
        scores = score_run(run)
        assert isinstance(scores, BenchmarkScores)
        assert 0 <= scores.composite_score <= 100
        assert scores.details["n_predictions"] == 3

    def test_with_resolved_outcomes(self):
        run = self._make_run(n_completed=2)
        resolved = {
            "market-0": 1.0,  # Yes
            "market-1": 0.0,  # No
        }
        scores = score_run(run, resolved_outcomes=resolved)
        # Brier: ((0.6-1)^2 + (0.6-0)^2) / 2 = (0.16 + 0.36) / 2 = 0.26
        assert scores.brier_score == pytest.approx(0.26, abs=0.01)

    def test_with_agent_stats(self):
        stats = {"roi_pct": 15.0, "sharpe_ratio": 1.2, "win_rate": 0.6, "max_drawdown": 0.05}
        run = self._make_run(agent_stats=stats)
        scores = score_run(run)
        assert scores.roi_pct == 15.0
        assert scores.sharpe_ratio == 1.2
        assert scores.win_rate == 0.6
        assert scores.max_drawdown == 0.05

    def test_skip_rate(self):
        run = self._make_run(n_completed=1, n_skipped=2)
        scores = score_run(run)
        assert scores.skip_rate == pytest.approx(2 / 3, abs=0.01)

    def test_proxy_brier_uses_market_price(self):
        """Proxy Brier uses market_price_yes instead of hardcoded 0.5."""
        run = EvalRun(model="test", market_set="mini", budget=10000)
        run.market_results = [
            MarketResult(
                slug="m1", question="Q?",
                model_probability=0.60,
                market_price_yes=0.65,
            ),
        ]
        scores = score_run(run)
        # Brier = (0.60 - 0.65)^2 = 0.0025
        assert scores.brier_score == pytest.approx(0.0025, abs=0.0001)

    def test_proxy_brier_fallback_no_price(self):
        """Falls back to 0.5 when market_price_yes is None."""
        run = EvalRun(model="test", market_set="mini", budget=10000)
        run.market_results = [
            MarketResult(
                slug="m1", question="Q?",
                model_probability=0.60,
                market_price_yes=None,
            ),
        ]
        scores = score_run(run)
        # Brier = (0.60 - 0.50)^2 = 0.01
        assert scores.brier_score == pytest.approx(0.01, abs=0.001)

    def test_alpha_in_score_run(self):
        """score_run computes alpha_score."""
        run = EvalRun(model="test", market_set="mini", budget=10000)
        run.market_results = [
            MarketResult(slug="m1", question="Q?", model_probability=0.80, market_price_yes=0.65),
        ]
        resolved = {"m1": 1.0}
        scores = score_run(run, resolved_outcomes=resolved)
        # model_brier = (0.80-1)^2 = 0.04, market_brier = (0.65-1)^2 = 0.1225
        # alpha = 0.04 - 0.1225 = -0.0825
        assert scores.alpha_score == pytest.approx(-0.0825, abs=0.001)

    def test_per_round_scoring(self):
        """Multi-round run includes per-round Brier in details."""
        run = EvalRun(model="test", market_set="mini", budget=10000)
        r1 = MarketResult(slug="m1", question="Q?", model_probability=0.80, market_price_yes=0.65)
        r2 = MarketResult(slug="m1", question="Q?", model_probability=0.70, market_price_yes=0.65)
        run.market_results = [r1, r2]
        run.rounds = [
            RoundResult(round_number=1, market_results=[r1]),
            RoundResult(round_number=2, market_results=[r2]),
        ]
        scores = score_run(run)
        assert "per_round" in scores.details
        assert len(scores.details["per_round"]) == 2
        assert scores.details["per_round"][0]["round"] == 1
        # Round 1: (0.80-0.65)^2 = 0.0225
        assert scores.details["per_round"][0]["brier_score"] == pytest.approx(0.0225, abs=0.001)

    def test_per_round_with_resolved(self):
        """Per-round scoring uses resolved outcomes when available."""
        run = EvalRun(model="test", market_set="mini", budget=10000)
        r1 = MarketResult(slug="m1", question="Q?", model_probability=0.80, market_price_yes=0.65)
        r2 = MarketResult(slug="m1", question="Q?", model_probability=0.70, market_price_yes=0.65)
        run.market_results = [r1, r2]
        run.rounds = [
            RoundResult(round_number=1, market_results=[r1]),
            RoundResult(round_number=2, market_results=[r2]),
        ]
        scores = score_run(run, resolved_outcomes={"m1": 1.0})
        # Round 1: (0.80-1.0)^2 = 0.04
        assert scores.details["per_round"][0]["brier_score"] == pytest.approx(0.04, abs=0.001)

    def test_per_round_skips_none_probability(self):
        """Per-round scoring skips results with None probability."""
        run = EvalRun(model="test", market_set="mini", budget=10000)
        r1 = MarketResult(slug="m1", question="Q?", model_probability=0.70, market_price_yes=0.65)
        r_err = MarketResult(slug="m2", question="Q?", error="fail")  # no probability
        run.market_results = [r1, r_err]
        run.rounds = [
            RoundResult(round_number=1, market_results=[r1]),
            RoundResult(round_number=2, market_results=[r_err]),
        ]
        scores = score_run(run)
        assert scores.details["per_round"][1]["n_predictions"] == 0

    def test_per_round_fallback_no_price(self):
        """Per-round scoring falls back to 0.5 when no market price."""
        run = EvalRun(model="test", market_set="mini", budget=10000)
        r1 = MarketResult(slug="m1", question="Q?", model_probability=0.60, market_price_yes=None)
        r2 = MarketResult(slug="m1", question="Q?", model_probability=0.60, market_price_yes=None)
        run.market_results = [r1, r2]
        run.rounds = [
            RoundResult(round_number=1, market_results=[r1]),
            RoundResult(round_number=2, market_results=[r2]),
        ]
        scores = score_run(run)
        # Brier = (0.60-0.50)^2 = 0.01
        assert scores.details["per_round"][0]["brier_score"] == pytest.approx(0.01, abs=0.001)

    def test_single_round_no_per_round(self):
        """Single-round run does NOT include per_round in details."""
        run = self._make_run(n_completed=3)
        scores = score_run(run)
        assert "per_round" not in scores.details

    def test_all_errors(self):
        run = self._make_run(n_completed=0, n_errors=3)
        scores = score_run(run)
        # No predictions → worst Brier
        assert scores.brier_score == 1.0
        assert scores.details["n_errors"] == 3
