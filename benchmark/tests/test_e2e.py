"""End-to-end integration test with mocked LLM + mocked leaderboard."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from pm_benchmark.config import LLMConfig, LeaderboardConfig, RunConfig
from pm_benchmark.export import save_huggingface_jsonl
from pm_benchmark.market_info import GAMMA_BASE
from pm_benchmark.report import save_reports
from pm_benchmark.runner import Runner
from pm_benchmark.scoring import score_run


# Sample Gamma API responses for mini market set
MOCK_MARKETS = {
    "will-leverkusen-win-the-202526-champions-league": {
        "slug": "will-leverkusen-win-the-202526-champions-league",
        "question": "Will Leverkusen win the 2025-26 Champions League?",
        "description": "Resolves Yes if Leverkusen wins.",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.08", "0.92"]',
        "volume": "9975249",
        "liquidity": "250000",
        "endDate": "2026-05-31T23:59:59Z",
        "active": True,
        "closed": False,
    },
    "will-china-invade-taiwan-before-2027": {
        "slug": "will-china-invade-taiwan-before-2027",
        "question": "Will China invade Taiwan by end of 2026?",
        "description": "Resolves Yes if invasion occurs.",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.05", "0.95"]',
        "volume": "9969547",
        "liquidity": "150000",
        "endDate": "2026-12-31T00:00:00Z",
        "active": True,
        "closed": False,
    },
    "will-russia-enter-kramatorsk-by-june-30-821-192": {
        "slug": "will-russia-enter-kramatorsk-by-june-30-821-192",
        "question": "Will Russia enter Kramatorsk by June 30?",
        "description": "Resolves Yes if Russian forces enter Kramatorsk.",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.15", "0.85"]',
        "volume": "99785",
        "liquidity": "100000",
        "endDate": "2026-06-30T00:00:00Z",
        "active": True,
        "closed": False,
    },
}


def _mock_llm_response(probability: float = 0.65, action: str = "buy_yes") -> str:
    return json.dumps({
        "probability": probability,
        "action": action,
        "confidence": "medium",
        "amount_usd": 200.0,
        "reasoning": "Analysis based on current data.",
    })


class TestE2EWithMockedAgent:
    """Full pipeline: market fetch → LLM → trade → score → report."""

    @respx.mock
    def test_full_pipeline(self, tmp_path):
        # 1. Mock Gamma API for all mini markets
        for slug, data in MOCK_MARKETS.items():
            respx.get(GAMMA_BASE + "/markets", params={"slug": slug}).mock(
                return_value=httpx.Response(200, json=[data])
            )

        # 2. Mock LLM calls
        call_count = 0
        responses = [
            _mock_llm_response(0.75, "buy_yes"),
            _mock_llm_response(0.40, "buy_no"),
            _mock_llm_response(0.50, "skip"),
        ]

        def mock_query(llm_config, prompt, system_prompt):
            nonlocal call_count
            resp = responses[call_count % len(responses)]
            call_count += 1
            return resp

        # 3. Mock Agent
        agent = MagicMock()
        agent.balance.return_value = {"cash": 10_000.0}
        agent.portfolio.return_value = []
        agent.buy.return_value = {"shares": 100, "avg_price": 0.65, "cost": 200.0}
        agent.stats.return_value = {
            "roi_pct": 3.5,
            "sharpe_ratio": 0.8,
            "win_rate": 0.5,
            "max_drawdown": 0.02,
            "total_trades": 2,
        }

        # 4. Configure and run
        config = RunConfig(
            llm=LLMConfig(model="claude-opus-4"),
            leaderboard=LeaderboardConfig(base_url="http://localhost:8000"),
            market_set="mini",
            budget=10_000.0,
            output_dir=tmp_path / "output",
        )

        runner = Runner(config, agent=agent, model_caller=mock_query)
        eval_run = runner.run()

        # 5. Verify run results
        assert eval_run.model == "claude-opus-4"
        assert eval_run.market_set == "mini"
        assert len(eval_run.market_results) == 3
        assert eval_run.completed_count == 3
        assert eval_run.error_count == 0
        assert eval_run.total_latency_seconds > 0

        # Verify individual market results
        r0 = eval_run.market_results[0]
        assert r0.model_probability == 0.75
        assert r0.action == "buy_yes"

        r1 = eval_run.market_results[1]
        assert r1.action == "buy_no"

        r2 = eval_run.market_results[2]
        assert r2.action == "skip"
        assert r2.trade_result is None  # no trade for skip

        # Verify agent calls
        assert agent.buy.call_count == 2  # only 2 buys, 1 skip
        agent.buy.assert_any_call(
            "will-leverkusen-win-the-202526-champions-league", "yes", 200.0
        )
        agent.buy.assert_any_call(
            "will-china-invade-taiwan-before-2027", "no", 200.0
        )

        # 6. Score the run
        scores = score_run(eval_run)
        assert 0 <= scores.composite_score <= 100
        assert scores.brier_score >= 0
        assert scores.roi_pct == 3.5
        assert scores.sharpe_ratio == 0.8

        # 7. Generate reports
        paths = save_reports(eval_run, scores, config.output_dir)
        assert paths["json"].exists()
        assert paths["markdown"].exists()

        # Verify JSON report is valid and complete
        report = json.loads(paths["json"].read_text())
        assert report["model"] == "claude-opus-4"
        assert len(report["market_results"]) == 3
        assert report["scores"]["composite_score"] == scores.composite_score

        # Verify Markdown report
        md = paths["markdown"].read_text()
        assert "# Benchmark Report: claude-opus-4" in md
        assert "will-leverkusen-win-the-202526-champions-league" in md

        # 8. Export to HuggingFace JSONL
        jsonl_path = tmp_path / "output" / "results.jsonl"
        save_huggingface_jsonl(eval_run, scores, jsonl_path)
        assert jsonl_path.exists()

        lines = [l for l in jsonl_path.read_text().strip().split("\n") if l]
        assert len(lines) == 4  # 3 market results + 1 summary
        summary = json.loads(lines[-1])
        assert summary["type"] == "summary"
        assert summary["composite_score"] == scores.composite_score

    @respx.mock
    def test_pipeline_with_closed_markets(self, tmp_path):
        """Markets that are closed should be skipped."""
        closed_market = MOCK_MARKETS["will-leverkusen-win-the-202526-champions-league"].copy()
        closed_market["closed"] = True

        for slug in MOCK_MARKETS:
            respx.get(GAMMA_BASE + "/markets", params={"slug": slug}).mock(
                return_value=httpx.Response(200, json=[closed_market])
            )

        config = RunConfig(
            llm=LLMConfig(model="test"),
            market_set="mini",
            output_dir=tmp_path / "out",
        )

        runner = Runner(config, model_caller=MagicMock())
        eval_run = runner.run()

        assert eval_run.skipped_count == 3
        assert eval_run.completed_count == 0

    @respx.mock
    def test_pipeline_with_api_errors(self, tmp_path):
        """API errors should be recorded, not crash the run."""
        for slug in MOCK_MARKETS:
            respx.get(GAMMA_BASE + "/markets", params={"slug": slug}).mock(
                return_value=httpx.Response(500, text="Server Error")
            )

        config = RunConfig(
            llm=LLMConfig(model="test"),
            market_set="mini",
            output_dir=tmp_path / "out",
        )

        runner = Runner(config, model_caller=MagicMock())
        eval_run = runner.run()

        assert eval_run.error_count == 3
        assert eval_run.completed_count == 0

    @respx.mock
    def test_score_with_resolved_outcomes(self, tmp_path):
        """Scoring with known outcomes produces accurate Brier scores."""
        for slug, data in MOCK_MARKETS.items():
            respx.get(GAMMA_BASE + "/markets", params={"slug": slug}).mock(
                return_value=httpx.Response(200, json=[data])
            )

        def mock_query(llm_config, prompt, system_prompt):
            return _mock_llm_response(0.80, "buy_yes")

        config = RunConfig(
            llm=LLMConfig(model="test"),
            market_set="mini",
            output_dir=tmp_path / "out",
        )

        runner = Runner(config, model_caller=mock_query)
        eval_run = runner.run()

        # Score with known outcomes
        resolved = {
            "will-leverkusen-win-the-202526-champions-league": 1.0,  # Yes
            "will-china-invade-taiwan-before-2027": 0.0,  # No
            "will-russia-enter-kramatorsk-by-june-30-821-192": 0.0,  # No
        }
        scores = score_run(eval_run, resolved_outcomes=resolved)

        # Brier: ((0.8-1)^2 + (0.8-0)^2 + (0.8-0)^2) / 3
        # = (0.04 + 0.64 + 0.64) / 3 = 0.44
        assert scores.brier_score == pytest.approx(0.44, abs=0.01)
